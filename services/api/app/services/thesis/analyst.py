# services/api/app/services/thesis/analyst.py
"""
Interactive per-position analyst.

A tool-using LLM loop the investor can talk to about one position: "what's
the strongest evidence against my thesis this quarter?", "what would have
to be true for this to break?". The model has read-only tools over the data
the platform already holds for the company — recent filings, full filing
text, measured price reactions, and past thesis assessments — plus the
thesis itself in its system prompt.

The route owns auth (position ownership); this module owns the loop.
Same defensive posture as the rest of the thesis service: never raise into
the caller — a failure returns None and the route maps it to 503.
"""
import json
import logging

from app.services.thesis.llm import (
    FILING_TEXT_CAP,
    _deep_model_chain,
    _format_structured_thesis,
    _next_api_key,
)

log = logging.getLogger(__name__)

MAX_ROUNDS = 5          # tool-call rounds before the model must answer
MAX_MESSAGES = 24       # client-supplied history cap
MAX_MESSAGE_CHARS = 4_000

_SYSTEM_PROMPT = """\
You are the investor's dedicated analyst for ONE position. You are rigorous,
evidence-first, and adversarial when asked to be — your job is to stress the
thesis, not to comfort its owner. Ground every claim in the filings, price
reactions, and assessments available through your tools; when the evidence is
thin, say so plainly. Never invent filings, numbers, or quotes.

Keep answers tight: lead with the conclusion, then the supporting evidence
with dates and forms (e.g. "8-K, 2026-06-12"). Plain text only — no markdown
headers or tables.

THE POSITION:
{position_block}

THE THESIS:
{thesis_block}
"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_recent_filings",
            "description": "Recent SEC filings for the company, newest first: "
                           "id, date, form, headline, summary, event types, "
                           "significance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer",
                              "description": "How many filings (max 20)."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_filing_text",
            "description": "Full parsed text of one filing, by filing id from "
                           "list_recent_filings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filing_event_id": {"type": "string"},
                },
                "required": ["filing_event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_reactions",
            "description": "Measured price moves after one filing (5m to 1w), "
                           "by filing id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filing_event_id": {"type": "string"},
                },
                "required": ["filing_event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_past_assessments",
            "description": "Past verdicts of filings judged against THIS thesis "
                           "(impact, rationale, confidence, per-assumption "
                           "verdicts), newest first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer",
                              "description": "How many assessments (max 20)."},
                },
            },
        },
    },
]


# ── Tool implementations (read-only, scoped to the position's company) ────

def _tool_list_recent_filings(position, args: dict) -> list[dict]:
    from app.models.filing_event import FilingEvent

    limit = min(int(args.get("limit") or 10), 20)
    events = (
        FilingEvent.query
        .filter(FilingEvent.company_id == position.company_id)
        .order_by(FilingEvent.filing_date.desc())
        .limit(limit)
        .all()
    )
    out = []
    for e in events:
        briefing = e.briefing_json or {}
        out.append({
            "id": e.id,
            "date": e.filing_date.date().isoformat() if e.filing_date else None,
            "form": e.signal_type,
            "headline": briefing.get("headline"),
            "summary": briefing.get("summary"),
            "event_types": e.event_types_json or [],
            "significance": briefing.get("significance"),
        })
    return out


def _get_company_event(position, filing_event_id: str):
    """Fetch one filing, refusing ids outside this position's company."""
    from app import db
    from app.models.filing_event import FilingEvent

    event = db.session.get(FilingEvent, str(filing_event_id))
    if event is None or event.company_id != position.company_id:
        return None
    return event


def _tool_get_filing_text(position, args: dict) -> dict:
    from app.services.thesis.engine import _filing_text

    event = _get_company_event(position, args.get("filing_event_id"))
    if event is None:
        return {"error": "unknown filing_event_id for this position's company"}
    text = _filing_text(event)
    return {
        "id": event.id,
        "form": event.signal_type,
        "date": event.filing_date.date().isoformat() if event.filing_date else None,
        "text": text[:FILING_TEXT_CAP] or "(no parsed text stored)",
    }


def _tool_get_price_reactions(position, args: dict) -> dict:
    event = _get_company_event(position, args.get("filing_event_id"))
    if event is None:
        return {"error": "unknown filing_event_id for this position's company"}
    return {
        "id": event.id,
        "reactions": [
            {"interval": r.interval, "pct_change": r.pct_change,
             "explosive": r.is_explosive}
            for r in event.price_reactions
            if r.status == "done" and r.pct_change is not None
        ],
    }


