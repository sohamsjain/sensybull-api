"""
briefing.py — LLM-powered filing briefing + event classification via Groq.

Takes a Filing + raw exhibit HTML, produces a structured Briefing that
includes both the human-readable summary and classified event types.
"""

import itertools
import json
import logging
import os
import threading

from groq import Groq

from forms import FormSpec, get_spec
from models import Briefing, Filing
from parser import strip_html

log = logging.getLogger(__name__)

# ── Round-robin Groq API key pool ────────────────────────────────────────
def _load_api_keys() -> list[str]:
    """Load Groq API keys from GROQ_API_KEYS (comma-separated) or GROQ_API_KEY."""
    raw = os.environ.get("GROQ_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("GROQ_API_KEY", "")
        if single.strip():
            keys = [single.strip()]
    if not keys:
        raise RuntimeError("No Groq API keys configured. Set GROQ_API_KEYS or GROQ_API_KEY.")
    log.info("Loaded %d Groq API key(s) for round-robin rotation.", len(keys))
    return keys

_key_cycle = itertools.cycle(_load_api_keys())
_key_lock = threading.Lock()

def _next_api_key() -> str:
    with _key_lock:
        return next(_key_cycle)

_ITEM_TEXT_CAP = 6_000
_EXHIBIT_TEXT_CAP = 8_000
_DOCUMENT_TEXT_CAP = 12_000
_TOTAL_TEXT_CAP = 24_000

# Model fallback chain: try the best model first, fall back on rate-limit errors.
_MODEL_CHAIN = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]

# Canonical event types for classification.
# The LLM picks 1-3 from this list (or "Other" as fallback).
EVENT_TYPES = [
    "M&A / Merger",
    "Acquisition",
    "Divestiture",
    "Activist Proxy",
    "Activist Initial",
    "Strategic Review",
    "Tender Offer",
    "Issuer Tender",
    "Going-Private",
    "Going Dark",
    "Spin-Off",
    "Capital Return",
    "Rights Issue",
    "Restructuring",
    "Insolvency",
    "Liquidation",
    "Delisting",
    "Busted M&A",
    "Litigation",
    "Domicile Change",
    "Earnings",
    "Leadership Change",
    "Debt / Financing",
    "Impairment",
    "Restatement",
    "Regulatory Action",
    "Cybersecurity Incident",
    "Material Agreement",
    "Dividend Change",
    "Bankruptcy",
    "Shelf Registration",
    "Share Offering",
    "Stock Split",
    "Insider Buying",
    "Late Filing",
    "Other",
]
# NOTE: this list is mirrored in services/api/app/routes/events.py — keep in sync.

_EVENT_TYPES_STR = ", ".join(f'"{t}"' for t in EVENT_TYPES)

