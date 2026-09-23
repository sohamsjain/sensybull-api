"""Symbol → Company, the one way.

Company.ticker holds FMP's symbol (the daily `sync-companies` run keeps it
current). Everything that turns a symbol into a company goes through
`find_company_by_symbol()` so a link built from any symbol we have ever
shown resolves:

1. the live ticker, in either class-share spelling (BRK.B and BRK-B);
2. a ticker alias — a former symbol, or a secondary share class
   (`CompanyTickerAlias`);
3. the symbol a stored feed event carries (events keep the ticker the SEC
   or the newswire used on the day, which can differ from FMP's).

`resolve_event_company()` is the subscriber's variant: an SEC filing names
its issuer by CIK, which beats any ticker.
"""

from app import db
from app.models.company import Company, CompanyTickerAlias
from app.models.filing_event import FilingEvent
from app.services.fundamentals.fmp_client import fmp_symbol


def symbol_variants(symbol: str) -> list[str]:
    """The spellings one symbol goes by: FMP's dash form first."""
    raw = (symbol or '').strip().upper()
    if not raw:
        return []
    return list(dict.fromkeys([fmp_symbol(raw), raw, raw.replace('-', '.')]))


def _pick(companies: list[Company], symbol: str | None = None) -> Company | None:
    """Among rows for one issuer: the exact ticker, then a listed one."""
    if not companies:
        return None
    if symbol:
        for c in companies:
            if c.ticker and c.ticker.upper() in symbol_variants(symbol):
                return c
    return sorted(companies, key=lambda c: c.listed is not True)[0]


def find_company_by_symbol(symbol: str, *, include_events: bool = True) -> Company | None:
    variants = symbol_variants(symbol)
    if not variants:
        return None
    # tickers are stored upper-case, so a plain IN keeps the unique index
    live = Company.query.filter(Company.ticker.in_(variants)).all()
    if live:
        return _pick(live)
    alias = CompanyTickerAlias.query.filter(CompanyTickerAlias.ticker.in_(variants)).first()
    if alias:
        return alias.company
    if include_events:
        event = (FilingEvent.query
                 .filter(FilingEvent.ticker.in_(variants))
                 .filter(FilingEvent.company_id.isnot(None))
                 .order_by(FilingEvent.created_at.desc())
                 .first())
        if event:
            return db.session.get(Company, event.company_id)
    return None


def _cik_forms(cik: str | None) -> list[str]:
    raw = str(cik or '').strip()
    if not raw or not raw.isdigit() or int(raw) == 0:
        return []
    return list(dict.fromkeys([raw.zfill(10), raw.lstrip('0'), raw]))


def resolve_event_company(ticker: str | None, cik: str | None) -> Company | None:
    """The company an incoming feed event belongs to, or None."""
    ciks = _cik_forms(cik)
    if ciks:
        by_cik = Company.query.filter(Company.cik.in_(ciks)).all()
        if by_cik:
            return _pick(by_cik, ticker)
    if ticker:
        return find_company_by_symbol(ticker, include_events=False)
    return None
