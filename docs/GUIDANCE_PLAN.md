# Guidance tracker — plan

> **Status:** proposal, nothing built. **Last updated:** 2026-10-22.
>
> Goal: every quantitative forward-looking statement a US-listed company's
> management makes (revenue, EPS, margins, FCF, capex, multi-year targets),
> extracted from 8-Ks, press releases and earnings-call transcripts, stored as
> structured rows with a verbatim source quote, normalised so that companies
> can be compared and sorted, and checked against what was actually reported.
> Readers browse a sortable guidance board, see each company's guidance
> history on its fundamentals page, and set alerts like "guidance cuts in
> Technology mega caps".

---

## 0. Ground truth first

Four things this plan rests on. Push back on any of them before building.

**1. The reference screenshot is not how US companies talk.** "100% for next
1 year" is an Indian small-cap concall habit. US management mostly guides an
*absolute range for a fiscal period*: "Q4 revenue of $1.42–1.46B", "FY27
adjusted EPS $5.10–5.30", "operating margin about 31%". Sometimes they give a
growth rate ("high-single-digit organic growth") or a multi-year target
("$10B revenue by FY2029"). So the single uniform number the board sorts on
cannot be read off the source. **It has to be computed by us:** guided
midpoint ÷ the matching prior-year actual from `fundamentals_period`, turned
into an annual rate. The LLM extracts; deterministic code standardises. If
the LLM computes growth rates, the board will be wrong in ways nobody can
audit.

**2. Coverage will be partial, and that is the product working correctly.**
Many large companies give no numeric guidance (Alphabet, Berkshire), many
small caps give none at all, and plenty give only margins or capex. My
unverified guess is 1,500–2,500 of our ~5,000 listed companies give at least
one quantitative revenue or EPS guide a year. Phase 0 measures the real
number. A company with no guidance is simply absent from the board, as you
said.

**3. Sorting by guided growth on its own rewards promoters.** Look at the
screenshot: the top rows are the most optimistic managements, not the best
companies. What nobody offers for free is **"how often did this management
hit what it guided?"** We already store the actuals (`fundamentals_period`),
so we can score every company's track record. That score, shown next to
every guided number, is the difference between this and a reprint of the
screenshot. It is also the feature people come back for.

**4. This conflicts with `PRODUCT_VISION.md`, so it needs a written
decision.** The vision says thesis monitoring is the priority, rules out
"taxonomy dashboards", and rules out financial-statement parsing. A guidance
board is a new surface, and transcripts are a new source. The honest
reconciliation is that **structured guidance is what the thesis judge needs
to judge against.** The vision itself names "guidance revisions (Item 2.02)"
as the first thing a verdict should react to. Build guidance as the data
layer, and the judge's drift verdict on "guidance lowered" comes almost free.
Don't build it as a separate product with its own roadmap. If you agree,
update `PRODUCT_VISION.md`'s scope list in the same PR that starts Phase 1.

---

## 1. What we build on

| Exists | Where | Use |
| --- | --- | --- |
| 8-K ingest with Item 2.02 + exhibit text (EX-99.1 is usually the earnings release *including its outlook table*) | `services/ingest/` | Real-time guidance source #1 |
| FMP press releases, seconds after the wire | `services/ingest/press_release/` | Real-time source #2 (earnings PRs often hit the wire before the 8-K) |
| PR↔8-K dedup | `realtime/pr_dedup.py` | The same release must yield one set of guidance rows, not two |
| Verbatim-quote verification + text-fragment deep links | `ingest/evidence.py`, `text_fragment.py` | Every guidance row cites its sentence; reuse unchanged |
| Statements per quarter/year, GAAP, from FMP | `fundamentals_period` | Prior-year base for implied growth; actuals for the track record |
| Earnings calendar | `fundamentals/fmp_client.py` (`/earnings-calendar`) | Know when a transcript should appear; schedule fetches |
| FMP Ultimate plan (includes transcripts, per FUNDAMENTALS_PLAN) | — | Transcripts + analyst estimates, no new vendor |
| Saved views as alert rules, every channel | `feed_filters.py`, `alerts/dispatcher.py` | Guidance alerts are a new filter dimension, not a new alert system |
| Company page (server-rendered) | web `/company/[symbol]` | Gets a Guidance section |

