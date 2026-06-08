# Groq LLM

Sensybull uses Groq's inference API to generate AI briefings from raw 8-K filing text. Groq runs open-source Llama models on custom LPU hardware, delivering fast inference at low cost.

---

## Why Groq?

### Speed

Groq's Language Processing Unit (LPU) is purpose-built hardware for LLM inference. It delivers tokens 5-10x faster than GPU-based providers:

- **Groq:** ~500 tokens/second
- **Typical GPU cloud:** ~50-100 tokens/second

For a real-time pipeline where new filings need to be processed and delivered within seconds, this speed advantage is critical. A briefing generates in 1-3 seconds instead of 10-30.

### Cost

Open-source Llama models on Groq cost a fraction of proprietary models:

- No per-token licensing fees (Llama is open-weight)
- Groq's pricing is competitive with other inference providers
- At our volume (dozens of filings/day), cost per briefing is negligible

### Quality

Llama-4-Scout-17B handles our structured JSON output format reliably. The system prompt is carefully engineered to get consistent results.

---

## Models

Two models in rotation:

### Primary: `meta-llama/llama-4-scout-17b-16e-instruct`

- 17B parameters, 16-expert mixture-of-experts architecture
- Strong at structured output (JSON)
- Best quality for our use case

### Fallback: `llama-3.1-8b-instant`

- 8B parameters, dense architecture
- Used when the primary model is rate-limited
- Smaller but still adequate for briefing generation
- Faster and cheaper

### Rotation Logic

```python
models = [
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "llama-3.1-8b-instant"
]

# Try primary model first
# On RateLimitError → switch to fallback
# On other errors → retry with same model
```

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
| Timeout | Retry with same model (up to 3 attempts) |
| Invalid JSON output | Log warning, return null briefing (filing still published with items/exhibits) |
| API down | Log error, skip briefing (filing still published) |

**Graceful degradation:** A filing is always published to Redis, even if the LLM call fails. The briefing field will be null, but items, exhibits, and tier are still present. This means users see the raw filing data even when the AI is unavailable.

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
