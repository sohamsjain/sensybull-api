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
    bullets: list[str]
    company_context: str


@dataclass
class Filing:
    id: str           # EDGAR entry ID — dedup key
    title: str        # company name
    cik: str
    ticker: str
    exchange: str
    updated: str      # ISO 8601 timestamp
    url: str          # EDGAR index URL
    items: list[Item] = field(default_factory=list)
    exhibits: list[Exhibit] = field(default_factory=list)
    briefing: Briefing | None = None
