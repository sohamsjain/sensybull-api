from dataclasses import dataclass, field


@dataclass
class Item:
    number: str       # "1.01"
    title: str
    text: str
    tier: int         # 1 = highest signal, 3 = lowest
    category: str     # "Contract", "Earnings", etc.


@dataclass
class Exhibit:
    type: str         # "EX-10.1"
    description: str
    url: str


@dataclass
class Briefing:
    headline: str
    summary: str
    primary_event_type: str
    deal_terms: dict[str, str]       # e.g. {"counterparty": "...", "deal_value": "..."}
    significance: str                # "High" / "Medium" / "Low"
    sentiment: str                   # "Positive" / "Negative" / "Neutral" / "Mixed"
    investor_takeaway: str           # 1-sentence "so what" for a PM
    catalysts: list[dict[str, str]]  # [{"date": "2026-07-15", "event": "Shareholder vote"}]
    event_types: list[str] = field(default_factory=lambda: ["Other"])


@dataclass
class Filing:
    id: str           # EDGAR entry ID — dedup key
    title: str        # company name
    cik: str
    ticker: str
    updated: str      # ISO 8601 timestamp
    url: str          # EDGAR index URL
    items: list[Item] = field(default_factory=list)
    exhibits: list[Exhibit] = field(default_factory=list)
    briefing: Briefing | None = None
