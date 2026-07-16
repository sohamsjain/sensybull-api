"""
materiality.py — LLM gate: does this press release merit an event?

A press release publishes only when the model confirms BOTH that the
release was issued by the company itself and that it reports a material
business development mapping to a canonical event type other than
"Other". There is no facts-only fallback (unlike 8-Ks, an unclassifiable
release has no deterministic structure to fall back on and cannot pass
the gate) — LLM failure means the release is retried next poll and
eventually dropped by the pipeline's attempt counter.

Reuses the Groq plumbing and validation from briefing.py; the prompt is
PR-specific.
"""

import logging
import re
from dataclasses import dataclass

from briefing import EVENT_TYPES, _chat_json, _validate_event_types
from models import Briefing

log = logging.getLogger(__name__)

_EVENT_TYPES_STR = ", ".join(f'"{t}"' for t in EVENT_TYPES)

_BODY_TEXT_CAP = 16_000
_MIN_BODY_CHARS = 400

# Promotional-content prefilter — matched against the HEADLINE only
# (bodies legitimately mention conferences etc.). Anything matching is
# dropped before spending an LLM call.
_PROMO_PATTERNS = [
    r"\bto (present|participate|speak|attend|exhibit|showcase)\b",
    r"\bfireside chat\b",
    r"\binvestor (conference|day|meeting)\b",
    r"\bconference (call|presentation|participation)\b",
    r"\bnamed (a |as )?(leader|winner|finalist|top)\b",
    r"\b(wins|receives|awarded|earns) .{0,40}\baward\b",
    r"\branked\b",
    r"\banniversary\b",
    r"\bcelebrates?\b",
    r"\bwebinar\b",
    r"\bwhite paper\b",
    r"\bcase study\b",
    r"\bpodcast\b",
    r"\btrade show\b",
    r"\bbooth\b",
]
_compiled_promo = [re.compile(p, re.IGNORECASE) for p in _PROMO_PATTERNS]


@dataclass
class Drop:
    """Terminal rejection of a release, with the reason for the drop logs."""
    reason: str


def prefilter_reason(headline: str, body_text: str) -> str | None:
    """Deterministic pre-LLM rejection: promo headline or too little text."""
    for pattern in _compiled_promo:
        if pattern.search(headline):
            return f"promo_headline:{pattern.pattern}"
    if len(body_text.strip()) < _MIN_BODY_CHARS:
        return "body_too_short"
    return None


_PR_SYSTEM_PROMPT = f"""\
You are a buyside special-situations analyst screening company press
releases. Most wire releases are noise — promotion, marketing, or
third parties talking about a company. Your job is to pass ONLY
material, first-party business developments, then brief them.

You are given the press release text and the name of the company whose
stock ticker the release references ("the subject company").

Produce a JSON object with these fields:

1. "issued_by_company" — true ONLY if this release was issued by the
   subject company itself about its own business. false if it was issued
   by a law firm, investor, fund, research shop, industry group, partner,
   or any other third party — even when the release is about the subject
   company. Law-firm shareholder alerts and class-action notices are
   ALWAYS false.

2. "material" — true ONLY if the release reports a development a
   portfolio manager would act on: M&A, a material agreement or contract,
   earnings or guidance, bankruptcy, financing or debt, restructuring,
   executive leadership change, delisting, restatement, cybersecurity
   incident, or a regulatory/clinical outcome (FDA decision, trial
   results, agency approval or rejection). Product marketing, awards,
   conference appearances, ESG reports, minor partnerships, and hiring
   below the C-suite are false.

3. "headline" — one short, plain-English sentence (max 100 chars) an
   everyday reader understands at a glance. Focus on WHAT is happening.

4. "summary" — 2-4 sentences from the subject company's perspective:
   what is happening, counterparties, key economics (amounts, share
   counts, prices), and current status. Flowing prose, not bullets.

5. "primary_event_type" — the single most investor-relevant label from:
   [{_EVENT_TYPES_STR}]

6. "event_types" — 1 to 3 labels from the same list (including the primary).

7. "deal_terms" — flat object of key-value pairs where stated:
   "counterparty", "deal_value", "share_count", "price_per_share",
   "premium", "consideration_type", "deal_status", "expected_close",
   "deal_type". Omit fields the text does not state.

8. "significance" — "High" = potential trade setup (M&A, bankruptcy,
   major regulatory/clinical outcome, material deal). "Medium" = notable
   but not immediately tradeable (leadership change, debt raise,
   earnings). "Low" = routine.

9. "sentiment" — "Positive", "Negative", "Neutral", or "Mixed".

10. "investor_takeaway" — one sentence: the "so what" for a PM.

11. "catalysts" — [{{"date": "YYYY-MM-DD" or null, "event": "..."}}] for
    upcoming dates stated in the release. Omit if none.

RULES:
- Use ONLY facts stated in the release text. No memory of the company,
  no extrapolation.
- When in doubt on "issued_by_company" or "material", answer false —
  a missed borderline release is cheaper than published spam.
- If the text is too thin to judge, respond with exactly
  {{"insufficient_content": true}} and no other fields.
Respond ONLY with valid JSON. No markdown, no commentary."""


