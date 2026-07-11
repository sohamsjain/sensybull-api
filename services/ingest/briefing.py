"""
briefing.py — LLM-powered filing briefing + event classification via Groq.

Takes a Filing + raw exhibit HTML, produces a structured Briefing that
includes both the human-readable summary and classified event types.

Anti-hallucination contract (see grounding.py):
1. The LLM is never called without substantive filing text to ground on.
2. Everything the LLM returns is verified against the exact text it was
   shown — ungrounded numbers/dates/names disqualify the narrative.
3. A second, independent LLM pass fact-checks the surviving narrative.
4. Any failure at any stage degrades to a deterministic facts-only
   briefing (form type, item categories, exhibit list) — never to an
   unverified story.
"""

import itertools
import json
import logging
import os
import threading

from groq import Groq

import grounding
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
_TOTAL_TEXT_CAP = 24_000

# The LLM is only called when at least this much substantive filing text
# (item bodies + exhibit bodies) exists to ground on.
# Below this, there is nothing to summarize — a model asked anyway will
# fabricate a plausible-sounding story (observed in production on
# exhibit-only 8-K/A amendments).
_MIN_SOURCE_CHARS = 200

# Model fallback chain: try the best model first, fall back on rate-limit errors.
_MODEL_CHAIN = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]

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
   - "Routine COO-to-CCO title change; no compensation or reporting changes."
   - "$200M shelf registration signals potential near-term equity raise; dilution risk."

9. "catalysts" — list of key upcoming dates/events extracted from the filing.
   Each entry: {{"date": "YYYY-MM-DD" or null, "event": "description"}}.
   Include: vote dates, tender deadlines, expected close dates, effective dates,
   record dates. Omit this field entirely if no catalysts are mentioned.
{{form_guidance}}
CRITICAL GROUNDING RULES — your output is mechanically checked against the
filing text, and one ungrounded fact discards the entire briefing:
- Use ONLY facts stated in the filing text below. You have NO other
  knowledge about this company. Do not use memory of the company, do not
  use typical deal patterns, do not extrapolate, do not guess.
- Every dollar amount, share count, price, percentage, party name, and
  date you write must appear in the provided text. If the text does not
  state it, OMIT it. An accurate briefing with missing fields is correct;
  a complete-looking briefing with invented fields is worthless.
- Never invent or recall a counterparty. If none is named in the text,
  omit the "counterparty" field.
- Amendments and exhibit-only filings often contain very little: describe
  only what THIS text says (e.g. "refiles the merger agreement exhibit"),
  never the underlying transaction's terms unless restated here.
- If the text is too thin to support a factual summary, respond with
  exactly {{"insufficient_content": true}} and no other fields.
Respond ONLY with valid JSON. No markdown, no commentary."""


_VERIFIER_SYSTEM_PROMPT = """\
You are a strict fact-checker. You are given SOURCE (text from an SEC
filing) and CLAIMS (sentences generated about it). Decide whether EVERY
statement in CLAIMS is directly supported by SOURCE alone. Outside
knowledge must not be used; a claim that is plausible but not stated in
SOURCE is unsupported. Pay special attention to: transaction types
(merger vs investment vs sale), party names, amounts, dates, and who is
doing what to whom.
Respond ONLY with valid JSON:
{"supported": true}
or
{"supported": false, "unsupported": ["<the unsupported claim>", ...]}"""


def _system_prompt(spec: FormSpec | None) -> str:
    """Render the system prompt for a form type (None → historical 8-K).

    Placeholders are substituted with str.replace, not str.format — the
    template contains literal JSON braces (the catalysts example) that
    format() would choke on.
    """
    form_name = spec.form if spec else "8-K"
    guidance = ""
    if spec and spec.llm_hint:
        guidance = f"\nFORM-SPECIFIC GUIDANCE ({form_name}):\n{spec.llm_hint}\n"
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


def _is_rate_limit(exc: Exception) -> bool:
    """Return True if the exception signals a Groq rate-limit (HTTP 429)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    return status == 429


def _chat_json(messages: list[dict], max_tokens: int = 1024) -> dict:
    """One JSON-mode chat completion over the model fallback chain.

    Rate limits fall through to the next model; any other error (or
    exhausting the chain) raises to the caller.
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
            if _is_rate_limit(exc) and model != _MODEL_CHAIN[-1]:
                log.warning("Rate-limited on %s, falling back", model)
                continue
            raise
    raise last_exc


# ---------------------------------------------------------------------------
# Facts-only briefing (deterministic — no LLM content whatsoever)
# ---------------------------------------------------------------------------

def facts_only_briefing(filing: Filing, spec: FormSpec | None) -> Briefing:
    """Briefing built purely from parsed filing structure.

    Used whenever the LLM path cannot produce a VERIFIED narrative: too
    little source text, LLM failure, or grounding rejection. Every field
    here is mechanical — form type, item categories from the 8-K item
    number registry, tier-derived significance.
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
# LLM cross-check (second, independent verification pass)
# ---------------------------------------------------------------------------

