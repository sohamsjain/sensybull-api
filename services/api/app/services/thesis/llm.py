# services/api/app/services/thesis/llm.py
"""
Groq-backed thesis evaluators and the thesis drafting assistant.

Two-stage judgment:
- `assess_thesis` (triage): cheap model over the briefing. Runs for every
  filing × held thesis. Its only job is to decide "does this even bear on
  the thesis?" — non-neutral verdicts escalate to the deep pass.
- `assess_thesis_deep`: a larger model over the FULL filing text, the
  measured price reaction, position direction/size, and the structured
  thesis. Returns per-assumption verdicts, a confidence score, and verbatim
  citations from the filing. Only runs on triage escalations, so cost stays
  proportional to signal.

Plus `draft_thesis`: turns an investor's raw notes into a structured,
falsifiable thesis (core claim, assumptions, kill criteria, horizon).

Design constraints (unchanged from the single-stage version):
- NEVER raise into the caller. A failed or unconfigured LLM returns None,
  so a held company still gets its event and the engine simply records no
  assessment (or falls back to the triage verdict). Thesis analysis is
  additive; it must never block ingestion.
- Lazy client construction: the `groq` package and API keys are only
  touched when a call is actually made, so the API boots fine without either.
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


def _deep_model_chain() -> list[str]:
    """Deep-pass model, overridable per deploy; triage chain as last resort.

    Default is Groq's recommended replacement after llama-3.3-70b-versatile
    was deprecated (June 2026).
    """
    primary = os.environ.get("THESIS_DEEP_MODEL", "openai/gpt-oss-120b")
    return [primary, *_MODEL_CHAIN]


_VALID_IMPACT = {"supports", "neutral", "threatens", "breaks"}

# Full filing text handed to the deep pass is capped to stay well inside
# model context while covering essentially every 8-K.
FILING_TEXT_CAP = 24_000

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

_DEEP_SYSTEM_PROMPT = """\
You are a senior buyside analyst doing a rigorous review. An investor holds a
position with a written THESIS, decomposed into a core claim and numbered,
falsifiable ASSUMPTIONS. A new SEC filing arrived for the company; a first-pass
screen flagged it as potentially material to this thesis. You now have the
FULL FILING TEXT and, when available, the market's measured PRICE REACTION.

Judge the filing against the thesis, assumption by assumption.

Respond ONLY with a JSON object:
{
  "impact": one of "supports" | "neutral" | "threatens" | "breaks",
  "confidence": number in [0,1] — how sure you are of the impact verdict,
  "rationale": 1-3 plain-English sentences (max 500 chars) explaining the verdict,
  "assumption_verdicts": [
    {"index": <assumption number, starting at 1>,
     "impact": "supports" | "neutral" | "threatens" | "breaks",
     "rationale": one sentence (max 220 chars)}
  ],
  "citations": [up to 3 short verbatim quotes from the filing text (max 300
                chars each) that ground your verdict]
}

Rules:
- "breaks" only when the filing directly contradicts the core claim or trips a
  stated kill criterion. "threatens": materially challenges without invalidating.
- The overall impact should follow from the assumption verdicts: if a
  load-bearing assumption breaks, the thesis breaks.
- Include one entry in assumption_verdicts for EVERY numbered assumption.
  If the thesis has no numbered assumptions, return an empty list.
- Citations must be verbatim substrings of the filing text. Never fabricate.
- Interpret relative to the position DIRECTION. For a SHORT thesis, a bullish
  development threatens/breaks it; for a LONG thesis, a bearish one does.
- The price reaction is evidence of how the market read the filing — weigh it,
  but your job is the thesis, not the tape. A big move against the thesis
  direction should lower your bar for "threatens".
- Judge only against the thesis as written. Do not invent facts.
"""

_DRAFT_SYSTEM_PROMPT = """\
You are an investment-writing coach. An investor gives you their raw,
unstructured reason for holding (or shorting) a company. Restructure it into
a falsifiable thesis. Do NOT invent views they did not express — sharpen what
is there. Where their reasoning implies an assumption they did not state
explicitly, you may surface it, phrased so they would recognize it as their own.

