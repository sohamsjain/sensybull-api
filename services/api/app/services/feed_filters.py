# services/api/app/services/feed_filters.py
"""
The feed's filters, applied in SQL.

Every feed filter the web offers is evaluated here, server-side, so a page
of results is a page of *matches*. (Until Sept 2026 the web filtered the 50
rows it had loaded, so "Important · Bankruptcy" showed whatever happened to
be in the last 50 events and called the rest "hidden".)

One parser (`parse_filters`) reads the filters from a query string or from
a saved view's stored dict; one function (`apply_filters`) narrows a
FilingEvent query by them; `facet_counts` counts what each option would
return with every *other* filter held, which is what makes a filter panel
honest about dead ends.

Mirrored by sensybull-web `src/lib/feed-filters.ts`, which applies the same
predicates to live socket events — keep the sector list, the market-cap
bucket bounds and the predicate rules in sync.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app import db
from app.models.company import Company
from app.models.event_type import EventType
from app.models.filing_event import FilingEvent
from app.models.price_reaction import PriceReaction, STATUS_DONE

# FMP's sector taxonomy (the /company-screener and /profile `sector` field).
# Company.sector only ever holds one of these; see `normalize_sector`.
SECTORS = [
    "Technology",
    "Healthcare",
    "Financial Services",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Communication Services",
    "Industrials",
    "Energy",
    "Basic Materials",
    "Real Estate",
    "Utilities",
]

# GICS spellings and FMP's older ones → the canonical name above.
_SECTOR_ALIASES = {
    "information technology": "Technology",
    "health care": "Healthcare",
    "financials": "Financial Services",
    "financial": "Financial Services",
    "consumer discretionary": "Consumer Cyclical",
    "consumer staples": "Consumer Defensive",
    "telecommunication services": "Communication Services",
    "communication": "Communication Services",
    "materials": "Basic Materials",
    "industrial": "Industrials",
    "utility": "Utilities",
}
_SECTOR_BY_LOWER = {s.lower(): s for s in SECTORS}

# Market-cap buckets, ordered largest first: (key, label, low, high) in USD,
# low inclusive, high exclusive. Conventional US boundaries.
CAP_BUCKETS = [
    ("mega", "Mega cap", 200_000_000_000, None),
    ("large", "Large cap", 10_000_000_000, 200_000_000_000),
    ("mid", "Mid cap", 2_000_000_000, 10_000_000_000),
    ("small", "Small cap", 300_000_000, 2_000_000_000),
    ("micro", "Micro cap", 0, 300_000_000),
]
CAP_KEYS = [b[0] for b in CAP_BUCKETS]

SOURCES = ["sec", "pr"]  # SEC filings, newswire press releases
SENTIMENTS = ["Positive", "Negative", "Mixed", "Neutral"]
MOVES = ["any", "up", "down"]  # an explosive reaction, in either / one direction
WINDOWS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90}
SCOPES = ["all", "mine"]

MAX_QUERY_LEN = 100


class FilterError(ValueError):
    """A filter value the API doesn't know — answered as a 400."""


def normalize_sector(raw) -> str | None:
    """A vendor's sector string → one of SECTORS, or None."""
    text = str(raw or "").strip().lower()
    if not text:
        return None
    return _SECTOR_BY_LOWER.get(text) or _SECTOR_ALIASES.get(text)


def cap_bucket(market_cap) -> str | None:
    """The bucket key for a market cap in dollars (None when unknown)."""
    if market_cap is None:
        return None
    try:
        value = float(market_cap)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    for key, _label, low, high in CAP_BUCKETS:
        if value >= low and (high is None or value < high):
            return key
    return None


@dataclass
class FeedFilters:
    scope: str = "all"
    important: bool = False
    event_types: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)
    source: str | None = None
    sentiments: list[str] = field(default_factory=list)
    moved: str | None = None
    window: str | None = None
    q: str = ""
    # Legacy knobs the old endpoints took; still honoured
    max_tier: int = 3
    signal_type: str | None = None

    def to_dict(self) -> dict:
        """The canonical, storable form (what a saved view keeps). Only
        non-default values, so two equal views compare equal."""
        out: dict = {"scope": self.scope}
        if self.important:
            out["important"] = True
        for key, values in (("event_type", self.event_types), ("sector", self.sectors),
                            ("cap", self.caps), ("sentiment", self.sentiments)):
            if values:
                out[key] = list(values)
        if self.source:
            out["source"] = self.source
        if self.moved:
            out["moved"] = self.moved
        if self.window:
            out["since"] = self.window
        if self.q:
            out["q"] = self.q
        return out

    def cache_key(self) -> str:
        parts = self.to_dict()
        parts["max_tier"] = self.max_tier
        parts["signal_type"] = self.signal_type
        return "|".join(f"{k}={parts[k]}" for k in sorted(parts))


# ── parsing ────────────────────────────────────────────────────────────

