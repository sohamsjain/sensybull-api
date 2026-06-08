# Sensybull — Project Knowledge Vault

> **What is Sensybull?** A real-time intelligence platform that transforms raw SEC 8-K filings into actionable, AI-summarized investment signals — delivered to your browser the moment they drop.

---

## The Big Picture

```
SEC EDGAR ──► Ingest Service ──► Redis Pub/Sub ──► API Service ──► WebSocket ──► React Frontend
  (8-K feed)    (poll + parse      (filing:new)     (persist +       (Socket.IO)    (live feed)
                 + LLM briefing)                     fan-out)
```

Two decoupled microservices talk only through a [[Redis Pub-Sub Contract]]. The [[Ingest Service]] fetches, parses, and enriches SEC filings. The [[API Service]] persists them, enforces access control, and pushes them to connected clients in real-time.

---

## Start Here

| If you want to understand... | Read this |
|---|---|
| How data flows end-to-end | [[Data Flow — End to End]] |
| The two services and why they're separate | [[Architecture Overview]] |
| What technologies we use and why | [[Technology Decisions]] |
| How filings get parsed and enriched | [[Ingest Pipeline Deep Dive]] |
| How real-time push works | [[Real-Time System]] |
| The data model | [[Data Model]] |
| Authentication & security | [[Authentication System]] |
| The React frontend | [[Web Frontend]] |
| How to run everything | [[Docker & Local Development]] |
| API endpoints reference | [[API Routes Reference]] |

---

## Concept Map

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  SEC EDGAR  │────►│   Ingest     │────►│   Redis     │
│  (8-K feed) │     │  Service     │     │  Pub/Sub    │
└─────────────┘     └──────┬───────┘     └──────┬──────┘
                           │                     │
                    ┌──────▼───────┐      ┌──────▼──────┐
                    │  Groq LLM   │      │  API        │
                    │  (briefing) │      │  Service    │
                    └─────────────┘      └──────┬──────┘
                                                │
                                         ┌──────▼──────┐
                                         │  SQLite DB  │
                                         └──────┬──────┘
                                                │
                                         ┌──────▼──────┐
                                         │  Socket.IO  │──► React Frontend
                                         └─────────────┘
```

---

## Key Concepts

- [[What is an 8-K Filing]] — The SEC form that drives everything
- [[Item Tier Classification]] — How we rank filing importance (Tier 1/2/3)
- [[Event Type Taxonomy]] — The 34 canonical labels our LLM assigns
- [[Watchlists]] — How users curate their universe of companies
- [[Catalysts]] — Future dates extracted from filings that matter to investors
- [[Idempotency & Deduplication]] — How we avoid processing the same filing twice

---

## Service Index

- [[Ingest Service]]
- [[API Service]]
- [[Web Frontend]]
- [[Redis]] — The glue between services

---

*This vault is designed to be opened in [Obsidian](https://obsidian.md). Notes are interlinked with `[[wiki-links]]` — click through to explore.*
