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
    "Other",
]

_EVENT_TYPES_STR = ", ".join(f'"{t}"' for t in EVENT_TYPES)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[...truncated]"


def _build_user_message(filing: Filing, exhibit_texts: dict[str, str]) -> str:
    parts: list[str] = []
    parts.append(f"Company: {filing.title}")
    if filing.ticker:
        parts.append(f"Ticker: {filing.ticker}")
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
    fallback = Briefing(
        headline=filing.title, bullets=[], company_context="",
        event_types=["Other"],
    )
    user_msg = _build_user_message(filing, exhibit_texts)
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    for model in _MODEL_CHAIN:
        try:
            client = Groq(api_key=_next_api_key())
            response = client.chat.completions.create(
                model=model,
                max_tokens=512,
                response_format={"type": "json_object"},
                messages=messages,
            )
            raw = response.choices[0].message.content
            data = json.loads(raw)
            log.info("Briefing generated via %s for %s", model, filing.title)
            return Briefing(
                headline=data.get("headline", filing.title),
                bullets=data.get("bullets", [])[:3],
                company_context=data.get("company_context", ""),
                event_types=_validate_event_types(data.get("event_types", [])),
            )
        except Exception as exc:
            if _is_rate_limit(exc) and model != _MODEL_CHAIN[-1]:
                log.warning("Rate-limited on %s, falling back for %s", model, filing.title)
                continue
            log.warning("Briefing generation failed on %s for %s: %s", model, filing.title, exc)
            return fallback

    return fallback