_SYSTEM_PROMPT_TEMPLATE = f"""\
You are a buyside special-situations analyst reading SEC filings.
Your job is to interpret filings the way an event-driven investor would —
identify the deal, the parties, the economics, and the status.

Given a {{form_name}} filing{{category_note}}, produce a JSON object with these fields:

1. "headline" — one short, plain-English sentence (max 100 chars) that an
   everyday reader would understand at a glance. Write it the way a person
   would say it aloud — no semicolon-separated fragments, no jargon strings.
   Focus on WHAT is happening, not who filed.
   Good: "FGMC agrees to a SPAC merger with a forward purchase of up to 3M shares"
   Bad:  "SPAC merger with FGMC; forward purchase agreement for up to 3M shares"
   Bad:  "FG Merger II Corp. enters into Forward Purchase Agreement with Atsion"

2. "summary" — a 2-4 sentence paragraph written from the SUBJECT COMPANY's
   perspective (the company whose stock is affected, which is not always the
   filer). Tell the investor story: what deal is happening, who the
   counterparties are, key economics (dollar amounts, share counts, prices),
   and the current procedural status (vote pending, effective date, etc.).
   Write flowing prose, not bullet points.

3. "primary_event_type" — the single MOST investor-relevant label from this list:
   [{_EVENT_TYPES_STR}]

4. "event_types" — 1 to 3 labels from the same list (including the primary).

5. "deal_terms" — a flat object of key-value pairs extracting structured data.
   Include whichever of these apply (omit fields that don't):
   - "counterparty": the other party in the transaction
   - "deal_value": total consideration or deal size
   - "share_count": number of shares involved
   - "price_per_share": per-share price if stated
   - "premium": acquisition premium if stated or calculable (e.g. "45%")
   - "consideration_type": "cash", "stock", or "mixed"
   - "deal_status": current status (e.g. "definitive agreement signed",
     "vote pending", "closed", "registration effective")
   - "expected_close": expected or actual closing date
   - "deal_type": e.g. "SPAC merger", "asset purchase", "stock-for-stock"

6. "significance" — how actionable is this for an event-driven investor?
   "High" = potential trade setup (M&A, tender, activist, material deal, bankruptcy).
   "Medium" = notable but not immediately tradeable (leadership change, debt raise,
   restructuring, earnings).
   "Low" = routine/informational (bylaw change, Reg FD, title change, compliance).

7. "sentiment" — net impact on the company's stock:
   "Positive", "Negative", "Neutral", or "Mixed".

8. "investor_takeaway" — one sentence: the "so what" for a portfolio manager.
   Examples:
   - "Creates ~$2.50/share merger arb spread with expected Q3 2026 close."
   - "Routine COO-to-CCO title change; no compensation or reporting changes."
   - "$200M shelf registration signals potential near-term equity raise; dilution risk."

9. "catalysts" — list of key upcoming dates/events extracted from the filing.
   Each entry: {{"date": "YYYY-MM-DD" or null, "event": "description"}}.
   Include: vote dates, tender deadlines, expected close dates, effective dates,
   record dates. Omit this field entirely if no catalysts are mentioned.
{{form_guidance}}
Respond ONLY with valid JSON. No markdown, no commentary."""


