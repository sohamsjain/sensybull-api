# services/api/app/services/company_loader.py
"""
The company universe, from Financial Modeling Prep.

`sync_companies()` (daily cron, `flask sync-companies`) makes the company
table match the US-listed common stocks FMP knows: NYSE, NASDAQ and AMEX
(`COMPANY_EXCHANGES`), actively trading, not ETFs or funds, and not the
non-common lines listed next to them (warrants, units, rights, preferreds,
notes). It replaced the SEC's company_tickers.json in Sept 2026 — that
file lists every SEC filer with a symbol, OTC shells and non-traded
vehicles included, and its tickers drift from the ones FMP (our price and
fundamentals vendor) uses, which sent readers to "No company with that
ticker".

What a run does:
- a symbol already in the table is marked `listed` and takes FMP's name;
- a new symbol costs one `/profile` call for its CIK, then either
  - renames the issuer's existing row when that row's ticker has left the
    universe (the old symbol becomes a `former` alias),
  - becomes a `share_class` alias when the issuer already has a live row
    (GOOG → Alphabet's GOOGL row: one issuer, one feed), or
  - creates the company;
- a row whose ticker is no longer in the universe is marked
  `listed = False`. Rows are never deleted: watchlists, feed events and
  fundamentals point at them, and SEC ingest still links 8-Ks to them;
- symbols stored feed events used that aren't a live ticker become
  `former` aliases, so every link the feed ever showed still resolves.

A thin or truncated universe (FMP error, plan change) never de-lists
anything: the run adds and updates, and skips the de-list pass.

`ensure_companies_loaded(app)` runs the same sync on startup when the
table is nearly empty.
"""

import logging
import os
import re

from app import db
from app.models.company import Company, CompanyTickerAlias
from app.models.filing_event import FilingEvent
from app.services.company_resolver import find_company_by_symbol
from app.services.fundamentals import fields as F
from app.services.fundamentals.fmp_client import FMPClient, FMPError, fmp_symbol
from app.utils.tickers import TICKER_RE

log = logging.getLogger(__name__)

DEFAULT_EXCHANGES = ("NYSE", "NASDAQ", "AMEX")
# Rows per screener call. A response this long may have been cut off, so
# the run treats the universe as incomplete (no de-listing).
SCREENER_LIMIT = 10000
# Fewer common stocks than this across the three exchanges means FMP
# answered badly, not that the market shrank.
MIN_UNIVERSE = 2500
COMMIT_BATCH = 500

# Non-common lines that share an exchange with the common stock.
_NON_COMMON_NAME = re.compile(
    r"\bwarrants?\b|\bunits?$|\brights?$|\bpreferred\b|\bpfd\b|\bdepositary shares\b"
    r"|\bnotes? due\b|\bdebentures?\b|\bsenior notes\b|\bsubordinated notes\b",
    re.IGNORECASE,
)
_NON_COMMON_SYMBOL = re.compile(r"-(P[A-Z]?|WT|WS|U|UN|R|RT)$")


def _exchanges() -> list[str]:
    raw = os.environ.get("COMPANY_EXCHANGES", "")
    chosen = [e.strip().upper() for e in raw.split(",") if e.strip()]
    return chosen or list(DEFAULT_EXCHANGES)


def _profile_limit() -> int:
    """New symbols looked up per run (one /profile call each)."""
    try:
        return int(os.environ.get("COMPANY_PROFILE_LIMIT", "3000"))
    except ValueError:
        return 3000


def is_common_stock(row: dict) -> bool:
    """A screener row we'd give a company page to."""
    if F.to_bool(F.pick(row, F.PROFILE_FIELDS["is_etf"])):
        return False
    if F.to_bool(F.pick(row, F.PROFILE_FIELDS["is_fund"])):
        return False
    if F.pick(row, F.PROFILE_FIELDS["is_actively_trading"]) is not None \
            and not F.to_bool(F.pick(row, F.PROFILE_FIELDS["is_actively_trading"])):
        return False
    symbol = fmp_symbol(row.get("symbol") or "")
    if (not symbol or len(symbol) > 10 or not TICKER_RE.match(symbol)
            or _NON_COMMON_SYMBOL.search(symbol)):
        return False
    name = (F.pick(row, F.PROFILE_FIELDS["name"]) or "").strip()
    return bool(name) and not _NON_COMMON_NAME.search(name)


