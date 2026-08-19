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
    # Taxonomy leaf slugs behind the event's simple categories, most
    # relevant first (see services/ingest/taxonomy.py). Internal detail —
    # the clients render event_types, never this. Empty on facts-only
    # briefings and on events classified before the taxonomy shipped.
    taxonomy: list[str] = field(default_factory=list)
    taxonomy_version: str = ""
    # "llm" | "facts_only" — how the narrative was produced (see
    # services/ingest/briefing.py). facts_only means no LLM-authored text.
    # Historical events also carry "llm_verified" / "structured".
    mode: str = "llm"


@dataclass
class FilingEvent:
    """Top-level event published to Redis channel `filing:new`."""
    # Ingest-side identifiers
    edgar_id: str            # EDGAR Atom entry ID — global dedup key.
                             # Press releases use a synthetic "pr:<wire>:<guid>".
    signal_type: str         # EDGAR form type ("8-K" / "8-K/A") or "PR"

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

    # Simple, user-facing categories from the taxonomy's top tier
    # (e.g. ["Strategic Transactions", "Capital & Financing"])
    event_types: list[str] = field(default_factory=list)

    # Provenance + dedup (added with press-release ingestion) ---------------
    # "edgar" for SEC filings; a wire name ("globenewswire", "prnewswire",
    # "businesswire", "accesswire") for press releases.
    source: str = "edgar"
    # Wire-reported issuing organization (press releases only)
    issuer_name: str = ""
    # Content fingerprints of the event body — see press_release/fingerprint.py.
    # For PRs: fingerprints of the release itself. For 8-Ks: of the first
    # fetched EX-99 exhibit (or primary document), used for PR-after-8-K drops.
    content_fingerprint: str = ""      # sha256 of normalized headline+body
    headline_fingerprint: str = ""     # sha256 of normalized headline
    content_simhash: str = ""          # 16-hex 64-bit simhash of the body
    # 8-K only: fingerprints of each fetched exhibit + primary document, so
    # the API can match the filing against an already-published PR event.
    # Shape: [{"source": "EX-99.1", "exact": ..., "headline": ..., "simhash": ...}]
    exhibit_fingerprints: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def channel(cls) -> str:
        return "filing:new"
