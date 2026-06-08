# What is an 8-K Filing?

An 8-K is the SEC form that publicly traded companies must file when a **material event** occurs — something significant enough that investors should know about it immediately.

---

## Why 8-Ks Matter

Unlike 10-Ks (annual reports) and 10-Qs (quarterly reports), 8-Ks are **event-driven**. They're filed within 4 business days of a triggering event. This makes them the fastest official source of corporate news:

- A CEO resigns → 8-K filed within 4 days
- A company is acquired → 8-K filed within 4 days
- A data breach occurs → 8-K filed within 4 days

For event-driven investors (Sensybull's target audience), 8-Ks are the raw signal. Everything else — news articles, analyst notes, social media — lags behind the filing.

---

## Structure of an 8-K

Every 8-K contains one or more **Items**, each covering a specific type of event:

### Section 1 — Registrant's Business and Operations

| Item | Title | Tier |
|---|---|---|
| 1.01 | Entry into a Material Definitive Agreement | 2 |
| 1.02 | Termination of a Material Definitive Agreement | 2 |
| 1.03 | Bankruptcy or Receivership | **1** |
| 1.04 | Mine Safety | 3 |
| 1.05 | Material Cybersecurity Incidents | **1** |

### Section 2 — Financial Information

| Item | Title | Tier |
|---|---|---|
| 2.01 | Completion of Acquisition or Disposition of Assets | 2 |
| 2.02 | Results of Operations and Financial Condition | 2 |
| 2.03 | Creation of a Direct Financial Obligation | 2 |
| 2.04 | Triggering Events (Accelerating Obligation) | 3 |
| 2.05 | Costs Associated with Exit or Disposal Activities | 2 |
| 2.06 | Material Impairments | **1** |

### Section 3 — Securities and Trading Markets

| Item | Title | Tier |
|---|---|---|
| 3.01 | Notice of Delisting or Transfer | **1** |
| 3.02 | Unregistered Sales of Equity Securities | 3 |
| 3.03 | Material Modification to Rights of Security Holders | 3 |

### Section 4 — Matters Related to Accountants

| Item | Title | Tier |
|---|---|---|
| 4.01 | Changes in Registrant's Certifying Accountant | 3 |
| 4.02 | Non-Reliance on Previously Issued Financials | **1** |

### Section 5 — Corporate Governance and Management

| Item | Title | Tier |
|---|---|---|
| 5.01 | Changes in Control of Registrant | **1** |
| 5.02 | Departure/Election of Directors or Officers | 2 |
| 5.03 | Amendments to Articles or Bylaws | 3 |
| 5.04 | Temporary Suspension of Trading Under Employee Plans | 3 |
| 5.05 | Amendments to Code of Ethics | 3 |
| 5.06 | Change in Shell Company Status | 3 |
| 5.07 | Submission to Vote of Security Holders | 3 |
| 5.08 | Shareholder Nominations | 3 |

### Section 6-9

| Item | Title | Tier |
|---|---|---|
| 6.01 | ABS Informational and Computational Material | 3 |
| 7.01 | Regulation FD Disclosure | 3 |
| 8.01 | Other Events | 3 |
| 9.01 | Financial Statements and Exhibits | 3 |

---

## What Sensybull Does With 8-Ks

1. **Fetch** — Poll EDGAR's Atom feed for new 8-K filings
2. **Parse** — Extract individual items and their text
3. **Classify** — Assign a [[Item Tier Classification|tier]] (1-3) to each item
4. **Enrich** — Send to [[Groq LLM]] for an AI briefing, [[Event Type Taxonomy|event type]] classification, and [[Catalysts|catalyst date]] extraction
5. **Deliver** — Push to users via WebSocket based on their [[Watchlists]]

The goal: turn a dense legal document into a one-paragraph investor briefing with actionable metadata — in under 10 seconds.

---

## Exhibits

8-K filings often include **exhibits** — attached documents:

- **EX-99.x** — Press releases (the human-readable version of the event)
- **EX-10.x** — Material agreements (the actual contracts)
- **EX-3.x** — Articles/bylaws amendments

Sensybull fetches EX-99.x press releases because they contain the clearest narrative of what happened. The press release text is included in the LLM prompt alongside the formal 8-K item text, giving the AI both the legal and narrative perspectives.

---

## See Also

- [[Item Tier Classification]] — How we rank item importance
- [[Ingest Pipeline Deep Dive]] — How we fetch and parse filings
- [[Event Type Taxonomy]] — How we classify the event type
