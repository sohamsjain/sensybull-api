"""
briefing.py — LLM-powered filing briefing + event classification via Groq.

Takes a Filing + raw exhibit HTML and produces a structured Briefing in a
single LLM pass: the model is shown the filing text and asked for a
headline, summary, classification, and key dates. Prompt guidance tells it
to stick to the filing text.

If the LLM call fails, the model reports insufficient content, or the
filing has too little text to summarize, the event publishes with a
deterministic facts-only briefing instead (form type, item categories,
tier-derived significance).
"""

import itertools
import json
import logging
import os
import threading

from groq import Groq

from forms import LLM_HINTS
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
_TOTAL_TEXT_CAP = 24_000

# The LLM is only called when at least this much substantive filing text
# (item bodies + exhibit bodies) exists to summarize. Below this there is
# nothing to say — skip the call and publish facts-only.
_MIN_SOURCE_CHARS = 200

# Model fallback chain: try the best model first, then degrade to the next
# on errors that are specific to a single model (rate limits, or a model
# that Groq has decommissioned / that the key can't access). Overridable via
# GROQ_MODELS (comma-separated, best-first) so a model retirement can be
# worked around by config without a redeploy.
def _load_model_chain() -> list[str]:
    raw = os.environ.get("GROQ_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()]
    if models:
        log.info("Groq model chain from GROQ_MODELS: %s", ", ".join(models))
        return models
    return [
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
    ]

_MODEL_CHAIN = _load_model_chain()

# Canonical event types for classification — deliberately a SMALL list of
# highly material categories (July 2026: the long multi-form taxonomy was
# rolled back along with non-8-K ingestion). The LLM picks 1-3 from this
# list (or "Other" as fallback).
EVENT_TYPES = [
    "Acquisition",
    "Material Agreement",
    "Earnings",
    "Bankruptcy",
    "Debt / Financing",
    "Restructuring",
    "Leadership Change",
    "Delisting",
    "Restatement",
    "Cybersecurity Incident",
    "Regulatory / Clinical",
    "Other",
]
# NOTE: this list is mirrored in services/api/app/routes/events.py — keep in sync.

_EVENT_TYPES_STR = ", ".join(f'"{t}"' for t in EVENT_TYPES)

# Deterministic 8-K item → event type mapping for facts-only briefings.
# Only unambiguous items are mapped; everything else falls back to "Other".
# Labels must exist in EVENT_TYPES.
_ITEM_EVENT_TYPES: dict[str, str] = {
    "1.01": "Material Agreement",
    "1.02": "Material Agreement",
    "1.03": "Bankruptcy",
    "1.05": "Cybersecurity Incident",
    "2.01": "Acquisition",
    "2.02": "Earnings",
    "2.03": "Debt / Financing",
    "2.05": "Restructuring",
    "3.01": "Delisting",
    "4.02": "Restatement",
    "5.02": "Leadership Change",
}

_TIER_SIGNIFICANCE = {1: "High", 2: "Medium", 3: "Low"}

# Narrative voice — shared by every prompt that asks the model to write
# prose (8-K briefings here, press releases in press_release/materiality.py).
#
# Source documents speak in the company's own voice: 8-K item text says
# "the Company", and the EX-99.1 press releases they attach say "we". A
# model summarizing that text mirrors whatever voice it was handed unless
# told otherwise. Everything we generate is our copy ABOUT a company, never
# the company talking to the reader, so the rule is stated once and both
# prompts embed it verbatim.
VOICE_RULES = """\
VOICE (applies to "headline", "summary" and "investor_takeaway"):
- You are an outside analyst writing about the company for investors.
  Refer to it by name or as "the company" — never "we", "our", "us",
  "you" or "your", even where the source text uses them.
- Report what the source says without adopting its promotional framing.
  Attribute the company's own expectations and self-assessments to it
  ("the company expects ...") instead of asserting them as fact."""

_SYSTEM_PROMPT_TEMPLATE = f"""\
You are a buyside special-situations analyst reading SEC filings.
Your job is to interpret filings the way an event-driven investor would —
identify the deal, the parties, the economics, and the status — using
ONLY what the filing text says.

Given a {{form_name}} filing, produce a JSON object with these fields:

1. "headline" — one short, plain-English sentence (max 100 chars) that an
   everyday reader would understand at a glance. Write it the way a person
   would say it aloud — no semicolon-separated fragments, no jargon strings.
   Focus on WHAT is happening, not who filed.
   Good: "FGMC agrees to a SPAC merger with a forward purchase of up to 3M shares"
   Bad:  "SPAC merger with FGMC; forward purchase agreement for up to 3M shares"
   Bad:  "FG Merger II Corp. enters into Forward Purchase Agreement with Atsion"

2. "summary" — a 2-4 sentence paragraph about the SUBJECT COMPANY (the
   company whose stock is affected, which is not always the filer). Tell
   the investor story: what deal is happening, who the counterparties are,
   key economics (dollar amounts, share counts, prices), and the current
   procedural status (vote pending, effective date, etc.).
   Write flowing prose, not bullet points.

3. "primary_event_type" — the single MOST investor-relevant label from this list:
   [{_EVENT_TYPES_STR}]

4. "event_types" — 1 to 3 labels from the same list (including the primary).

5. "deal_terms" — a flat object of key-value pairs extracting structured data.
   Every value MUST be a plain, display-ready string — never a nested
   object, array, or expression. If a figure is a total you had to add up,
   write the total itself ("$11.5B"), not the arithmetic.
   Good: "deal_value": "$11.5B"
   Bad:  "deal_value": {{"$sum": "11500000000"}}
   Bad:  "deal_value": ["$500M", "$7B", "$4B"]
   Include whichever of these apply (omit fields that don't):
   - "counterparty": the other party in the transaction
   - "deal_value": total consideration or deal size, abbreviated ("$11.5B")
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
   - "Routine COO-to-CCO title change; no compensation or reporting changes."
   - "$200M shelf registration signals potential near-term equity raise; dilution risk."

9. "catalysts" — list of key upcoming dates/events extracted from the filing.
   Each entry: {{"date": "YYYY-MM-DD" or null, "event": "description"}}.
   Include: vote dates, tender deadlines, expected close dates, effective dates,
   record dates. Omit this field entirely if no catalysts are mentioned.

{VOICE_RULES}
{{form_guidance}}
RULES:
- Use ONLY facts stated in the filing text below. Do not use memory of the
  company, do not extrapolate, do not guess. If the text does not state
  something, OMIT it.
- Amendments and exhibit-only filings often contain very little: describe
  only what THIS text says (e.g. "refiles the merger agreement exhibit"),
  never the underlying transaction's terms unless restated here.
- If the text is too thin to support a factual summary, respond with
  exactly {{"insufficient_content": true}} and no other fields.
Respond ONLY with valid JSON. No markdown, no commentary."""


def _system_prompt(form_type: str) -> str:
    """Render the system prompt for a form type.

    Placeholders are substituted with str.replace, not str.format — the
    template contains literal JSON braces (the catalysts example) that
    format() would choke on.
    """
    form_name = form_type or "8-K"
    hint = LLM_HINTS.get(form_name, "")
    guidance = f"\nFORM-SPECIFIC GUIDANCE ({form_name}):\n{hint}\n" if hint else ""
    return (
        _SYSTEM_PROMPT_TEMPLATE
        .replace("{form_name}", form_name)
        .replace("{form_guidance}", guidance)
    )


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[...truncated]"


def _build_user_message(filing: Filing, exhibit_plain: dict[str, str]) -> str:
    parts: list[str] = []
    parts.append(f"Company: {filing.title}")
    if filing.ticker:
        parts.append(f"Ticker: {filing.ticker}")
    if filing.form_type and filing.form_type != "8-K":
        parts.append(f"Form: {filing.form_type}")
    parts.append(f"Filed: {filing.updated}")
    parts.append("")

    # Item text
    for item in filing.items:
        parts.append(f"--- Item {item.number}: {item.title} ({item.category}) ---")
        parts.append(_truncate(item.text, _ITEM_TEXT_CAP))
        parts.append("")

    # Exhibit index — titles from the EDGAR filing index are facts and often
    # the only clue to what an exhibit-only filing contains (e.g. "EX-2.1
    # AGREEMENT AND PLAN OF MERGER, DATED AS OF ...").
    if filing.exhibits:
        parts.append("--- Exhibit Index (titles only; exhibit bodies not included unless below) ---")
        for ex in filing.exhibits:
            desc = f": {ex.description}" if ex.description else ""
            parts.append(f"{ex.type}{desc}")
        parts.append("")

    # Exhibit text
    for ex_type, plain in exhibit_plain.items():
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


def _coerce_deal_terms(raw: object) -> dict[str, str]:
    """Flatten the model's deal_terms into a display-ready str→str dict.

    Values must survive as plain strings: they are rendered verbatim in the
    Deal Terms block. A bare str(v) is not enough — the model sometimes
    answers with a nested object when a figure is a total it had to add up
    (e.g. {"deal_value": {"$sum": "11500000000"}}), and str() on a dict
    yields its Python repr, which then ships to the UI as literal
    "{'$sum': '11500000000'}". Unwrap single-scalar containers, drop
    anything else.
    """
    if not isinstance(raw, dict):
        return {}

    out: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not value:
            continue
        scalar = _scalar_term(value)
        if scalar:
            out[str(key)] = scalar
    return out


def _scalar_term(value: object, _depth: int = 0) -> str:
    """Return value as a display string, or "" if it isn't scalar-shaped.

    Containers holding exactly one usable value are unwrapped (one level of
    nesting at a time) since the payload is still unambiguous; anything with
    several values would need a formatting decision this layer can't make.
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""      # a bare true/false is never a meaningful deal term
    if isinstance(value, (int, float)):
        return str(value)
    if _depth >= 2:
        return ""      # deeply nested: give up rather than guess
    if isinstance(value, dict):
        inner = list(value.values())
    elif isinstance(value, (list, tuple)):
        inner = list(value)
    else:
        return ""
    usable = [v for v in inner if v or v == 0]
    if len(usable) != 1:
        return ""
    return _scalar_term(usable[0], _depth + 1)


def _is_rate_limit(exc: Exception) -> bool:
    """Return True if the exception signals a Groq rate-limit (HTTP 429)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 429


def _is_model_unavailable(exc: Exception) -> bool:
    """Return True if Groq rejected the model as nonexistent/inaccessible.

    Groq returns HTTP 404 with code "model_not_found" when a model has been
    decommissioned or isn't enabled for the key. Like a rate limit, this is
    specific to the current model, so the chain should degrade to the next
    one rather than aborting the whole call.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 404:
        return True
    if getattr(exc, "code", None) == "model_not_found":
        return True
    return "model_not_found" in str(exc) or "does not exist" in str(exc)


def _chat_json(messages: list[dict], max_tokens: int = 1024) -> dict:
    """One JSON-mode chat completion over the model fallback chain.

    Errors specific to a single model — rate limits (429) and unavailable
    models (404 model_not_found, e.g. a decommissioned model) — fall through
    to the next model in the chain. Any other error, or exhausting the
    chain, raises to the caller.
    """
    last_exc: Exception = RuntimeError("empty model chain")
    for model in _MODEL_CHAIN:
        try:
            client = Groq(api_key=_next_api_key())
            response = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=messages,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as exc:
            last_exc = exc
            rate_limited = _is_rate_limit(exc)
            if (rate_limited or _is_model_unavailable(exc)) and model != _MODEL_CHAIN[-1]:
                reason = "rate-limited" if rate_limited else "unavailable"
                log.warning("Model %s %s, falling back", model, reason)
                continue
            raise
    raise last_exc


# ---------------------------------------------------------------------------
# Facts-only briefing (deterministic — no LLM content whatsoever)
# ---------------------------------------------------------------------------

def facts_only_briefing(filing: Filing) -> Briefing:
    """Briefing built purely from parsed filing structure.

    Used whenever the LLM path cannot produce a narrative: too little
    source text, LLM failure, or the model reporting insufficient content.
    Every field here is mechanical — form type, item categories from the
    8-K item number registry, tier-derived significance.
    """
    mapped = []
    for it in filing.items:
        t = _ITEM_EVENT_TYPES.get(it.number)
        if t and t not in mapped:
            mapped.append(t)
    event_types = mapped[:3] or ["Other"]

    categories = []
    for it in filing.items:
        label = it.category or f"Item {it.number}"
        if label not in categories:
            categories.append(label)
    if categories:
        headline = f"{filing.form_type} filed: {', '.join(categories)}"
    else:
        headline = f"{filing.form_type} filed — see filing for details"

    tier = min((it.tier for it in filing.items), default=3)
    significance = _TIER_SIGNIFICANCE.get(tier, "Medium")

    return Briefing(
        headline=headline, summary="", primary_event_type=event_types[0],
        deal_terms={}, significance=significance, sentiment="Neutral",
        investor_takeaway="", catalysts=[], event_types=event_types,
        mode="facts_only",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_briefing(filing: Filing, exhibit_texts: dict[str, str]) -> Briefing:
    """Generate a Briefing from filing data + exhibit HTML in one LLM pass.

    Falls back to a deterministic facts-only briefing when the LLM call
    fails or there is too little source text to summarize.
    """
    exhibit_plain = {
        ex_type: strip_html(html).strip()
        for ex_type, html in exhibit_texts.items()
    }

    # Skip the LLM call entirely when there's nothing to summarize
    # (e.g. exhibit-only 8-K/A amendments).
    substantive = "\n".join(
        [it.text for it in filing.items]
        + list(exhibit_plain.values())
    ).strip()
    if len(substantive) < _MIN_SOURCE_CHARS:
        log.info("Facts-only briefing (only %d chars of source text) for %s [%s]",
                 len(substantive), filing.title, filing.form_type)
        return facts_only_briefing(filing)

    messages = [
        {"role": "system", "content": _system_prompt(filing.form_type)},
        {"role": "user", "content": _build_user_message(filing, exhibit_plain)},
    ]

    try:
        data = _chat_json(messages)
    except Exception as exc:
        log.warning("Briefing generation failed for %s: %s", filing.title, exc)
        return facts_only_briefing(filing)

    if data.get("insufficient_content"):
        log.info("Model reported insufficient content for %s [%s]",
                 filing.title, filing.form_type)
        return facts_only_briefing(filing)

    headline = str(data.get("headline") or "").strip()
    summary = str(data.get("summary") or "").strip()
    takeaway = str(data.get("investor_takeaway") or "").strip()

    # Validate primary_event_type against canonical list
    raw_primary = data.get("primary_event_type", "")
    valid_map = {t.lower(): t for t in EVENT_TYPES}
    primary = (valid_map.get(raw_primary.strip().lower(), "Other")
               if isinstance(raw_primary, str) and raw_primary else "Other")

    # Ensure deal_terms is a flat str→str dict
    deal_terms = _coerce_deal_terms(data.get("deal_terms", {}))

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

    if not headline:
        headline = facts_only_briefing(filing).headline

    return Briefing(
        headline=headline,
        summary=summary,
        primary_event_type=primary,
        deal_terms=deal_terms,
        significance=significance,
        sentiment=sentiment,
        investor_takeaway=takeaway,
        catalysts=catalysts,
        event_types=_validate_event_types(data.get("event_types", [])),
        mode="llm",
    )