def fetch_universe(client: FMPClient) -> tuple[list[dict], bool]:
    """(common-stock rows, complete?) — most-traded line of an issuer first.

    Raises FMPError when an exchange can't be fetched at all.
    """
    rows: dict[str, dict] = {}
    complete = True
    for exchange in _exchanges():
        batch = client.screener(
            exchange=exchange, isEtf=False, isFund=False, isActivelyTrading=True,
            includeAllShareClasses=True, limit=SCREENER_LIMIT,
        )
        if len(batch) >= SCREENER_LIMIT:
            log.warning("Screener returned %d rows for %s — may be truncated", len(batch), exchange)
            complete = False
        for row in batch:
            if not isinstance(row, dict) or not is_common_stock(row):
                continue
            listed_on = (row.get("exchangeShortName") or row.get("exchange") or "").upper()
            if listed_on and listed_on not in _exchanges():
                continue  # the screener ignored the filter
            rows.setdefault(fmp_symbol(row["symbol"]), row)
    if len(rows) < MIN_UNIVERSE:
        log.warning("Only %d common stocks in the FMP universe (< %d) — treating as incomplete",
                    len(rows), MIN_UNIVERSE)
        complete = False
    ordered = sorted(rows.values(), key=lambda r: -(F.to_int(r.get("volume")) or 0))
    return ordered, complete


def _set_alias(ticker: str, company: Company, kind: str) -> None:
    alias = CompanyTickerAlias.query.filter_by(ticker=ticker).first()
    if alias is None:
        db.session.add(CompanyTickerAlias(ticker=ticker, company=company, kind=kind))
    else:
        alias.company = company
        alias.kind = kind


def _drop_alias(ticker: str) -> None:
    """A live ticker always wins over an alias spelled the same."""
    CompanyTickerAlias.query.filter_by(ticker=ticker).delete()


def _pad_cik(raw) -> str | None:
    text = str(raw or "").strip()
    return text.zfill(10) if text.isdigit() and int(text) else None


def sync_companies(client: FMPClient | None = None) -> dict:
    """Make the company table match FMP's listed common stocks.

    Returns counts: universe, updated, added, renamed, share_classes,
    delisted, aliases, pending (new symbols left for the next run).
    """
    client = client or FMPClient()
    stats = dict(universe=0, updated=0, added=0, renamed=0, share_classes=0,
                 delisted=0, aliases=0, pending=0, complete=False)
    if not client.configured:
        log.warning("FMP_API_KEY not set — company sync skipped")
        return stats
    try:
        rows, complete = fetch_universe(client)
    except FMPError:
        log.exception("FMP screener failed — company table left as is")
        return stats
    stats.update(universe=len(rows), complete=complete)
    universe = {fmp_symbol(r["symbol"]) for r in rows}

    companies = Company.query.all()
    by_ticker = {c.ticker.upper(): c for c in companies if c.ticker}
    by_cik: dict[str, Company] = {}
    for c in companies:
        cik = _pad_cik(c.cik)
        if cik:
            # prefer the row that's already in the universe
            if cik not in by_cik or (c.ticker or "").upper() in universe:
                by_cik[cik] = c
    class_aliases = {a.ticker for a in CompanyTickerAlias.query.filter_by(kind="share_class")}

    budget = _profile_limit()
    for i, row in enumerate(rows):
        symbol = fmp_symbol(row["symbol"])
        name = F.pick(row, F.PROFILE_FIELDS["name"]).strip()[:200]
        company = by_ticker.get(symbol)
        if company is not None:
            company.listed = True
            company.name = name
            stats["updated"] += 1
        elif symbol in class_aliases:
            continue
        elif budget <= 0:
            stats["pending"] += 1
            continue
        else:
            budget -= 1
            try:
                profile = client.profile(symbol) or {}
            except FMPError as exc:
                log.info("profile lookup failed for %s: %s", symbol, exc)
                stats["pending"] += 1
                continue
            cik = _pad_cik(F.pick(profile, F.PROFILE_FIELDS["cik"]))
            issuer = by_cik.get(cik) if cik else None
            if issuer is not None and (issuer.ticker or "").upper() in universe:
                _set_alias(symbol, issuer, "share_class")
                class_aliases.add(symbol)
                stats["share_classes"] += 1
                continue
            _drop_alias(symbol)
            if issuer is not None:
                old = (issuer.ticker or "").upper()
                if old:
                    by_ticker.pop(old, None)
                    _set_alias(old, issuer, "former")
                log.info("Company %s renamed %s → %s (CIK %s)", issuer.id, old, symbol, cik)
                issuer.ticker = symbol
                issuer.name = name
                issuer.listed = True
                company = issuer
                stats["renamed"] += 1
            else:
                company = Company(name=name, ticker=symbol, cik=cik, listed=True)
                db.session.add(company)
                if cik:
                    by_cik[cik] = company
                stats["added"] += 1
            by_ticker[symbol] = company
            db.session.flush()
        if (i + 1) % COMMIT_BATCH == 0:
            db.session.commit()
    db.session.commit()

    if complete:
        stats["delisted"] = _mark_delisted(universe)
    else:
        log.warning("Universe incomplete — skipping the de-list pass")
    stats["aliases"] = _alias_event_tickers()
    _backfill_filing_events()
    log.info("Company sync: %s", stats)
    return stats