def _list(value) -> list[str]:
    """A comma-separated string, a list, or a repeated query arg → values."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _pick(allowed: list[str], values: list[str], name: str, *, casefold=False) -> list[str]:
    lookup = {a.lower(): a for a in allowed} if casefold else {a: a for a in allowed}
    out = []
    for v in values:
        hit = lookup.get(v.lower() if casefold else v)
        if hit is None:
            raise FilterError(f"unknown {name}: {v!r}")
        out.append(hit)
    return out


def parse_filters(source, *, event_types: list[str] | None = None) -> FeedFilters:
    """Read filters from request.args (a MultiDict) or a stored dict.

    Unknown values raise FilterError rather than being ignored: a typo'd
    filter that silently widens the feed back to everything looks like a
    working filter with nothing to show.
    """
    from app.routes.events import EVENT_TYPES  # the canonical list lives with its route

    def get(key):
        if hasattr(source, "getlist"):
            values = source.getlist(key)
            if not values:
                return None
            return values if len(values) > 1 else values[0]
        return source.get(key)

    f = FeedFilters()
    scope = get("scope")
    if scope is not None:
        if scope not in SCOPES:
            raise FilterError(f"unknown scope: {scope!r}")
        f.scope = scope
    f.important = _bool(get("important"))
    f.event_types = _pick(event_types or EVENT_TYPES, _list(get("event_type")),
                          "event_type", casefold=True)
    f.sectors = _pick(SECTORS, [normalize_sector(s) or s for s in _list(get("sector"))], "sector")
    f.caps = _pick(CAP_KEYS, [c.lower() for c in _list(get("cap"))], "cap")
    f.sentiments = _pick(SENTIMENTS, _list(get("sentiment")), "sentiment", casefold=True)

    src = get("source")
    if src:
        src = str(src).lower()
        if src not in SOURCES:
            raise FilterError(f"unknown source: {src!r}")
        f.source = src

    moved = get("moved")
    if moved:
        moved = str(moved).lower()
        if moved not in MOVES:
            raise FilterError(f"unknown moved: {moved!r}")
        f.moved = moved

    since = get("since")
    if since:
        since = str(since).lower()
        if since not in WINDOWS:
            raise FilterError(f"unknown since: {since!r} (one of {', '.join(WINDOWS)})")
        f.window = since

    q = get("q")
    if q:
        f.q = re.sub(r"\s+", " ", str(q)).strip()[:MAX_QUERY_LEN]

    tier = get("max_tier")
    if tier is not None:
        try:
            f.max_tier = int(tier)
        except (TypeError, ValueError):
            raise FilterError(f"bad max_tier: {tier!r}") from None
    f.signal_type = get("signal_type") or None
    return f


# ── SQL ────────────────────────────────────────────────────────────────

def order_column(scope: str):
    """The column each scope's endpoint sorts by: /events/ by filing date,
    /events/all by receipt (so REST pages line up with the live socket)."""
    return FilingEvent.filing_date if scope == "mine" else FilingEvent.created_at


def _json_text(key: str):
    return FilingEvent.briefing_json[key].as_string()


def important_clause():
    """Mirrors FilingEvent.important: significance High, or tier 1 when
    the briefing carries no significance at all."""
    significance = _json_text("significance")
    return sa.or_(
        significance == "High",
        sa.and_(significance.is_(None), FilingEvent.max_tier == 1),
    )


def _cap_clause(caps: list[str]):
    ranges = []
    for key, _label, low, high in CAP_BUCKETS:
        if key not in caps:
            continue
        cond = Company.market_cap >= max(low, 1)
        if high is not None:
            cond = sa.and_(cond, Company.market_cap < high)
        ranges.append(cond)
    return sa.or_(*ranges)


def _company_ids_where(*conds):
    return sa.select(Company.id).where(*conds)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def apply_filters(query, f: FeedFilters, *, skip: str | None = None, now: datetime | None = None):
    """Narrow a FilingEvent query by every filter except `skip` (a facet
    name — see facet_counts). Scope is the caller's business: the two
    scopes differ in who may ask, not in how a filter reads."""
    query = query.filter(FilingEvent.max_tier <= f.max_tier)
    if f.signal_type:
        query = query.filter(FilingEvent.signal_type == f.signal_type)

    if f.important and skip != "important":
        query = query.filter(important_clause())

    if f.event_types and skip != "event_type":
        query = query.filter(FilingEvent.event_types.any(EventType.type_name.in_(f.event_types)))

    if f.sectors and skip != "sector":
        query = query.filter(FilingEvent.company_id.in_(
            _company_ids_where(Company.sector.in_(f.sectors))))

    if f.caps and skip != "cap":
        query = query.filter(FilingEvent.company_id.in_(_company_ids_where(_cap_clause(f.caps))))

    if f.source and skip != "source":
        is_pr = FilingEvent.signal_type == "PR"
        query = query.filter(is_pr if f.source == "pr" else sa.not_(is_pr))

    if f.sentiments and skip != "sentiment":
        query = query.filter(_json_text("sentiment").in_(f.sentiments))

    if f.moved and skip != "moved":
        conds = [PriceReaction.is_explosive.is_(True), PriceReaction.status == STATUS_DONE]
        if f.moved == "up":
            conds.append(PriceReaction.pct_change > 0)
        elif f.moved == "down":
            conds.append(PriceReaction.pct_change < 0)
        query = query.filter(FilingEvent.price_reactions.any(sa.and_(*conds)))

    if f.window and skip != "since":
        now = now or datetime.now(timezone.utc)
        query = query.filter(order_column(f.scope) >= now - timedelta(days=WINDOWS[f.window]))

    if f.q and skip != "q":
        like = f"%{_escape_like(f.q.lower())}%"
        query = query.filter(sa.or_(
            sa.func.lower(FilingEvent.ticker).like(like, escape="\\"),
            sa.func.lower(FilingEvent.company_name).like(like, escape="\\"),
            sa.func.lower(_json_text("headline")).like(like, escape="\\"),
        ))
    return query


# ── facets ─────────────────────────────────────────────────────────────

def facet_counts(base_query_factory, f: FeedFilters) -> dict:
    """How many events each filter option would show, holding every other
    filter as it is (so choosing a second sector widens, never zeroes).

    `base_query_factory()` returns a fresh FilingEvent query already
    narrowed to the scope (e.g. the reader's companies).
    """
    def ids(skip):
        return apply_filters(base_query_factory(), f, skip=skip) \
            .with_entities(FilingEvent.id).subquery()

    total = apply_filters(base_query_factory(), f).order_by(None).count()

    sub = ids("event_type")
    event_type = dict(
        db.session.query(EventType.type_name, sa.func.count(sa.distinct(EventType.filing_event_id)))
        .filter(EventType.filing_event_id.in_(sa.select(sub.c.id)))
        .group_by(EventType.type_name).all()
    )

    def grouped(skip, key_expr, *, join_company=False):
        """{key: events} for one facet. The key is computed in a subquery
        and grouped by its column: grouping by an expression that carries
        bound parameters (a CASE, a JSON path) can fail on Postgres when a
        driver binds server-side, since the SELECT's and the GROUP BY's
        parameters are then different placeholders."""
        inner = sa.select(key_expr.label("k"), FilingEvent.id.label("event_id")) \
            .select_from(FilingEvent)
        if join_company:
            inner = inner.join(Company, Company.id == FilingEvent.company_id)
        inner = inner.where(FilingEvent.id.in_(sa.select(ids(skip).c.id))).subquery()
        rows = db.session.execute(
            sa.select(inner.c.k, sa.func.count(inner.c.event_id)).group_by(inner.c.k)
        ).all()
        return {k: n for k, n in rows if k is not None}

    sector = {k: n for k, n in grouped("sector", Company.sector, join_company=True).items()
              if k in SECTORS}

    bucket_expr = sa.case(
        *[((Company.market_cap >= max(low, 1)) & (Company.market_cap < high) if high is not None
           else (Company.market_cap >= low), key)
          for key, _label, low, high in CAP_BUCKETS],
        else_=None,
    )
    cap = grouped("cap", bucket_expr, join_company=True)

    source = grouped("source", sa.case((FilingEvent.signal_type == "PR", "pr"), else_="sec"))

    sentiment = {k: n for k, n in grouped("sentiment", _json_text("sentiment")).items()
                 if k in SENTIMENTS}

    important = apply_filters(base_query_factory(), f, skip="important") \
        .filter(important_clause()).order_by(None).count()

    moved = {}
    for direction in MOVES:
        probe = FeedFilters(**{**f.__dict__, "moved": direction})
        moved[direction] = apply_filters(base_query_factory(), probe).order_by(None).count()

    return {
        "total": total,
        "event_type": event_type,
        "sector": sector,
        "cap": cap,
        "source": source,
        "sentiment": sentiment,
        "important": important,
        "moved": moved,
    }


def options_payload(event_types: list[str]) -> dict:
    """Every option each filter offers, with its label — backs the web's
    filter panel so its lists can't drift from what the API accepts."""
    return {
        "event_type": [t for t in event_types if t != "Other"],
        "sector": SECTORS,
        "cap": [
            {"key": key, "label": label, "min": low or None, "max": high}
            for key, label, low, high in CAP_BUCKETS
        ],
        "source": [{"key": "sec", "label": "SEC filings"}, {"key": "pr", "label": "Press releases"}],
        "sentiment": SENTIMENTS,
        "moved": [
            {"key": "any", "label": "Moved the stock"},
            {"key": "up", "label": "Moved it up"},
            {"key": "down", "label": "Moved it down"},
        ],
        "since": [
            {"key": "1d", "label": "24 hours"},
            {"key": "7d", "label": "7 days"},
            {"key": "30d", "label": "30 days"},
            {"key": "90d", "label": "90 days"},
        ],
    }
