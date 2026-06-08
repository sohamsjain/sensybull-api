# Glossary

Quick reference for terms used throughout this vault.

---

## SEC / Finance Terms

**8-K** — SEC form filed within 4 business days of a material corporate event. The primary data source for Sensybull. See [[What is an 8-K Filing]].

**10-K / 10-Q** — Annual and quarterly SEC reports. Not processed by Sensybull (yet).

**Accession Number** — SEC's unique identifier for a filing (e.g., `0001234567-24-012345`).

**Catalyst** — A future event that could move a stock price. See [[Catalysts]].

**CIK (Central Index Key)** — SEC's unique numeric identifier for a company (e.g., `0000320193` = Apple).

**EDGAR** — Electronic Data Gathering, Analysis, and Retrieval. SEC's filing database and search system.

**Event-Driven Investing** — An investment strategy focused on corporate events (mergers, earnings, leadership changes) that create price dislocations.

**Exhibit** — A document attached to an SEC filing. EX-99.x are press releases; EX-10.x are material agreements.

**Item** — A numbered section in an 8-K filing describing a specific type of event (e.g., Item 5.02 = officer departure).

**SIC Code** — Standard Industrial Classification code identifying a company's industry.

**Ticker** — A company's stock market symbol (e.g., AAPL, MSFT).

---

## Technical Terms

**Briefing** — The AI-generated summary of a filing, produced by the [[Groq LLM]].

**Daemon Thread** — A background thread that automatically terminates when the main process exits. Used for the Redis subscriber.

**EdgarTools** — Python library for accessing the SEC EDGAR API.

**Eventlet** — Python library for cooperative concurrency via green threads. See [[Eventlet]].

**FilingEvent** — The core data record — an enriched 8-K filing with items, exhibits, briefing, and metadata. See [[Data Model]].

**Greenlet** — A lightweight cooperative thread managed by Eventlet. Thousands can run in one OS thread.

**Groq** — AI inference provider running Llama models on custom LPU hardware. See [[Groq LLM]].

**Idempotent** — An operation that produces the same result regardless of how many times it's applied. See [[Idempotency & Deduplication]].

**LPU** — Language Processing Unit. Groq's custom hardware for fast LLM inference.

**Marshmallow** — Python library for object serialization and validation.

**max_tier** — The most critical (lowest number) tier among all items in a filing.

**Monkey-patching** — Replacing standard library functions at runtime. Eventlet does this to make blocking I/O cooperative.

**Namespace** — Socket.IO concept for logically separate communication channels. We use `/feed`.

**Pub/Sub** — Publish/Subscribe messaging pattern. Publisher sends to a channel; subscribers receive. See [[Redis]].

**Room** — Socket.IO concept for grouping connections. Used for targeted message delivery (e.g., `user:123`).

**seen.json** — Local file tracking which EDGAR entries have been processed by the ingest service.

**Tier** — Importance ranking for 8-K items: 1 (critical), 2 (important), 3 (routine). See [[Item Tier Classification]].

**WAL (Write-Ahead Logging)** — SQLite mode enabling concurrent reads during writes.

**Watchlist** — A user-defined collection of companies. Determines which events a user receives. See [[Watchlists]].

---

## See Also

- [[Home]] — Vault entry point
- [[Architecture Overview]] — System overview
