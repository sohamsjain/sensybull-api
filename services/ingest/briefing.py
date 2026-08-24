"""
briefing.py — LLM-powered filing briefing + event classification via Groq.

Takes a Filing + raw exhibit HTML and produces a structured Briefing in a
single LLM pass: the model is shown the filing text and asked for a
headline, summary, classification, and key dates. Prompt guidance tells it
to stick to the filing text.

Classification runs against the three-tier taxonomy in taxonomy.py: the
model names the specific leaf event ("ceo_departure", "covenant_violation")
and we collapse that to the ONE simple category the end user sees. The leaf
is kept on the briefing for analytics; it is never displayed.

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

import taxonomy
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
# Sized against the per-minute token ceiling of the models we call (8K TPM
# on the current Groq tier). A request that alone exceeds TPM is rejected
# outright, so the whole prompt has to fit, at ~3.8 chars/token:
#
#   ~2.6K  system prompt (carries the taxonomy leaf list — taxonomy.PROMPT_BLOCK)
#   ~2.6K  this cap
#    2.0K  the completion budget below
#   ------
#   ~7.3K  of the 8K ceiling
#
# It was 16K chars before the taxonomy joined the prompt; the leaf list is
# worth the source text it costs, since classification is what the whole
# product surface is built on and typical 8-K item text lands well under
# this cap. Raising it makes the largest filings 429 on every model in the
# chain and publish facts-only.
_TOTAL_TEXT_CAP = 10_000

# Completion budget per call. Reasoning models bill their thinking against
# this budget, so it sits well above the ~600 tokens the JSON answer needs;
# too low and the JSON comes back truncated.
_MAX_COMPLETION_TOKENS = 2_048

# The LLM is only called when at least this much substantive filing text
# (item bodies + exhibit bodies) exists to summarize. Below this there is
# nothing to say — skip the call and publish facts-only.
_MIN_SOURCE_CHARS = 200

# Model fallback chain: try the best model first, then degrade to the next
# on errors that are specific to a single model (rate limits, a model that
# Groq has decommissioned / that the key can't access, or a model that
# answers with nothing usable). Overridable via GROQ_MODELS (comma-separated,
# best-first) so a model retirement can be worked around by config without a
# redeploy.
#
# August 2026: Groq decommissioned the Llama 3.x chat models. Both
# llama-3.3-70b-versatile and llama-3.1-8b-instant now answer 404
# model_not_found, so the chain was exhausted on every filing and every
# briefing published facts-only. The chain is now the GPT-OSS pair Groq
# points to as the replacement, with Qwen behind them; all three serve
# JSON mode.
_DEFAULT_MODEL_CHAIN = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
]


def _load_model_chain() -> list[str]:
    raw = os.environ.get("GROQ_MODELS", "")
    models = [m.strip() for m in raw.split(",") if m.strip()]
    if models:
        log.info("Groq model chain from GROQ_MODELS: %s", ", ".join(models))
        return models
    return list(_DEFAULT_MODEL_CHAIN)

_MODEL_CHAIN = _load_model_chain()

# Extra request fields per model. The GPT-OSS models reason before they
# answer, and that thinking is billed against the completion budget; low
# effort keeps latency and token use near what the old Llama chain used.
# It costs nothing in output quality here — Groq returns the reasoning in
# its own response field, never inside the JSON content we parse.
#
# These go into the request body via extra_body, never as named arguments
# to the SDK: the pinned groq client (0.25.0) has no reasoning_effort
# parameter and raises TypeError on an unknown kwarg before it ever calls
# Groq, which took the whole chain down with it. extra_body passes fields
# straight through on any SDK version, so the client and the API can adopt
# new fields on their own schedules.
_MODEL_KWARGS: dict[str, dict] = {
    "openai/gpt-oss-120b": {"reasoning_effort": "low"},
    "openai/gpt-oss-20b": {"reasoning_effort": "low"},
}

# The simple, user-facing categories. The LLM is never shown this list —
# it classifies against the taxonomy's specific leaf events (taxonomy.py)
# and we collapse the answer onto these labels, which is all the end user
# ever sees. The list mirrors services/api/app/routes/events.py, which
# backs GET /events/types — keep the two in sync.
EVENT_TYPES = taxonomy.CATEGORIES

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

3. "primary_category" — the ONE leaf of the TAXONOMY at the end of this
   prompt that best names what this filing reports. Answer with the slug
   exactly as written.

4. "categories" — 1 to 3 leaves from the same taxonomy, most investor-relevant
   first, starting with your "primary_category". Add a second or third only
   when the filing genuinely reports separate events (e.g. a merger agreement
   AND the debt financing that funds it) — not to hedge one event across
   neighbouring leaves.

5. "deal_terms" — a flat object of key-value pairs extracting structured data.
   Every value MUST be a plain, display-ready string — never a nested
   object, array, or expression. If a figure is a total you had to add up,
   write the total itself ("$11.5B"), not the arithmetic.
   Good: "deal_value": "$11.5B"
   Bad:  "deal_value": {{"$sum": "11500000000"}}
   Bad:  "deal_value": ["$500M", "$7B", "$4B"]
   Write word values in Title Case ("Definitive Agreement Signed"), keeping
   acronyms and proper names as they are spelled ("SPAC", "Inc."). Figures
   stay as written ("$11.5B", "45%").
   Include whichever of these apply (omit fields that don't):
   - "counterparty": the other party in the transaction
   - "deal_value": total consideration or deal size, abbreviated ("$11.5B")
   - "share_count": number of shares involved
   - "price_per_share": per-share price if stated
   - "premium": acquisition premium if stated or calculable (e.g. "45%")
   - "consideration_type": "Cash", "Stock", or "Mixed"
   - "deal_status": current status (e.g. "Definitive Agreement Signed",
     "Vote Pending", "Closed", "Registration Effective")
   - "expected_close": expected or actual closing date
   - "deal_type": e.g. "SPAC Merger", "Asset Purchase", "Stock-for-Stock"

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

TAXONOMY — answer with leaf slugs only; the group headings above them are
not valid answers:
{taxonomy.PROMPT_BLOCK}

CHOOSING A LEAF:
- Name what HAPPENED, not the SEC item number it was filed under.
{taxonomy.PROMPT_HINTS}
- If genuinely nothing fits, answer "" for "primary_category" and [] for
  "categories" rather than forcing a wrong leaf.

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
    if len(combined) > _TOTAL_TEXT_CAP:
        # The cap is a guess at what the token ceiling leaves us, and a
        # guess we pay for in dropped filing text. Log every time it bites
        # so the number can be set from data instead of arithmetic.
        log.warning(
            "Source truncated to fit the prompt: %d chars cut to %d for %s [%s]",
            len(combined), _TOTAL_TEXT_CAP, filing.title, filing.form_type,
        )
    return _truncate(combined, _TOTAL_TEXT_CAP)


def _validate_event_types(raw: list) -> list[str]:
    """Keep only display labels that exist in the canonical list, capped at 3.

    Classification answers arrive as taxonomy leaves, not labels, so this is
    only reached for callers that produce labels directly (facts-only
    briefings, legacy payloads).
    """
    valid = {t.lower(): t for t in EVENT_TYPES}
    out: list[str] = []
    for label in raw:
        if not isinstance(label, str):
            continue
        canonical = valid.get(label.strip().lower())
        if canonical and canonical not in out:
            out.append(canonical)
    return out[:3] or [taxonomy.OTHER]


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


class _EmptyCompletion(RuntimeError):
    """The model answered with no content at all (nothing to parse)."""


def _is_unsupported_parameter(exc: Exception, params: list[str]) -> bool:
    """Return True if our per-model extra fields are what got rejected.

    _MODEL_KWARGS is a hand-maintained table, so it can drift ahead of both
    the API and the installed client. Two shapes of rejection: Groq answers
    HTTP 400 for a field it doesn't accept, and the SDK raises TypeError
    before any request when a field isn't in its own signature. Either is
    worth one plain retry rather than losing an otherwise healthy model.
    """
    message = str(exc).lower()
    if isinstance(exc, TypeError) and "unexpected keyword argument" in message:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status != 400:
        return False
    if any(p.lower() in message for p in params):
        return True
    return any(w in message for w in ("unsupported", "unrecognized", "not supported"))


def _fallthrough_reason(exc: Exception) -> str | None:
    """Why this failure is the model's fault (and worth trying the next one).

    Returns a short phrase for the log, or None when the error says nothing
    about the model — a transport failure or a bad request would fail the
    same way on every model, so the chain should not burn through itself.
    """
    if _is_rate_limit(exc):
        return "rate-limited"
    if _is_model_unavailable(exc):
        return "unavailable"
    if isinstance(exc, _EmptyCompletion):
        return "returned an empty completion"
    if isinstance(exc, json.JSONDecodeError):
        return "returned unparseable JSON"
    return None


def _request_kwargs(model: str, messages: list[dict], max_tokens: int,
                    *, with_extras: bool = True) -> dict:
    """Every argument one completion call passes to the SDK.

    Built in one place so a test can bind it against the installed client's
    real signature — mocked clients accept anything, which is exactly how a
    kwarg the SDK didn't have reached production.
    """
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
        messages=messages,
    )
    extra = _MODEL_KWARGS.get(model) if with_extras else None
    if extra:
        kwargs["extra_body"] = dict(extra)
    return kwargs


def _one_completion(model: str, messages: list[dict], max_tokens: int) -> dict:
    """One JSON-mode completion from a single model."""
    client = Groq(api_key=_next_api_key())
    request = _request_kwargs(model, messages, max_tokens)
    try:
        response = client.chat.completions.create(**request)
    except Exception as exc:
        extra = request.get("extra_body")
        if not extra or not _is_unsupported_parameter(exc, list(extra)):
            raise
        log.warning("Model %s rejected %s, retrying without it",
                    model, ", ".join(sorted(extra)))
        response = client.chat.completions.create(
            **_request_kwargs(model, messages, max_tokens, with_extras=False))

    content = (response.choices[0].message.content or "").strip()
    if not content:
        # Reasoning models can spend the whole completion budget thinking
        # and return nothing — indistinguishable from a model failure here.
        raise _EmptyCompletion(f"{model} returned an empty completion")
    return json.loads(content)


def _chat_json(messages: list[dict], max_tokens: int = _MAX_COMPLETION_TOKENS) -> dict:
    """One JSON-mode chat completion over the model fallback chain.

    Errors specific to a single model — rate limits (429), unavailable
    models (404 model_not_found, e.g. a decommissioned model), and answers
    that carry no usable JSON — fall through to the next model in the
    chain. Any other error, or exhausting the chain, raises to the caller.
    """
    last_exc: Exception = RuntimeError("empty model chain")
    for model in _MODEL_CHAIN:
        try:
            return _one_completion(model, messages, max_tokens)
        except Exception as exc:
            last_exc = exc
            reason = _fallthrough_reason(exc)
            if reason and model != _MODEL_CHAIN[-1]:
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

    No taxonomy leaf is claimed: without the LLM the SEC item number is all
    we know, and it maps only to the coarse category, never to a specific
    event (taxonomy.ITEM_CATEGORIES).
    """
    mapped = []
    for it in filing.items:
        t = taxonomy.ITEM_CATEGORIES.get(it.number)
        if t and t not in mapped:
            mapped.append(t)
    event_types = mapped[:3] or [taxonomy.OTHER]

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
        taxonomy=[], mode="facts_only",
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

    # Classification: the model answers with taxonomy leaves; the product
    # surface is the ONE simple category each leaf collapses to.
    raw_categories = data.get("categories")
    leaves = taxonomy.validate_tertiaries([
        data.get("primary_category"),
        *(raw_categories if isinstance(raw_categories, list) else []),
    ])
    event_types = taxonomy.to_labels(leaves)
    primary = event_types[0]

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
        event_types=event_types,
        taxonomy=leaves,
        mode="llm",
    )
