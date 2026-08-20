# Groq LLM

Sensybull uses Groq's inference API to generate AI briefings from raw 8-K filing text. Groq runs open-weight models on custom LPU hardware, delivering fast inference at low cost.

---

## Why Groq?

### Speed

Groq's Language Processing Unit (LPU) is purpose-built hardware for LLM inference. It delivers tokens 5-10x faster than GPU-based providers:

- **Groq:** ~500 tokens/second
- **Typical GPU cloud:** ~50-100 tokens/second

For a real-time pipeline where new filings need to be processed and delivered within seconds, this speed advantage is critical. A briefing generates in 1-3 seconds instead of 10-30.

### Cost

Open-weight models on Groq cost a fraction of proprietary models:

- No per-token licensing fees
- Groq's pricing is competitive with other inference providers
- At our volume (dozens of filings/day), cost per briefing is negligible

### Quality

The models in the chain handle our structured JSON output format reliably. The system prompt is carefully engineered to get consistent results.

> Note (August 2026): Groq decommissioned the Llama 3.x chat models —
> `llama-3.3-70b-versatile` and `llama-3.1-8b-instant`, i.e. the entire
> chain. Both answered 404 `model_not_found`, so the chain was exhausted on
> every filing and every event published facts-only. The chain is now
> GPT-OSS with a Qwen last resort. (`meta-llama/llama-4-scout-17b-16e-instruct`
> went the same way in July 2026.) See below on why a retirement degrades
> gracefully instead of taking ingestion down — and why it stops being
> graceful once *every* model in the chain is gone.

---

## Models

Three models in rotation (default chain; override with `GROQ_MODELS`,
comma-separated, best-first):

### Primary: `openai/gpt-oss-120b`

- 120B mixture-of-experts, Groq's recommended replacement for the retired Llama chain
- Strong at structured output (JSON mode)
- Best quality for our use case

### Fallback: `openai/gpt-oss-20b`

- Same family, 20B — used when the primary is rate-limited or unavailable
- Smaller but still adequate for briefing generation
- Faster and cheaper

### Last resort: `qwen/qwen3.6-27b`

- Deliberately a different model family: if Groq retires the GPT-OSS pair
  together (as it did the Llama pair), the chain still has somewhere to go

### Reasoning models

The GPT-OSS models think before they answer, and those reasoning tokens are
billed against the completion budget. Two consequences the code handles:

- `reasoning_effort: "low"` is sent per-model (`_MODEL_KWARGS`), keeping
  latency and token use near the old Llama numbers. It goes through
  `extra_body`, never as a named SDK argument: the pinned client
  (groq 0.25.0) has no such parameter and raises `TypeError` before it ever
  calls Groq — which is exactly how the first deploy of this chain took
  every briefing down. `extra_body` passes fields straight into the request
  body on any client version. Either rejection, the SDK's `TypeError` or an
  HTTP 400 from Groq, triggers one plain retry on the same model rather
  than losing it.
- Mocked clients accept any keyword, so `_request_kwargs()` builds the call
  in one place and tests bind it against the installed SDK's real signature
  (`TestRequestMatchesInstalledSDK`). Add a per-model field there, not
  inline at the call site, or the mock will green-light something the
  client cannot send.
- The completion budget is 2,048 tokens (`_MAX_COMPLETION_TOKENS`), well
  above the ~600 the JSON answer needs, so reasoning can't truncate it.
  The reasoning itself comes back in its own response field, never inside
  the JSON we parse.

### Token budget

Groq's per-minute token ceiling applies to a single request too: one
oversize prompt is rejected outright, on every model in the chain. The
prompt caps are sized to fit under it — `_TOTAL_TEXT_CAP` (16K chars of
filing text) and `press_release._BODY_TEXT_CAP` (12K chars) plus the ~1.3K
token system prompt and the completion budget. Raising a cap without
checking the ceiling makes the *largest* filings — usually the interesting
ones — silently publish facts-only.

### Rotation Logic

```python
models = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.6-27b",
]

# Try primary model first
# On RateLimitError (429) → switch to next model
# On model-unavailable (404 model_not_found, e.g. a retired model) → switch to next model
# On an unusable answer (empty completion, unparseable JSON) → switch to next model
# On any other error (transport, 400, auth — nothing to do with the model)
#   → raise immediately rather than burn the rest of the chain
# On exhausting the chain → raise; the caller (generate_briefing) then
#   publishes a deterministic facts-only briefing
```

The chain is overridable at runtime via the `GROQ_MODELS` env var so a
model retirement can be worked around by config without a redeploy — set it
on the ingest worker in Render the moment a `model_not_found` shows up in
the logs, then fix the default.

---

## API Key Management

Multiple Groq API keys can be provided via `GROQ_API_KEYS` (comma-separated). The system round-robins between them:

```python
# Environment: GROQ_API_KEYS=key1,key2,key3
# Request 1 → key1
# Request 2 → key2
# Request 3 → key3
# Request 4 → key1 (wraps around)
```

**Why multiple keys?** Groq has per-key rate limits. Multiple keys multiply throughput during high-filing periods (market close, earnings season).

---

## The System Prompt

The LLM receives a carefully crafted system prompt that defines its persona and output format:

```
You are a senior event-driven investment analyst. Interpret this 8-K filing
as a professional would — focus on materiality, market impact, and
actionable insights for investors.
```

Key instructions:
- Generate headline (max 100 chars), summary (2-4 sentences), takeaway (1 sentence)
- Classify into 1-3 event types from the [[Event Type Taxonomy|canonical list]]
- Extract deal terms as flat key-value pairs
- Assess significance (High/Medium/Low) and sentiment (Positive/Negative/Neutral/Mixed)
- Identify upcoming [[Catalysts|catalyst dates]]
- Output valid JSON only — no markdown, no explanation

### Why This Prompt Design?

**Single call, multiple outputs.** One API call generates the briefing, classifies the event, extracts deal terms, and identifies catalysts. This is cheaper and faster than separate specialized calls.

**Analyst persona.** By framing the LLM as an "event-driven analyst," the output naturally focuses on what matters to investors (materiality, price impact) rather than legal interpretation.

**Structured JSON.** The prompt specifies exact field names and types. Llama models follow structured output instructions well, especially with clear examples.

---

## Error Handling

| Error | Response |
|---|---|
| Rate limit (429) | Switch to fallback model, try different API key |
| Model retired (404 `model_not_found`) | Switch to next model in the chain |
| Empty or unparseable answer | Switch to next model in the chain |
| Timeout | Retry with same model (up to 3 attempts) |
| Chain exhausted | Log warning, publish a deterministic facts-only briefing |
| API down | Log error, publish a facts-only briefing (filing still published) |

**Graceful degradation:** A filing is always published to Redis, even if every LLM call fails. The briefing is then `mode = "facts_only"` — form type, item categories and tier-derived significance, no generated prose — so users still see the filing, just without the narrative.

---

## What Gets Sent to the LLM

```
System: [analyst persona + output format instructions]

User: [filing content]
  - Company: {name} ({ticker})
  - Filing date: {date}
  - Items:
    - Item {number}: {title}
      {extracted text}
  - Exhibits:
    - {type}: {text from press release}
```

The user prompt includes the full extracted text from items and up to 3 press release exhibits. This gives the LLM both the formal legal language (items) and the human-readable narrative (press releases) to generate the best briefing.

---

## See Also

- [[Ingest Pipeline Deep Dive]] — Where LLM calls happen in the pipeline
- [[Event Type Taxonomy]] — The classification output
- [[Catalysts]] — The date extraction output
- [[Technology Decisions]] — Why Groq over alternatives