def _tool_get_past_assessments(position, args: dict) -> list[dict]:
    from app.models.thesis_assessment import ThesisAssessment

    limit = min(int(args.get("limit") or 10), 20)
    rows = (
        ThesisAssessment.query
        .filter_by(position_id=position.id)
        .order_by(ThesisAssessment.created_at.desc())
        .limit(limit)
        .all()
    )
    return [{
        "filing_event_id": a.filing_event_id,
        "impact": a.impact,
        "rationale": a.rationale,
        "confidence": a.confidence,
        "assumption_verdicts": a.assumption_verdicts_json or [],
        "retroactive": a.retroactive,
        "date": a.created_at.date().isoformat() if a.created_at else None,
    } for a in rows]


_TOOL_IMPLS = {
    "list_recent_filings": _tool_list_recent_filings,
    "get_filing_text": _tool_get_filing_text,
    "get_price_reactions": _tool_get_price_reactions,
    "get_past_assessments": _tool_get_past_assessments,
}


def _run_tool(position, name: str, arguments: str) -> str:
    impl = _TOOL_IMPLS.get(name)
    if impl is None:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        args = json.loads(arguments or "{}")
        if not isinstance(args, dict):
            args = {}
        return json.dumps(impl(position, args), default=str)
    except Exception as exc:  # noqa: BLE001 — surface the error to the model
        log.warning("thesis.analyst: tool %s failed: %s", name, exc)
        return json.dumps({"error": "tool execution failed"})


# ── The loop ──────────────────────────────────────────────────────────────

def _system_prompt(position) -> str:
    company = position.company
    position_block = (
        f"Company: {company.name} ({company.ticker or '—'})\n"
        f"Direction: {position.direction}"
    )
    if position.shares is not None:
        position_block += f"\nShares: {position.shares}"
    if position.cost_basis is not None:
        position_block += f"\nCost basis: {position.cost_basis}"
    position_block += f"\nThesis status: {position.thesis_status}"

    thesis_block = _format_structured_thesis(
        position.thesis or "(none written)", position.thesis_structured)
    return _SYSTEM_PROMPT.format(position_block=position_block,
                                 thesis_block=thesis_block)


def _clean_history(messages: list) -> list[dict]:
    """Validate client-supplied history down to plain user/assistant turns."""
    cleaned = []
    for m in (messages or [])[-MAX_MESSAGES:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content[:MAX_MESSAGE_CHARS]})
    return cleaned


def run_analyst(position, messages: list) -> dict | None:
    """Answer the latest user message with tool access. None on failure."""
    history = _clean_history(messages)
    if not history or history[-1]["role"] != "user":
        return None

    try:
        from groq import Groq
    except ImportError:
        return None
    key = _next_api_key()
    if key is None:
        return None
    client = Groq(api_key=key)

    convo = [{"role": "system", "content": _system_prompt(position)}, *history]
    model_chain = _deep_model_chain()

    for model in model_chain:
        try:
            return _loop(client, model, position, list(convo))
        except Exception as exc:  # noqa: BLE001 — try the next model
            status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
            if model != model_chain[-1] and status in (404, 429):
                log.warning("thesis.analyst: %s on %s, falling back", status, model)
                continue
            log.warning("thesis.analyst: failed on %s: %s", model, exc)
            return None
    return None


def _loop(client, model: str, position, convo: list[dict]) -> dict:
    tools_used: list[str] = []
    for round_no in range(MAX_ROUNDS + 1):
        # On the last round, withhold tools so the model must answer.
        allow_tools = round_no < MAX_ROUNDS
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1200,
            messages=convo,
            tools=_TOOLS if allow_tools else None,
            tool_choice="auto" if allow_tools else None,
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            return {
                "reply": (msg.content or "").strip(),
                "model": model,
                "tools_used": tools_used,
            }
        convo.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name,
                              "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            tools_used.append(tc.function.name)
            convo.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _run_tool(position, tc.function.name,
                                     tc.function.arguments),
            })
    # MAX_ROUNDS exhausted without a final answer (shouldn't happen — the
    # final round runs without tools).
    return {"reply": "", "model": model, "tools_used": tools_used}
