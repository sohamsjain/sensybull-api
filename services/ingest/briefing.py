"""
briefing.py — LLM-powered filing briefing via Groq (Llama 3.3 70B).

Takes a Filing + raw exhibit HTML, produces a structured Briefing.
Text is transient — never stored, only used to build the LLM prompt.
"""

import json
import logging

from groq import Groq

from models import Briefing, Filing
from parser import strip_html

log = logging.getLogger(__name__)

_ITEM_TEXT_CAP = 8_000
_EXHIBIT_TEXT_CAP = 12_000
_TOTAL_TEXT_CAP = 40_000

_SYSTEM_PROMPT = (
    "You are a financial filing analyst. Given an SEC 8-K filing's item text "
    "and exhibit content, produce a concise briefing as a JSON object with "
    "exactly these keys:\n"
    '  "headline": a concise title of what happened (one line),\n'
    '  "bullets": an array of 2-3 factual bullet points stating what happened,\n'
    '  "company_context": one sentence of company context.\n'
    "Output valid JSON only, no markdown fences."
)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[...truncated]"


def _build_user_message(filing: Filing, exhibit_texts: dict[str, str]) -> str:
    parts: list[str] = []
    parts.append(f"Company: {filing.title}")
    if filing.ticker:
        parts.append(f"Ticker: {filing.ticker} ({filing.exchange})")
    parts.append(f"Filed: {filing.updated}")
    parts.append("")

    # Item text
    for item in filing.items:
        parts.append(f"--- Item {item.number}: {item.title} ({item.category}) ---")
        parts.append(_truncate(item.text, _ITEM_TEXT_CAP))
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


def generate_briefing(filing: Filing, exhibit_texts: dict[str, str]) -> Briefing:
    """Generate a Briefing from filing data + exhibit HTML. Returns fallback on failure."""
    fallback = Briefing(headline=filing.title, bullets=[], company_context="")
    try:
        user_msg = _build_user_message(filing, exhibit_texts)
        client = Groq()
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            max_tokens=512,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        return Briefing(
            headline=data.get("headline", filing.title),
            bullets=data.get("bullets", [])[:3],
            company_context=data.get("company_context", ""),
        )
    except Exception as exc:
        log.warning("Briefing generation failed for %s: %s", filing.title, exc)
        return fallback