def _mark_delisted(universe: set[str]) -> int:
    """listed=False for rows whose ticker left the universe — unless one of
    their share-class aliases is still in it, which then becomes the ticker."""
    live_classes: dict[str, CompanyTickerAlias] = {}
    for alias in CompanyTickerAlias.query.filter_by(kind="share_class"):
        if alias.ticker in universe:
            live_classes.setdefault(alias.company_id, alias)
    count = 0
    for company in Company.query.filter(db.or_(Company.listed.is_(None), Company.listed.is_(True))):
        if company.ticker and company.ticker.upper() in universe:
            continue
        live_class = live_classes.get(company.id)
        if live_class is not None and company.ticker:
            old, new = company.ticker.upper(), live_class.ticker
            db.session.delete(live_class)
            db.session.flush()
            company.ticker = new
            company.listed = True
            _set_alias(old, company, "former")
            continue
        company.listed = False
        count += 1
    db.session.commit()
    return count


def _alias_event_tickers() -> int:
    """Symbols feed events were stored under that no longer name a live row
    (SEC's spelling, a pre-rename ticker) → `former` aliases of the event's
    company, so the feed's company links keep resolving."""
    pairs = (db.session.query(FilingEvent.ticker, FilingEvent.company_id)
             .join(Company, Company.id == FilingEvent.company_id)
             .filter(FilingEvent.ticker.isnot(None))
             .filter(FilingEvent.ticker != Company.ticker)
             .distinct().all())
    if not pairs:
        return 0
    live = {t.upper() for (t,) in db.session.query(Company.ticker).filter(Company.ticker.isnot(None))}
    known = {t for (t,) in db.session.query(CompanyTickerAlias.ticker)}
    added = 0
    for ticker, company_id in pairs:
        symbol = (ticker or "").upper()
        if not symbol or symbol in live or symbol in known or len(symbol) > 10:
            continue
        db.session.add(CompanyTickerAlias(ticker=symbol, company_id=company_id, kind="former"))
        known.add(symbol)
        added += 1
    db.session.commit()
    return added


def ensure_companies_loaded(app) -> None:
    """On startup, sync companies if the table is empty or underpopulated."""
    with app.app_context():
        try:
            count = db.session.query(Company.id).count()
        except Exception:
            db.session.rollback()
            log.info("Company table not found — skipping company load (run migrations first)")
            return

        if count >= MIN_UNIVERSE:
            log.info("Company table has %d rows — skipping startup sync", count)
            return

        log.info("Company table has only %d rows — running sync", count)
        sync_companies()


def _backfill_filing_events() -> None:
    """Link orphaned FilingEvents to their Company rows by ticker."""
    orphans = (
        FilingEvent.query
        .filter(FilingEvent.company_id.is_(None))
        .filter(FilingEvent.ticker.isnot(None))
        .all()
    )
    linked = 0
    for event in orphans:
        company = find_company_by_symbol(event.ticker, include_events=False)
        if company is not None:
            event.company_id = company.id
            linked += 1
    if linked:
        db.session.commit()
        log.info("Backfilled company_id on %d/%d orphaned filing events", linked, len(orphans))


# ── live check ─────────────────────────────────────────────────────────

# Well-known lines the universe must (and must not) contain.
MUST_INCLUDE = ("AAPL", "MSFT", "BRK-B", "GOOGL", "GOOG", "JPM", "TSM")
MUST_EXCLUDE = ("SPY", "QQQ", "VTI")


def run_checks(client: FMPClient | None = None) -> list[tuple[str, str, str]]:
    """Live-check the screener assumptions (`flask check-company-universe`)."""
    client = client or FMPClient()
    out = []
    try:
        rows, complete = fetch_universe(client)
    except FMPError as exc:
        return [("universe", "fail", f"screener failed: {exc}")]
    symbols = {fmp_symbol(r["symbol"]) for r in rows}
    out.append(("universe size", "ok" if complete else "fail",
                f"{len(rows)} common stocks on {','.join(_exchanges())}"
                + ("" if complete else " — incomplete (truncated or too few); de-listing is off")))
    missing = [s for s in MUST_INCLUDE if s not in symbols]
    out.append(("well-known stocks", "fail" if missing else "ok",
                f"missing {missing}" if missing else f"all of {list(MUST_INCLUDE)} present"))
    leaked = [s for s in MUST_EXCLUDE if s in symbols]
    out.append(("ETFs excluded", "fail" if leaked else "ok",
                f"ETFs in universe: {leaked}" if leaked else "none of the sampled ETFs present"))
    sample = rows[0] if rows else {}
    has_fields = all(F.pick(sample, F.PROFILE_FIELDS[k]) is not None for k in ("name", "market_cap"))
    out.append(("row shape", "ok" if has_fields else "fail",
                f"fields: {sorted(sample)[:20]}"))
    try:
        profile = client.profile("AAPL") or {}
    except FMPError as exc:
        profile = {}
        out.append(("profile CIK", "fail", str(exc)))
    else:
        cik = _pad_cik(F.pick(profile, F.PROFILE_FIELDS["cik"]))
        out.append(("profile CIK", "ok" if cik == "0000320193" else "fail", f"AAPL cik={cik}"))
    return out