def _build_user_message(headline: str, body_text: str, company_name: str,
                        ticker: str, published: str) -> str:
    body = body_text[:_BODY_TEXT_CAP]
    return (
        f"Subject company: {company_name}\n"
        f"Ticker: {ticker}\n"
        f"Published: {published}\n\n"
        f"--- Press release headline ---\n{headline}\n\n"
        f"--- Press release body ---\n{body}"
    )


def classify_release(headline: str, body_text: str, company_name: str,
                     ticker: str, published: str) -> Briefing | Drop:
    """One LLM pass: first-party + materiality gate, then the briefing.

    Returns a Briefing when the release passes, Drop(reason) on a clean
    rejection. LLM/transport failures raise to the caller (the pipeline
    retries next poll rather than dropping on a transient error).
    """
    messages = [
        {"role": "system", "content": _PR_SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(
            headline, body_text, company_name, ticker, published)},
    ]
    data = _chat_json(messages)

    if data.get("insufficient_content"):
        return Drop("llm_insufficient_content")
    if not data.get("issued_by_company"):
        return Drop("llm_not_first_party")
    if not data.get("material"):
        return Drop("llm_not_material")

    valid_map = {t.lower(): t for t in EVENT_TYPES}
    raw_primary = data.get("primary_event_type", "")
    primary = (valid_map.get(raw_primary.strip().lower(), "Other")
               if isinstance(raw_primary, str) and raw_primary else "Other")
    if primary == "Other":
        return Drop("llm_no_material_category")

    out_headline = str(data.get("headline") or "").strip() or headline[:100]
    summary = str(data.get("summary") or "").strip()
    takeaway = str(data.get("investor_takeaway") or "").strip()

    raw_terms = data.get("deal_terms", {})
    deal_terms = {
        str(k): str(v) for k, v in raw_terms.items()
        if isinstance(k, str) and v
    } if isinstance(raw_terms, dict) else {}

    _VALID_SIGNIFICANCE = {"high": "High", "medium": "Medium", "low": "Low"}
    raw_sig = data.get("significance", "")
    significance = _VALID_SIGNIFICANCE.get(
        raw_sig.strip().lower() if isinstance(raw_sig, str) else "", "Medium"
    )

    _VALID_SENTIMENT = {"positive": "Positive", "negative": "Negative",
                        "neutral": "Neutral", "mixed": "Mixed"}
    raw_sent = data.get("sentiment", "")
    sentiment = _VALID_SENTIMENT.get(
        raw_sent.strip().lower() if isinstance(raw_sent, str) else "", "Neutral"
    )

    raw_catalysts = data.get("catalysts", [])
    catalysts = []
    if isinstance(raw_catalysts, list):
        for cat in raw_catalysts:
            if isinstance(cat, dict) and cat.get("event"):
                catalysts.append({
                    "date": str(cat["date"]) if cat.get("date") else None,
                    "event": str(cat["event"]),
                })

    event_types = _validate_event_types(data.get("event_types", []))
    if primary not in event_types:
        event_types = ([primary] + event_types)[:3]

    return Briefing(
        headline=out_headline,
        summary=summary,
        primary_event_type=primary,
        deal_terms=deal_terms,
        significance=significance,
        sentiment=sentiment,
        investor_takeaway=takeaway,
        catalysts=catalysts,
        event_types=event_types,
        mode="llm",
    )