Respond ONLY with a JSON object:
{
  "core_claim": one sentence (max 300 chars) — the single claim the position
                stands or falls on,
  "assumptions": [2-5 short falsifiable statements (max 300 chars each) that
                  must hold for the core claim to be true],
  "kill_criteria": [1-3 concrete observable events (max 300 chars each) that
                    would prove the thesis wrong — the investor's exit tripwires],
  "horizon": a short time horizon like "6-12 months" if stated or clearly
             implied, else null
}
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


def _chat_json(model_chain: list[str], messages: list[dict],
               max_tokens: int) -> tuple[dict, str] | None:
    """One JSON-mode chat completion with model fallback. Never raises."""
    try:
        from groq import Groq
    except ImportError:
        log.debug("thesis.llm: groq package not installed — skipping call")
        return None

    for model in model_chain:
        key = _next_api_key()
        if key is None:
            log.debug("thesis.llm: no Groq API key configured — skipping call")
            return None
        try:
            client = Groq(api_key=key)
            resp = client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=messages,
            )
            return json.loads(resp.choices[0].message.content), model
        except Exception as exc:  # noqa: BLE001 — never propagate
            # Any failure — rate limit, decommissioned/unknown model, bad
            # params, malformed JSON — warrants trying the next model in the
            # chain rather than giving up. Only the last model's failure
            # surfaces as None.
            if model != model_chain[-1]:
                log.warning("thesis.llm: %s failed (%s), falling back", model, exc)
                continue
            log.warning("thesis.llm: call failed on %s: %s", model, exc)
            return None
    return None


# ── Triage pass ───────────────────────────────────────────────────────────

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
    """Triage verdict: {"impact", "rationale", "model"} or None on failure."""
    if not thesis or not thesis.strip():
        return None

    result = _chat_json(_MODEL_CHAIN, [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_message(thesis, direction, event)},
    ], max_tokens=300)
    if result is None:
        return None
    data, model = result

    impact = str(data.get("impact", "")).strip().lower()
    if impact not in _VALID_IMPACT:
        log.warning("thesis.llm: invalid impact %r — treating as neutral", impact)
        impact = "neutral"
    rationale = str(data.get("rationale", "") or "")[:500]
    return {"impact": impact, "rationale": rationale, "model": model}


# ── Deep pass ─────────────────────────────────────────────────────────────

def _format_structured_thesis(thesis: str, structured: dict | None) -> str:
    """Render the thesis block for the deep prompt, numbered assumptions first."""
    if not structured:
        return f"THESIS (free-form, no numbered assumptions):\n{thesis}"
    lines = [f"CORE CLAIM: {structured.get('core_claim') or thesis}"]
    assumptions = structured.get("assumptions") or []
    if assumptions:
        lines.append("ASSUMPTIONS:")
        lines.extend(f"  {i}. {a}" for i, a in enumerate(assumptions, start=1))
    kill = structured.get("kill_criteria") or []
    if kill:
        lines.append("KILL CRITERIA (would prove the thesis wrong):")
        lines.extend(f"  - {k}" for k in kill)
    if structured.get("horizon"):
        lines.append(f"HORIZON: {structured['horizon']}")
    return "\n".join(lines)


def _format_position(direction: str, shares, cost_basis) -> str:
    parts = [f"direction={direction}"]
    if shares is not None:
        parts.append(f"shares={shares}")
    if cost_basis is not None:
        parts.append(f"cost_basis={cost_basis}")
    return ", ".join(parts)


def _build_deep_user_message(thesis: str, structured: dict | None,
                             direction: str, shares, cost_basis,
                             event: dict, filing_text: str,
                             price_summary: str | None) -> str:
    briefing = event.get("briefing") or {}
    lines = [
        f"POSITION: {_format_position(direction, shares, cost_basis)}",
        "",
        _format_structured_thesis(thesis, structured),
        "",
        "NEW FILING:",
        f"Company: {event.get('company_name')} ({event.get('ticker') or '—'})",
        f"Form: {event.get('signal_type')}",
        f"Headline: {briefing.get('headline') or ''}",
        f"Measured price reaction since filing: {price_summary or 'not yet available'}",
        "",
        "FULL FILING TEXT:",
        (filing_text or "")[:FILING_TEXT_CAP],
    ]
    return "\n".join(lines)


