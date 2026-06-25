"""
llm.py — Groq-backed interpretation of the computed numbers + thesis revision.

Given the deterministic ratios (from playbooks), the fundamentals snapshot, the
filing briefing, the company's prior thesis and recent filing history, produce:
  - a second-order ``insight`` with bull/bear points and caveats, and
  - a revised, self-contained ``thesis_narrative`` plus a ``thesis_change_summary``.

Hard rules baked into the prompt: stay **unbiased** (always give both sides),
ground every claim in the numbers/facts provided, be explicit about missing data,
and use **no buy/sell/hold/price-target language** (informational only — protects
the advisory line; see the web /disclaimer page).

Keys are loaded lazily so importing this module never crashes a process that has
no Groq key configured (web, tests). Mirrors the rotation in
``services/ingest/briefing.py``.
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

_key_cycle = None
_key_lock = threading.Lock()


def _load_api_keys() -> list[str]:
    raw = os.environ.get("GROQ_API_KEYS", "")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    if not keys:
        single = os.environ.get("GROQ_API_KEY", "").strip()
        if single:
            keys = [single]
    return keys


def _next_key() -> str:
    global _key_cycle
    with _key_lock:
        if _key_cycle is None:
            keys = _load_api_keys()
            if not keys:
                raise RuntimeError("No Groq API keys configured (GROQ_API_KEYS / GROQ_API_KEY).")
            _key_cycle = itertools.cycle(keys)
        return next(_key_cycle)


SYSTEM_PROMPT = """\
You are an equity research analyst writing an UNBIASED, second-order read on a \
company event. You are given (1) a plain-language briefing of an SEC filing, \
(2) deterministic financial ratios already computed from the company's latest \
reported financials, (3) a fundamentals snapshot (with a list of fields that \
were unavailable), (4) the company's prior thesis if one exists, and (5) recent \
filing history.

Your job: explain what this event means for the company in the context of its \
actual financials, then update the running thesis so it tells a coherent story \
over time.

Hard rules:
- Ground EVERY quantitative claim in the numbers provided. Do not invent figures. \
If a needed number is in the "missing" list, say the data was unavailable rather \
than guessing.
- Be genuinely balanced: give both bull and bear points. Never recommend an action.
- Do NOT use buy / sell / hold / overweight / price-target / "should invest" \
language. This is informational analysis, not investment advice.
- The thesis_narrative must be SELF-CONTAINED: someone reading only it should \
understand the current story. Build on the prior thesis — evolve it, don't \
discard it — and have thesis_change_summary state what this event changed.

Respond ONLY with valid JSON (no markdown) with exactly these keys:
{
  "insight": "2-4 sentence second-order interpretation grounded in the ratios",
  "bull_points": ["..."],
  "bear_points": ["..."],
  "confidence": "low" | "medium" | "high",
  "caveats": ["data gaps or assumptions, e.g. 'interest expense unavailable'"],
  "thesis_narrative": "the full updated thesis, self-contained, unbiased",
  "thesis_change_summary": "one sentence: what this event changed in the thesis",
  "thesis_points": {"bull": ["..."], "bear": ["..."], "uncertainties": ["..."]}
}
"""

_CONFIDENCE = {"low", "medium", "high"}


def _user_message(ctx: dict) -> str:
    return json.dumps({
        "company": {"name": ctx.get("company_name"), "ticker": ctx.get("ticker")},
        "event": ctx.get("event", {}),
        "playbook": ctx.get("playbook"),
        "computed_metrics": ctx.get("computed", []),
        "ratios": ctx.get("ratios", {}),
        "fundamentals": ctx.get("fundamentals", {}),
        "prior_thesis": ctx.get("prior_thesis"),
        "recent_history": ctx.get("history", []),
    }, default=str)


def _normalize(raw: dict) -> dict:
    def _list(x):
        return [str(i) for i in x] if isinstance(x, list) else ([str(x)] if x else [])

    conf = str(raw.get("confidence", "medium")).lower()
    points = raw.get("thesis_points") or {}
    return {
        "insight": str(raw.get("insight", "")).strip(),
        "bull_points": _list(raw.get("bull_points")),
        "bear_points": _list(raw.get("bear_points")),
        "confidence": conf if conf in _CONFIDENCE else "medium",
        "caveats": _list(raw.get("caveats")),
        "thesis_narrative": str(raw.get("thesis_narrative", "")).strip(),
        "thesis_change_summary": str(raw.get("thesis_change_summary", "")).strip(),
        "thesis_points": {
            "bull": _list(points.get("bull")) if isinstance(points, dict) else [],
            "bear": _list(points.get("bear")) if isinstance(points, dict) else [],
            "uncertainties": _list(points.get("uncertainties")) if isinstance(points, dict) else [],
        },
    }


def generate_analysis(ctx: dict) -> dict:
    """Call Groq to produce the insight + revised thesis. Raises on hard failure.

    The worker catches exceptions and falls back to the instant briefing, so any
    error here (no key, rate limit on all models, bad JSON) is recoverable.
    """
    from groq import Groq

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_message(ctx)},
    ]

    # Bound each attempt so a slow/stalled provider can't hold an alert
    # indefinitely (the "single combined message" waits on this call).
    timeout = float(os.environ.get("ANALYSIS_LLM_TIMEOUT", "45"))

    last_exc = None
    for model in _MODEL_CHAIN:
        try:
            client = Groq(api_key=_next_key(), timeout=timeout, max_retries=1)
            resp = client.chat.completions.create(
                model=model,
                max_tokens=1400,
                temperature=0.3,
                response_format={"type": "json_object"},
                messages=messages,
            )
            raw = json.loads(resp.choices[0].message.content)
            result = _normalize(raw)
            result["model"] = model
            return result
        except Exception as exc:  # noqa: BLE001 — try the next model, then fail to caller
            last_exc = exc
            log.warning("analysis LLM: model %s failed — %s", model, exc)
    raise RuntimeError(f"analysis LLM: all models failed ({last_exc})")