**The hard constraint: the LLM budget.** Briefings run on Groq at 8K TPM with
a 16K-character prompt cap (`briefing._TOTAL_TEXT_CAP`). An earnings-call
transcript is 40–80K characters, so it physically does not fit. Guidance
extraction therefore **cannot ride the briefing pass**. It needs its own
worker and a long-context model (section 4.3).

---

## 2. Customer journeys (what makes it hard to give up)

Ordered by how often they happen, not how impressive they look.

**J1. "Did anything I own change its guidance?"** (owner, every earnings
season, several times a week). A push or email arrives seconds after the
release: *"NVDA raised FY27 revenue guidance 4% to $212–216B, 3% above
consensus. Track record: beat its guide 7 of the last 8 quarters."* Below it
is the quoted sentence, linked to the highlighted passage on sec.gov. **This
is the habit loop.** It runs on what we already ingest in real time (8-K +
PR), so it ships first.

**J2. "What guided up or down this morning?"** (active trader, daily during
earnings season). A feed filter `Guidance: raised · lowered · withdrawn`
combined with the existing sector, cap and important filters. Sort by size
of revision or by distance from consensus. Today they piece this together
from a dozen headlines.

**J3. "Show me the highest guided growth, but only from managements that
deliver."** (screener user, weekly). This is the screenshot, done properly:
the `/guidance` board. One row per company: metric, period, implied
annualised growth, vs consensus, revision direction, track-record badge.
Filters: sector/industry, cap, metric, horizon, minimum hit rate. Default
sort is implied growth, with low-credibility rows visibly marked rather
than hidden.

**J4. "How has this company's story moved?"** (due diligence, before buying).
A Guidance section on `/company/[symbol]` with the **guidance walk**: for
each fiscal year, every guide issued for it (initial → raised → narrowed),
ending at the actual. It is one small chart per metric plus a table. Revisions
show momentum better than any single number does.