def _llm_verify(headline: str, summary: str, source_text: str) -> bool:
    """Independent fact-check of the narrative against the source text.

    Fail-closed: any error, malformed response, or unsupported verdict
    returns False (→ facts-only fallback). Disable with
    BRIEFING_LLM_VERIFY=0 (the deterministic grounding checks still run).
    """
    if os.environ.get("BRIEFING_LLM_VERIFY", "1") == "0":
        return True
    claims = "\n".join(c for c in (headline, summary) if c)
    if not claims:
        return True
    try:
        data = _chat_json([
            {"role": "system", "content": _VERIFIER_SYSTEM_PROMPT},
            {"role": "user", "content": f"SOURCE:\n{source_text}\n\nCLAIMS:\n{claims}"},
        ], max_tokens=512)
        if data.get("supported") is True:
            return True
        log.warning("LLM verifier rejected narrative: %s", data.get("unsupported"))
        return False
    except Exception as exc:
        log.warning("LLM verifier failed (%s) — failing closed", exc)
        return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def generate_briefing(filing: Filing, exhibit_texts: dict[str, str]) -> Briefing:
    """Generate a verified Briefing from filing data + exhibit HTML.

    Returns a facts-only briefing whenever a verified narrative cannot be
    produced — never an unverified one.
    """
    spec = get_spec(filing.form_type)

    exhibit_plain = {
        ex_type: strip_html(html).strip()
        for ex_type, html in exhibit_texts.items()
    }

    # ── Gate: refuse to ask a model about text that isn't there ─────────
    substantive = "\n".join(
        [it.text for it in filing.items]
        + list(exhibit_plain.values())
    ).strip()
    if len(substantive) < _MIN_SOURCE_CHARS:
        log.info("Facts-only briefing (only %d chars of source text) for %s [%s]",
                 len(substantive), filing.title, filing.form_type)
        return facts_only_briefing(filing, spec)

    user_msg = _build_user_message(filing, exhibit_plain)
    messages = [
        {"role": "system", "content": _system_prompt(spec)},
        {"role": "user", "content": user_msg},
    ]

    try:
        data = _chat_json(messages)
    except Exception as exc:
        log.warning("Briefing generation failed for %s: %s", filing.title, exc)
        return facts_only_briefing(filing, spec)

    if data.get("insufficient_content"):
        log.info("Model reported insufficient content for %s [%s]",
                 filing.title, filing.form_type)
        return facts_only_briefing(filing, spec)

    headline = str(data.get("headline") or "").strip()
    summary = str(data.get("summary") or "").strip()
    takeaway = str(data.get("investor_takeaway") or "").strip()

    # Validate primary_event_type against canonical list
    raw_primary = data.get("primary_event_type", "")
    valid_map = {t.lower(): t for t in EVENT_TYPES}
    primary = (valid_map.get(raw_primary.strip().lower(), "Other")
               if isinstance(raw_primary, str) and raw_primary else "Other")

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

    # ── Deterministic grounding: verify against the text the model saw ──
    corpus = grounding.build_corpus(user_msg)
    allowed_names = tuple(
        n for n in (filing.title, filing.ticker, filing.form_type)
        if n
    )

    narrative_problems = (
        grounding.narrative_problems(headline, corpus, allowed_names)
        + grounding.narrative_problems(summary, corpus, allowed_names)
    )
    if narrative_problems:
        log.warning("Grounding REJECTED narrative for %s [%s]: %s",
                    filing.title, filing.form_type, "; ".join(narrative_problems))
        return facts_only_briefing(filing, spec)

    # Takeaway is the interpretive layer — an ungrounded fact there drops
    # only the takeaway, not the (verified) narrative.
    if takeaway and grounding.narrative_problems(takeaway, corpus, allowed_names):
        log.info("Dropped ungrounded investor_takeaway for %s", filing.title)
        takeaway = ""

    deal_terms, dropped_terms = grounding.verify_deal_terms(
        deal_terms, corpus, allowed_names)
    if dropped_terms:
        log.info("Dropped ungrounded deal terms for %s: %s",
                 filing.title, "; ".join(dropped_terms))

    catalysts, dropped_cats = grounding.verify_catalysts(catalysts, corpus)
    if dropped_cats:
        log.info("Dropped ungrounded catalysts for %s: %s",
                 filing.title, "; ".join(dropped_cats))

    # ── Independent LLM cross-check of the surviving narrative ──────────
    if not _llm_verify(headline, summary, user_msg):
        log.warning("Verifier pass REJECTED narrative for %s [%s]",
                    filing.title, filing.form_type)
        return facts_only_briefing(filing, spec)

    if not headline:
        headline = facts_only_briefing(filing, spec).headline

    log.info("Verified briefing generated for %s", filing.title)
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
        mode="llm_verified",
    )
