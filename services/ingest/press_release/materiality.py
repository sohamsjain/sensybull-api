"""
materiality.py — LLM pass: first-party check + briefing for a press release.

Every first-party release from a US-listed company publishes (Oct 2026:
"keep every PR for now" — the materiality gate and the promo-headline
prefilter were removed). The model still decides `issued_by_company`, the
backstop behind the law-firm blocklist and issuer check, and writes the
summary, event types, significance and sentiment. There is no facts-only
fallback: LLM failure means the release is retried and eventually dropped
by the pipeline's attempt counter.

Reuses the Groq plumbing and validation from briefing.py; the prompt is
PR-specific. (The module keeps its name so imports and history line up.)
"""

import logging
from dataclasses import dataclass

from briefing import (
    EVENT_TYPES,
    VOICE_RULES,
    _chat_json,
    _coerce_deal_terms,
    _validate_event_types,
)
from models import Briefing

log = logging.getLogger(__name__)

_EVENT_TYPES_STR = ", ".join(f'"{t}"' for t in EVENT_TYPES)

# Bounded by the same per-minute token ceiling as the 8-K prompt (see
# briefing._TOTAL_TEXT_CAP): system prompt + body + completion budget has to
# fit inside one request, and a release long enough to hit this cap has
# said everything material well before it.
_BODY_TEXT_CAP = 12_000
# Only reachable when the article fetch failed and FMP's teaser is all we
# have. Measured over headline + body: Business Wire and GlobeNewswire
# teasers are often a single ~150-char sentence ("Q3 shipments 1.3 million
# units, down 1%"), and with the headline that is still enough to brief —
# the LLM answers insufficient_content when it isn't.
_MIN_BODY_CHARS = 200


@dataclass
class Drop:
    """Terminal rejection of a release, with the reason for the drop logs."""
    reason: str


def prefilter_reason(headline: str, body_text: str) -> str | None:
    """Deterministic pre-LLM rejection: too little text to brief."""
    if len(headline.strip()) + len(body_text.strip()) < _MIN_BODY_CHARS:
        return "body_too_short"
    return None


_PR_SYSTEM_PROMPT = f"""\
You are a buyside analyst briefing company press releases. Some wire
releases are third parties talking about a company rather than the
company itself; flag those, then brief the release.

You are given the press release text and the name of the company whose
stock ticker the release references ("the subject company").

Produce a JSON object with these fields:

1. "issued_by_company" — true ONLY if this release was issued by the
   subject company itself about its own business. false if it was issued
   by a law firm, investor, fund, research shop, industry group, partner,
   or any other third party — even when the release is about the subject
   company. Law-firm shareholder alerts and class-action notices are
   ALWAYS false.

2. "summary" — 2-4 sentences about the subject company: what is
   happening, counterparties, key economics (amounts, share counts,
   prices), and current status. Flowing prose, not bullets.

3. "primary_event_type" — the single most investor-relevant label from:
   [{_EVENT_TYPES_STR}]

4. "event_types" — 1 to 3 labels from the same list (including the primary).

5. "deal_terms" — flat object of key-value pairs where stated:
   "counterparty", "deal_value", "share_count", "price_per_share",
   "premium", "consideration_type", "deal_status", "expected_close",
   "deal_type". Omit fields the text does not state.
   Every value MUST be a plain, display-ready string — never a nested
   object, array, or expression. If a figure is a total you had to add
   up, write the total itself ("$11.5B"), not the arithmetic.
   Good: "deal_value": "$11.5B"
   Bad:  "deal_value": {{"$sum": "11500000000"}}
   Write word values in Title Case ("Definitive Agreement Signed",
   "Cash", "SPAC Merger"), keeping acronyms and proper names as they
   are spelled. Figures stay as written ("$11.5B", "45%").

6. "significance" — "High" = potential trade setup (M&A, bankruptcy,
   major regulatory/clinical outcome, material deal). "Medium" = notable
   but not immediately tradeable (leadership change, debt raise,
   earnings). "Low" = routine.

7. "sentiment" — "Positive", "Negative", "Neutral", or "Mixed".

8. "investor_takeaway" — one sentence: the "so what" for a PM.

9. "catalysts" — [{{"date": "YYYY-MM-DD" or null, "event": "..."}}] for
   upcoming dates stated in the release. Omit if none.

{VOICE_RULES}

RULES:
- Use ONLY facts stated in the release text. No memory of the company,
  no extrapolation.
- When in doubt on "issued_by_company", answer false — a missed
  borderline release is cheaper than published spam.
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
        f"--- Press release headline (the company's own words) ---\n{headline}\n\n"
        f"--- Press release body (the company's own words) ---\n{body}"
    )


def classify_release(headline: str, body_text: str, company_name: str,
                     ticker: str, published: str) -> Briefing | Drop:
    """One LLM pass: first-party check, then the briefing.

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

    valid_map = {t.lower(): t for t in EVENT_TYPES}
    raw_primary = data.get("primary_event_type", "")
    primary = (valid_map.get(raw_primary.strip().lower(), "Other")
               if isinstance(raw_primary, str) and raw_primary else "Other")

    # The release's own title is the event headline (no LLM rewrite)
    out_headline = headline
    summary = str(data.get("summary") or "").strip()
    takeaway = str(data.get("investor_takeaway") or "").strip()

    deal_terms = _coerce_deal_terms(data.get("deal_terms", {}))

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
