# Item Tier Classification

Every 8-K item is assigned a **tier** indicating its potential market impact. This is Sensybull's importance filter.

---

## The Three Tiers

### Tier 1 — Critical

Events that are almost always market-moving. Investors need to know immediately.

| Item | Event | Why it's critical |
|---|---|---|
| 1.03 | Bankruptcy or Receivership | Company may be worthless |
| 1.05 | Material Cybersecurity Incident | Regulatory + reputational risk |
| 2.06 | Material Impairments | Significant asset writedown |
| 3.01 | Delisting/Transfer Notice | Stock may become untradeable |
| 4.02 | Non-Reliance on Prior Financials | Restatement = trust destroyed |
| 5.01 | Changes in Control | Ownership is changing hands |

### Tier 2 — Important

Significant events that sophisticated investors track closely.

| Item | Event | Why it matters |
|---|---|---|
| 1.01 | Material Agreement | New contract, partnership, or deal |
| 1.02 | Termination of Agreement | A major deal fell through |
| 2.01 | Acquisition/Disposition | Company bought or sold assets |
| 2.02 | Results of Operations | Earnings preview or guidance |
| 2.03 | New Financial Obligation | Taking on debt |
| 2.05 | Exit/Disposal Costs | Restructuring underway |
| 5.02 | Director/Officer Changes | Leadership shakeup |

### Tier 3 — Routine

Administrative or procedural events. Rarely market-moving on their own.

Everything else: bylaw amendments, auditor changes, Reg FD disclosures, shareholder votes, etc.

---

## How Tiers Are Used

### In the Ingest Pipeline

Each filing's **`max_tier`** is the most critical tier among all its items:

```python
# If a filing has Item 5.02 (Tier 2) and Item 1.03 (Tier 1):
max_tier = min(item.tier for item in items)  # = 1
```

Lower number = more critical. This pre-computation means consumers don't need to scan all items.

### In the API

The events endpoint supports tier filtering:

```
GET /events/?max_tier=2    # Only Tier 1 and 2 events
GET /events/?max_tier=1    # Only Tier 1 (critical) events
```

### In WebSocket Replay

When a client connects, the server replays the last 50 events — but only Tier 1 and 2. Tier 3 events are available via REST but not pushed on reconnection to avoid noise.

### In the Frontend

Each filing card shows a **TierBadge** component with color coding:
- Tier 1: Red/critical indicator
- Tier 2: Yellow/important indicator
- Tier 3: Gray/routine indicator

---

## Design Philosophy

The tier system is **hardcoded** in `services/ingest/parser.py`, not ML-driven. This is intentional:

1. **Deterministic** — Same item always gets the same tier. No model drift.
2. **Transparent** — Users can understand exactly why a filing is marked critical.
3. **SEC-stable** — The 8-K item numbering system hasn't changed in years.

The AI's **significance** assessment (High/Medium/Low in the briefing) adds a contextual layer on top. A Tier 2 item might be "High" significance if the deal value is large, or "Low" if it's a routine contract renewal.

---

## See Also

- [[What is an 8-K Filing]] — Full item reference table
- [[Event Type Taxonomy]] — AI-driven classification (complements tiers)
- [[Ingest Pipeline Deep Dive]] — Where tier assignment happens