def _system_prompt(spec: FormSpec | None) -> str:
    """Render the system prompt for a form type (None → historical 8-K).

    Placeholders are substituted with str.replace, not str.format — the
    template contains literal JSON braces (the catalysts example) that
    format() would choke on.
    """
    form_name = spec.form if spec else "8-K"
    category_note = f" ({spec.category})" if spec and spec.category else ""
    guidance = ""
    if spec and spec.llm_hint:
        guidance = f"\nFORM-SPECIFIC GUIDANCE ({form_name}):\n{spec.llm_hint}\n"
    return (
        _SYSTEM_PROMPT_TEMPLATE
        .replace("{form_name}", form_name)
        .replace("{category_note}", category_note)
        .replace("{form_guidance}", guidance)
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[...truncated]"


def _build_user_message(filing: Filing, exhibit_texts: dict[str, str]) -> str:
    parts: list[str] = []
    parts.append(f"Company: {filing.title}")
    if filing.ticker:
        parts.append(f"Ticker: {filing.ticker}")
    if filing.form_type and filing.form_type != "8-K":
        parts.append(f"Form: {filing.form_type}")
    if filing.filed_by:
        parts.append(f"Filed by: {filing.filed_by}")
    parts.append(f"Filed: {filing.updated}")
    parts.append("")

    # Item text
    for item in filing.items:
        parts.append(f"--- Item {item.number}: {item.title} ({item.category}) ---")
        parts.append(_truncate(item.text, _ITEM_TEXT_CAP))
        parts.append("")

    # Document excerpt (forms without item structure: proxies, tenders, NT…)
    if filing.document_excerpt:
        parts.append(f"--- Filing Document ({filing.form_type}) ---")
        parts.append(_truncate(filing.document_excerpt, _DOCUMENT_TEXT_CAP))
        parts.append("")

    # Exhibit text
    for ex_type, html in exhibit_texts.items():
        plain = strip_html(html).strip()
        if not plain:
            continue
        parts.append(f"--- Exhibit {ex_type} ---")
        parts.append(_truncate(plain, _EXHIBIT_TEXT_CAP))
        parts.append("")

    combined = "\n".join(parts)
    return _truncate(combined, _TOTAL_TEXT_CAP)


def _validate_event_types(raw: list) -> list[str]:
    """Keep only labels that exist in the canonical list, capped at 3."""
    valid = {t.lower(): t for t in EVENT_TYPES}
    out: list[str] = []
    for label in raw:
        if not isinstance(label, str):
            continue
        canonical = valid.get(label.strip().lower())
        if canonical:
            out.append(canonical)
    return out[:3] or ["Other"]


def _is_rate_limit(exc: Exception) -> bool:
    """Return True if the exception signals a Groq rate-limit (HTTP 429)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 429


def generate_briefing(filing: Filing, exhibit_texts: dict[str, str]) -> Briefing:
    """Generate a Briefing from filing data + exhibit HTML. Returns fallback on failure."""
    spec = get_spec(filing.form_type)
    # LLM failure still needs a filterable classification — a 13D whose
    # briefing call fails must still land as "Activist Initial", not "Other"
    fallback_type = spec.default_event_type if spec else "Other"
    fallback = Briefing(
        headline=filing.title, summary="", primary_event_type=fallback_type,
        deal_terms={}, significance="Medium", sentiment="Neutral",
        investor_takeaway="", catalysts=[], event_types=[fallback_type],
    )
    user_msg = _build_user_message(filing, exhibit_texts)
    messages = [
        {"role": "system", "content": _system_prompt(spec)},
        {"role": "user", "content": user_msg},
    ]

    for model in _MODEL_CHAIN:
        try:
            client = Groq(api_key=_next_api_key())
            response = client.chat.completions.create(
                model=model,
                max_tokens=1024,
                response_format={"type": "json_object"},
                messages=messages,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            log.info("Briefing generated via %s for %s", model, filing.title)

            # Validate primary_event_type against canonical list
            raw_primary = data.get("primary_event_type", "")
            valid_map = {t.lower(): t for t in EVENT_TYPES}
            primary = valid_map.get(raw_primary.strip().lower(), "Other") if raw_primary else "Other"

            # Ensure deal_terms is a flat str→str dict
            raw_terms = data.get("deal_terms", {})
            deal_terms = {
                str(k): str(v) for k, v in raw_terms.items()
                if isinstance(k, str) and v
            } if isinstance(raw_terms, dict) else {}

            # Validate significance
            _VALID_SIGNIFICANCE = {"high": "High", "medium": "Medium", "low": "Low"}
            raw_sig = data.get("significance", "")
            significance = _VALID_SIGNIFICANCE.get(
                raw_sig.strip().lower() if isinstance(raw_sig, str) else "", "Medium"
            )

            # Validate sentiment
            _VALID_SENTIMENT = {"positive": "Positive", "negative": "Negative",
                                "neutral": "Neutral", "mixed": "Mixed"}
            raw_sent = data.get("sentiment", "")
            sentiment = _VALID_SENTIMENT.get(
                raw_sent.strip().lower() if isinstance(raw_sent, str) else "", "Neutral"
            )

            # Validate catalysts — list of dicts with "event" key
            raw_catalysts = data.get("catalysts", [])
            catalysts = []
            if isinstance(raw_catalysts, list):
                for cat in raw_catalysts:
                    if isinstance(cat, dict) and cat.get("event"):
                        catalysts.append({
                            "date": str(cat["date"]) if cat.get("date") else None,
                            "event": str(cat["event"]),
                        })

            return Briefing(
                headline=data.get("headline", filing.title),
                summary=data.get("summary", ""),
                primary_event_type=primary,
                deal_terms=deal_terms,
                significance=significance,
                sentiment=sentiment,
                investor_takeaway=data.get("investor_takeaway", ""),
                catalysts=catalysts,
                event_types=_validate_event_types(data.get("event_types", [])),
            )
        except Exception as exc:
            if _is_rate_limit(exc) and model != _MODEL_CHAIN[-1]:
                log.warning("Rate-limited on %s, falling back for %s", model, filing.title)
                continue
            log.warning("Briefing generation failed on %s for %s: %s", model, filing.title, exc)
            return fallback

    return fallback