def _clean_assumption_verdicts(raw, n_assumptions: int) -> list[dict]:
    verdicts = []
    if not isinstance(raw, list):
        return verdicts
    for item in raw[:10]:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        impact = str(item.get("impact", "")).strip().lower()
        if impact not in _VALID_IMPACT:
            continue
        if n_assumptions and not (1 <= index <= n_assumptions):
            continue
        verdicts.append({
            "index": index,
            "impact": impact,
            "rationale": str(item.get("rationale", "") or "")[:500],
        })
    return verdicts


def _clean_citations(raw, filing_text: str) -> list[str]:
    citations = []
    if not isinstance(raw, list):
        return citations
    haystack = (filing_text or "")[:FILING_TEXT_CAP]
    for item in raw[:3]:
        quote = str(item or "").strip()[:300]
        # Keep only quotes that actually appear in the filing — the prompt
        # demands verbatim substrings, and fabricated citations are worse
        # than none.
        if quote and quote in haystack:
            citations.append(quote)
    return citations


def assess_thesis_deep(thesis: str, structured: dict | None, direction: str,
                       shares, cost_basis, event: dict, filing_text: str,
                       price_summary: str | None) -> dict | None:
    """Deep verdict with per-assumption analysis, confidence, and citations.

    Returns {"impact", "confidence", "rationale", "assumption_verdicts",
    "citations", "model"} or None on failure — the caller falls back to the
    triage verdict.
    """
    if not thesis or not thesis.strip():
        return None

    result = _chat_json(_deep_model_chain(), [
        {"role": "system", "content": _DEEP_SYSTEM_PROMPT},
        {"role": "user", "content": _build_deep_user_message(
            thesis, structured, direction, shares, cost_basis,
            event, filing_text, price_summary)},
    ], max_tokens=1200)
    if result is None:
        return None
    data, model = result

    impact = str(data.get("impact", "")).strip().lower()
    if impact not in _VALID_IMPACT:
        log.warning("thesis.llm: deep pass invalid impact %r — discarding", impact)
        return None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence"))))
    except (TypeError, ValueError):
        confidence = None
    n_assumptions = len((structured or {}).get("assumptions") or [])
    return {
        "impact": impact,
        "confidence": confidence,
        "rationale": str(data.get("rationale", "") or "")[:1000],
        "assumption_verdicts": _clean_assumption_verdicts(
            data.get("assumption_verdicts"), n_assumptions),
        "citations": _clean_citations(data.get("citations"), filing_text),
        "model": model,
    }


# ── Thesis drafting assistant ─────────────────────────────────────────────

def draft_thesis(raw_text: str, company_name: str | None = None,
                 ticker: str | None = None,
                 direction: str = "long") -> dict | None:
    """Structure raw investor notes into a falsifiable thesis draft.

    Returns {"core_claim", "assumptions", "kill_criteria", "horizon", "model"}
    or None on failure/unconfigured.
    """
    if not raw_text or not raw_text.strip():
        return None

    company_line = ""
    if company_name or ticker:
        company_line = f"COMPANY: {company_name or ''} ({ticker or '—'})\n"
    user_message = (
        f"{company_line}POSITION DIRECTION: {direction}\n\n"
        f"RAW NOTES:\n{raw_text.strip()[:5000]}"
    )

    result = _chat_json(_deep_model_chain(), [
        {"role": "system", "content": _DRAFT_SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ], max_tokens=800)
    if result is None:
        return None
    data, model = result

    core_claim = str(data.get("core_claim", "") or "").strip()[:300]
    if not core_claim:
        return None
    assumptions = [str(a).strip()[:300] for a in (data.get("assumptions") or [])
                   if str(a).strip()][:5]
    kill_criteria = [str(k).strip()[:300] for k in (data.get("kill_criteria") or [])
                     if str(k).strip()][:3]
    horizon = data.get("horizon")
    horizon = str(horizon).strip()[:100] if horizon else None
    return {
        "core_claim": core_claim,
        "assumptions": assumptions,
        "kill_criteria": kill_criteria,
        "horizon": horizon,
        "model": model,
    }
