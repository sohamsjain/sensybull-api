# services/api/app/services/thesis/llm.py
"""
Groq-backed thesis evaluator.

Given an investor's thesis and a filing event's briefing, judge what the
event does to the thesis: supports / neutral / threatens / breaks, plus a
one-sentence rationale grounded in the filing.

Design constraints:
- NEVER raise into the caller. A failed or unconfigured LLM returns None,
  so a held company still gets its event and the engine simply records no
  assessment. Thesis analysis is additive; it must never block ingestion.
- Lazy client construction: the `groq` package and API keys are only
  touched when an assessment is actually requested, so the API boots fine
  without either.
"""
import itertools
import json
import logging
import os
import threading

log = logging.getLogger(__name__)

_MODEL_CHAIN = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant",
]

_VALID_IMPACT = {"supports", "neutral", "threatens", "breaks"}

_SYSTEM_PROMPT = """\
You are a portfolio risk analyst. An investor holds a position in a company
and has written down their THESIS — the reason they hold it. A new SEC filing
just arrived for that company. Judge what this filing does to the thesis.

Respond ONLY with a JSON object:
{
  "impact": one of "supports" | "neutral" | "threatens" | "breaks",
  "rationale": one plain-English sentence (max 220 chars) citing the specific
               fact in the filing that drives your verdict.
}

Rules:
- "breaks": the filing directly contradicts the core claim of the thesis.
- "threatens": the filing materially challenges the thesis but doesn't
   fully invalidate it; the investor should review.
- "supports": the filing reinforces the thesis.
- "neutral": the filing has no real bearing on the thesis.
- Interpret relative to the position DIRECTION. For a SHORT thesis, a
  bullish development threatens/breaks it; for a LONG thesis, a bearish one does.
- Judge only against the thesis as written. Do not invent facts not in the filing.
"""

# ── Groq key rotation (mirrors services/ingest/briefing.py) ──────────────
_key_cycle = None
_key_lock = threading.Lock()


def _load_api_keys() -> list[str]:
    raw = os.environ.get("GROQ_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("GROQ_API_KEY", "")
        if single.strip():
            keys = [single.strip()]
    return keys


def _next_api_key() -> str | None:
    global _key_cycle
    with _key_lock:
        if _key_cycle is None:
            keys = _load_api_keys()
            if not keys:
                return None
            _key_cycle = itertools.cycle(keys)
        return next(_key_cycle)


def is_configured() -> bool:
    """True if at least one Groq key is available."""
    return bool(_load_api_keys())


def _build_user_message(thesis: str, direction: str, event: dict) -> str:
    briefing = event.get("briefing") or {}
    lines = [
        f"POSITION DIRECTION: {direction}",
        f"THESIS: {thesis}",
        "",
        "NEW FILING:",
        f"Company: {event.get('company_name')} ({event.get('ticker') or '—'})",
        f"Form: {event.get('signal_type')}",
        f"Headline: {briefing.get('headline') or ''}",
        f"Summary: {briefing.get('summary') or ''}",
        f"Event types: {', '.join(event.get('event_types') or []) or '—'}",
        f"Significance: {briefing.get('significance') or '—'}",
        f"Sentiment: {briefing.get('sentiment') or '—'}",
        f"Investor takeaway: {briefing.get('investor_takeaway') or '—'}",
    ]
    return "\n".join(lines)


def assess_thesis(thesis: str, direction: str, event: dict) -> dict | None:
    """Return {"impact", "rationale", "model"} or None on failure/unconfigured."""
    if not thesis or not thesis.strip():
        return None

    try:
        from groq import Groq
    except ImportError:
        log.debug("thesis.llm: groq package not installed — skipping assessment")
        return None

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(thesis, direction, event)},
    ]

    for model in _MODEL_CHAIN:
        key = _next_api_key()
        if key is None:
            log.debug("thesis.llm: no Groq API key configured — skipping assessment")
            return None
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model=model,
                max_tokens=300,
                response_format={"type": "json_object"},
                messages=messages,
            )
            data = json.loads(resp.choices[0].message.content)
            impact = str(data.get("impact", "")).strip().lower()
            if impact not in _VALID_IMPACT:
                log.warning("thesis.llm: invalid impact %r — treating as neutral", impact)
                impact = "neutral"
            rationale = str(data.get("rationale", "") or "")[:500]
            return {"impact": impact, "rationale": rationale, "model": model}
        except Exception as exc:  # noqa: BLE001 — never propagate
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if status == 429 and model != _MODEL_CHAIN[-1]:
                log.warning("thesis.llm: rate-limited on %s, falling back", model)
                continue
            log.warning("thesis.llm: assessment failed on %s: %s", model, exc)
            return None
    return None
