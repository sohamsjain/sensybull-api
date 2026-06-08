# services/ingest/events.py
"""
FilingEvent — the Redis pub/sub contract between ingest and api.

Every field here is part of the public contract. Add fields freely;
never remove or rename without updating services/api as well.
"""

from dataclasses import dataclass, field, asdict
import json


@dataclass
class FilingEventItem:
    number: str       # "1.01"
    title: str
    tier: int         # 1 = critical, 2 = important, 3 = routine
    category: str     # "Contract", "Earnings", etc.
    text: str         # cleaned plain-text body


@dataclass
class FilingEventExhibit:
    type: str         # "EX-99.1"
    description: str
    url: str


@dataclass
class FilingEventBriefing:
    headline: str
    summary: str
    primary_event_type: str
    significance: str                # "High" / "Medium" / "Low"
    sentiment: str                   # "Positive" / "Negative" / "Neutral" / "Mixed"
    investor_takeaway: str           # 1-sentence "so what"
    catalysts: list[dict[str, str]] = field(default_factory=list)
    deal_terms: dict[str, str] = field(default_factory=dict)


@dataclass
class FilingEvent:
    """Top-level event published to Redis channel `filing:new`."""
    # Ingest-side identifiers
    edgar_id: str            # EDGAR Atom entry ID — global dedup key
    signal_type: str         # "8-K" today; "earnings" / "insider" later

    # Company identity
    cik: str
    ticker: str
    company_name: str        # raw EDGAR title

    # Filing metadata
    filing_date: str         # ISO-8601 timestamp
    edgar_url: str           # EDGAR index URL
    accession_number: str    # parsed from URL; empty string if unavailable

    # Parsed content
    max_tier: int            # lowest tier number among items (1 = most critical)
    items: list[FilingEventItem] = field(default_factory=list)
    exhibits: list[FilingEventExhibit] = field(default_factory=list)
    briefing: FilingEventBriefing | None = None

    # LLM-classified event types (e.g. ["Acquisition", "Debt / Financing"])
    event_types: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def channel(cls) -> str:
        return "filing:new"