**J5. Rule-based alerts** ("guidance cuts in Technology mega caps", "any
small-cap healthcare initiating guidance", "withdrawn guidance anywhere").
These are saved feed views with the new `guidance` dimension. The existing
dispatcher, channels, mutes and one-alert-per-event key all apply unchanged.

**J6. Thesis drift** (later, the vision's judge). "Guidance lowered" on a
company whose thesis line says "revenue growth reaccelerates" is the
cleanest `drift` verdict we will ever have, because it is backed by a
structured row and a verified quote.

What would make people trust it, and what would make them leave:
- **Speed:** within about a minute of the release for J1 and J2. Transcript
  enrichment can arrive hours later.
- **Every number clickable to its source sentence.** One wrong unit
  (millions read as billions) on a mega cap and the feature is dead.
  Section 5 is about preventing that.
- **No false "cut" alerts.** "Reaffirmed" and "narrowed around the same
  midpoint" must never alert as lowered.

---

## 3. Standardising guidance (the core of the feature)

### 3.1 The canonical row

Every extracted statement becomes one `guidance_item`:

| Field | Values / notes |
| --- | --- |
| `metric` | Fixed list: `revenue`, `eps_adjusted`, `eps_gaap`, `operating_income`, `ebitda_adjusted`, `gross_margin`, `operating_margin`, `fcf`, `capex`, `other`. `other` keeps a free-text label (ARR, same-store sales, deliveries) and is shown but never ranked. |
| `scope` | `total` or `segment` (+ label). The board ranks totals only. |
| `basis` | `gaap`, `non_gaap`, `organic`, `constant_currency`, `unspecified` |
| `period` | Resolved to **our fiscal calendar**: `fiscal_year` + `fiscal_period` (`Q1..Q4`, `FY`), or a multi-year target year. "Next year" or "fiscal '27" is resolved by code from the company's `fiscal_year_end_month` and the filing date, not by the LLM. |
| `kind` | `absolute` (a $ or per-share range), `growth` (a % range stated by management), `margin` (a % level), `qualitative_range` ("high single digits") |
| `low`, `high`, `unit` | Units normalised to whole dollars, dollars per share or percent. A point guide has low = high. Verbal ranges map through one table: low single digits = 1–3%, mid = 4–6%, high = 7–9%, low double digits = 10–13%, mid-teens = 14–16%, high teens = 17–19%. That table is ours, published on the page, and the original words are kept. |
| `action` | `initiated`, `raised`, `lowered`, `maintained`, `narrowed`, `widened`, `withdrawn`. **Computed by code** by comparing against the previous active row for the same (company, metric, scope, basis, period), not taken from the model's adjective. Management "reaffirming" while moving the midpoint down 2% is a cut. |
| `quote` | Verified span (evidence.py rules), plus deep link. Rows without a verified quote are not stored. |
| `sources` | One or more of 8-K, PR, transcript. The same statement in a PR, its 8-K and the call is **one row with three sources**, not three rows. |

### 3.2 The number the board sorts on

`implied_growth_annualised`, computed, never extracted:

- `absolute` revenue/EPS/FCF: midpoint ÷ the same period's prior-year actual
  − 1. A quarterly guide compares to the same quarter a year earlier.
  Multi-year: CAGR from the last reported fiscal year to the target year.
- `growth`: the stated midpoint; for multi-year rates, already annual.
- `margin`: not a growth rate. It is shown as a change in basis points versus
  the prior year and sorted separately. Mixing it into growth would be
  meaningless.
- Negative or near-zero bases (loss-making EPS, small revenue): no rate,
  show "n/m". Otherwise a $0.01 → $0.10 EPS guide tops the board at +900%.
  The board needs a floor (revenue base ≥ $10M, EPS base ≥ $0.10) or it fills
  with noise.

The board shows **one headline row per company**: the furthest-out active
revenue guide, else EPS. All other active guides sit in an expandable row.
Every number keeps a horizon tag ("FY27", "Q4", "→ FY29 target"), because a
quarter guide and a three-year target are not the same claim even after
annualising.

### 3.3 Basis matching for the track record

FMP statements are GAAP. Most EPS guidance is **non-GAAP**. So the actual
used for scoring has to match the basis of the guide:
- Revenue: GAAP actual from `fundamentals_period` (revenue is rarely
  adjusted). This is reliable.
- Adjusted EPS / EBITDA / FCF: the company's own reported adjusted figure,
  extracted from the next earnings release by the same pipeline, which
  already has to read that release for guidance. FMP's
  `/earnings` `epsActual` is a cross-check, not the source.
- Score per company: hit rate (actual ≥ guide low), average beat versus
  midpoint, and "sandbagger / straight / over-promiser" from the bias over
  the last 8 guided periods. Fewer than 4 scored periods → no badge.

Use the **last guide before the period ended**, not the first, so that a
company that cut twice and then "beat" is not scored as reliable. Show both
the initial and the final guide in the walk, because the drift between them
is the real story.

### 3.4 Versus consensus

FMP `/analyst-estimates` gives consensus revenue and EPS per fiscal year
(and quarter). "Guided midpoint vs consensus" is what moves US stocks on the
night, so it belongs in J1 alerts and as a board column. Snapshot consensus
**at the time of the guide** (a `consensus_at_issue` column). Otherwise
consensus converges on the guide within days and the comparison reads 0%.

---

## 4. Pipeline

### 4.1 Sources and timing

| Source | Latency | Role |
| --- | --- | --- |
| Earnings PR (FMP) | seconds | First real-time read; usually carries the outlook table |
| 8-K Item 2.02 EX-99.1 | ~1–15 min | Same text, sec.gov link for evidence; confirms the PR |
| FMP transcript | hours to a day (**verify in Phase 0**) | Verbal guidance absent from the release, segment colour, multi-year targets; enriches or creates rows |

Real-time alerts fire from PR/8-K. Transcripts never send a "raised" alert
hours late for something already alerted. They can only *add* rows, and a
transcript-only revision alerts with its own source label.

### 4.2 Where it runs

A **separate `guidance_worker`** on the API side, started like
`reaction_worker.py` and fed by the subscriber after an event persists. It is
not part of ingest, because the ingest LLM budget is spoken for and briefing
latency must not move.

1. **Cheap gate:** an Earnings event type, or Item 2.02, or the text matches
   `guidance|outlook|expects? .* (revenue|eps|earnings)|full[- ]year|fiscal 20\d\d`.
   Most 8-Ks stop here at zero cost.
2. **Section cut:** take the release's "Outlook/Guidance" section when one
   exists (most earnings releases have a heading), else the whole document.
   For transcripts, take prepared remarks plus Q&A turns that match the gate
   regex.
3. **Extraction** (one LLM call, JSON schema = section 3.1 minus the
   computed fields, plus a verbatim quote per item).
4. **Verify quote** (evidence.py) → **resolve period** → **normalise units**
   → **sanity checks** (section 5) → **diff against active rows** → `action`.
5. Persist, then emit: attach a `guidance` summary to the event payload
   (`briefing_json.guidance`, so no event-table migration), set the event's
   guidance facet, and call the alert triggers.

Backfill uses the same worker over FMP transcripts for the last 12 quarters
per company. That is what puts history and track records on the page from day
one. Without it, the board takes a full earnings cycle to fill and the track
record takes two years.

### 4.3 Model choice and volume (estimate before committing)

- Volume: ~2,000 guiding companies × 4 quarters × (release + transcript)
  ≈ 16K extractions/year, peaking at roughly 400/day in earnings season.
  Backfill: ~5,000 companies × 12 quarters ≈ 60K transcripts ≈ 1B input
  tokens at ~15K tokens each.
- The Groq tier we use cannot take a transcript in one request. Use a
  long-context model with strict JSON output. Run backfill through a batch
  API (non-urgent, much cheaper) and real-time through the normal API. Price
  both options from current pricing pages in Phase 0. Don't size it from
  memory, mine included.
- Accuracy matters more than cost here: one wrong mega-cap number costs more
  than the token bill. Run the Phase 0 golden set against two model tiers and
  choose on measured precision.

---

## 5. Accuracy safeguards (what keeps a wrong number off the board)

- **Quote or nothing:** no verified span, no row (same rule as evidence).
- **Unit sanity:** a guided revenue outside 0.3×–3× of the trailing
  comparable actual is held back as `needs_review`, not published. This
  catches millions/billions and quarterly/annual mix-ups.
- **Period sanity:** the target period must end after the source's date,
  except in-period updates.
- **Segment leakage:** if a "total revenue" guide is under 50% of the last
  total actual, demote it to `segment` / `needs_review`.
- **Diff cool-off:** a `lowered` action from a transcript within 48h of a PR
  row for the same period must match the PR row's numbers before it alerts.
  Spoken numbers get rounded ("about one-four").
- **Review queue:** a CLI (`flask guidance-review`) listing held rows for mega
  and large caps, at least for the first earnings season. A human clears or
  fixes them. Track the hold rate; aim to switch the queue off, not keep it.
- **Golden set as a test:** the labelled Phase 0 set lives in
  `tests/fixtures/guidance/`, and extraction precision is a CI-tracked number
  (an offline eval, not run on every push).

---

## 6. Data model (sketch)

- `guidance_item`: section 3.1 + `company_id`, `issued_at`, `status`
  (`active | superseded | withdrawn | needs_review | expired`),
  `supersedes_id`, `implied_growth`, `prior_actual`, `consensus_at_issue`,
  `source_event_ids` (JSON), `transcript_ref`.
- `guidance_outcome`: one row per (company, metric, basis, period) once the
  actual lands: `initial_mid`, `final_mid`, `actual`, `hit`, `surprise_pct`.
  It is filled by a step appended to `sync-fundamentals` (statements arrive
  there anyway) plus the release extraction for adjusted figures.
- `company_guidance` (one row per company, like `company_fundamentals`):
  the board's denormalised row (headline guide, implied growth, vs
  consensus, last action, hit rate, bias badge). The board query then has no
  joins and no window functions.
- `transcript`: `company_id`, fiscal year/quarter, call date, text, fetched_at.
  Stored so re-extraction after a prompt change costs no FMP calls.

---

## 7. API and web surface

API (all added to `app/openapi.py`):
- `GET /guidance/` public board: `sector, industry, cap, metric, horizon,
  action, min_hit_rate, sort, since`, paged, Redis-cached 60s. It reuses
  `feed_filters`' sector/cap parsing rather than a second copy.
- `GET /guidance/<symbol>` active guides, history (the walk), outcomes,
  track record.
- Feed filter dimension `guidance` (`raised, lowered, initiated, withdrawn,
  maintained`) in `feed_filters.py` + facets + `src/lib/feed-filters.ts`
  mirror. That single addition gives J2 and J5, including alert rules, with
  no new alert code.
- Payload: `briefing.guidance = [{metric, period, low, high, unit, action,
  implied_growth, vs_consensus, quote, url}]`. The web renders it in expanded
  updates between evidence and deal terms.

Web:
- `/guidance`: the board (server-rendered like `/company/[symbol]`, so it is
  indexable). `DataTable`, sticky header, one headline row per company.
- Company page: a **Guidance** section after the header ratios (walk chart +
  table + track-record line).
- Feed card: one line on collapsed updates, "Raised FY27 revenue guide +4%".
  This is the one exception worth making to headline-only, because the
  action is the headline. Decide this explicitly; it touches a product rule.
- Alert copy: lead with action + metric + period + delta + vs consensus,
  then the quote.

Not doing: an EVENT_TYPES entry for "Guidance" (that list is deliberately
small, and guidance is an attribute of an Earnings event, not a category);
guidance for OTC filers; non-USD reporters in the first version.

---

## 8. Phases (each one ships something usable)

**Phase 0: measure (≈1 week, no product code).**
`tools/probe_guidance.py`: pull 150 transcripts + EX-99.1s across cap
buckets and sectors from the last two quarters. Hand-label 50 of them. Run
the extractor on two model tiers. Output: precision/recall per field,
coverage (% of companies with ≥1 rankable guide), transcript latency after
the call, token cost per extraction. **Go/no-go gate: ≥95% precision on
metric + period + numbers for rankable rows.** Below that, the board does
more harm than good.

**Phase 1: real-time guidance on what we already ingest.** Worker + gate +
extraction for 8-K/PR, `guidance_item`, the action diff, the `guidance` feed
filter + facets, alert rules, payload + expanded-update rendering. This
delivers J1, J2 and J5. No transcripts yet. It ships within the scope the
vision already allows (Item 2.02).

**Phase 2: transcripts + history.** FMP transcript fetch (scheduled off the
earnings calendar), the `transcript` table, a 12-quarter backfill through
the batch API, multi-source merge, and the company-page Guidance section with
the walk (J4).

**Phase 3: the board + track record.** `guidance_outcome`,
`company_guidance`, consensus snapshotting, `/guidance` board (J3),
track-record badges in alerts and on the board.

**Phase 4: thesis.** Feed guidance actions into the judge as structured
input (J6). It belongs to the thesis rebuild, not here.

Stop condition: if Phase 0 fails its gate, ship Phase 1 restricted to
`withdrawn` and `initiated` (the easiest actions to get right) and stop.

---

## 9. Open questions for you

1. Is guidance the thesis judge's data layer (my recommendation), or a
   standalone surface? This decides whether `PRODUCT_VISION.md` changes.
2. Is the `/guidance` board public (SEO, acquisition) or signed-in only? I'd
   make it public like fundamentals and keep alerts behind sign-in.
3. Collapsed feed cards showing the guidance action breaks "headline only".
   Accept that exception?
4. Budget ceiling for the LLM bill (backfill one-off + monthly), which
   decides the model tier after Phase 0.
