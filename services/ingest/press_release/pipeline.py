"""
pipeline.py — press-release poll loop.

Per release, in order (cheapest checks first; each drop is counted):

  seen-set → promo/thin prefilter → law-firm blocklist → ticker
  extraction → issuer verification → cross-wire fingerprint dedup →
  LLM materiality gate → publish to Redis `filing:new`.

Publishing reuses the exact FilingEvent contract the 8-K pipeline uses;
the API subscriber treats PRs identically apart from dedup (see
services/api/app/services/realtime/pr_dedup.py). The subscriber also
re-checks cross-wire dups against the DB, so the fingerprint file here
being ephemeral across deploys only costs LLM calls, not duplicate feed
items.
"""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import seen as seen_store
from events import FilingEvent, FilingEventBriefing
from parser import strip_html
from publisher import publish_filing

from press_release import fmp
from press_release import issuer as issuer_mod
from press_release import materiality
from press_release.language import is_english
from press_release.feeds import enabled_wires, fetch_pr_url, fetch_release_body, parse_wire_feed
from press_release.fingerprint import build_fingerprints, fingerprints_match

log = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PR_SEEN_FILE = os.path.join(DATA_DIR, "pr_seen.json")
FINGERPRINT_FILE = os.path.join(DATA_DIR, "pr_fingerprints.json")

# Cross-wire dedup window: the same release lands on a second wire within
# minutes; 48h is generous and keeps the file tiny.
_FINGERPRINT_TTL_HOURS = 48

# Upper bound on LLM classifications per poll cycle — a wire flooding the
# feed can't burn the Groq budget. Overflow stays unseen for the next cycle.
_LLM_CAP_PER_CYCLE = 20

# Transient LLM failures: retry on later polls, give up after this many.
_MAX_LLM_ATTEMPTS = 3

_EDGAR_ID_MAX = 500  # filing_event.edgar_id column width


def _poll_interval() -> int:
    try:
        return max(60, int(os.environ.get("PR_POLL_INTERVAL", "180")))
    except ValueError:
        return 180


def pr_ingest_enabled() -> bool:
    return os.environ.get("PR_INGEST_ENABLED", "").strip() in ("1", "true", "yes")


def pr_source() -> str:
    """"fmp" (default): FMP's aggregated press-release endpoint. "rss": the
    original per-wire RSS adapters (GlobeNewswire + PR Newswire only)."""
    return os.environ.get("PR_SOURCE", "fmp").strip().lower() or "fmp"


def _dry_run() -> bool:
    """PR_DRY_RUN=1 runs every gate (LLM included) and logs what would
    publish, without publishing."""
    return os.environ.get("PR_DRY_RUN", "").strip() in ("1", "true", "yes")


def _env_float(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, default)))
    except ValueError:
        return default


def _synthetic_edgar_id(wire: str, guid: str) -> str:
    eid = f"pr:{wire}:{guid}"
    if len(eid) > _EDGAR_ID_MAX:
        digest = hashlib.sha256(guid.encode("utf-8")).hexdigest()
        eid = f"pr:{wire}:sha256:{digest}"
    return eid


def _iso_published(raw: str) -> str:
    """Best-effort ISO-8601 for the wire's pubDate; falls back to now(UTC).

    Wires publish RFC-822 dates ("Tue, 15 Jul 2026 08:30:00 -0400"); the
    subscriber parses ISO only, so convert here.
    """
    if raw:
        try:
            from email.utils import parsedate_to_datetime
            return parsedate_to_datetime(raw).isoformat()
        except (ValueError, TypeError):
            pass
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Rolling cross-wire fingerprint memory
# ---------------------------------------------------------------------------

def _load_fingerprints() -> list[dict]:
    try:
        with open(FINGERPRINT_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return []


def _save_fingerprints(entries: list[dict]) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=_FINGERPRINT_TTL_HOURS)).isoformat()
    pruned = [e for e in entries if e.get("ts", "") >= cutoff]
    dir_ = os.path.dirname(FINGERPRINT_FILE)
    with tempfile.NamedTemporaryFile(
        "w", dir=dir_, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tmp:
        json.dump(pruned, tmp)
        tmp_path = tmp.name
    os.replace(tmp_path, FINGERPRINT_FILE)
    entries[:] = pruned


def _is_cross_wire_dup(entries: list[dict], ticker: str, fingerprints: dict) -> bool:
    for e in entries:
        if e.get("ticker") != ticker:
            continue
        if fingerprints_match(fingerprints, e):
            return True
    return False


# ---------------------------------------------------------------------------
# Per-release processing
# ---------------------------------------------------------------------------

def process_release(release, ticker_index: dict, fp_entries: list[dict],
                    counters: dict) -> FilingEvent | None:
    """Run one release through the gate chain. Returns the publishable
    event, or None on any drop (counted in `counters`).

    LLM/transport failures propagate to the caller (retried next poll).
    """
    # Wires syndicate translations of the same release; the product is
    # English-only and the English original arrives separately. The wire's
    # tag is a fast path; FMP has none, so the text itself is checked too —
    # once on the teaser (before paying for an article fetch), once on the
    # full body.
    language = (release.language or "").strip().lower()
    headline = release.headline.strip()
    if language and not language.startswith("en"):
        counters["non_english"] += 1
        return None
    teaser = strip_html(release.body_html or "")
    if not is_english(f"{headline}\n{teaser}"):
        counters["non_english"] += 1
        return None
    # Law-firm alerts give themselves away in the headline/teaser; dropping
    # them here saves the article fetch.
    blocked = issuer_mod.is_blocklisted(headline, teaser)
    if blocked:
        counters["blocklisted"] += 1
        log.info("PR drop [%s] %.80s — blocklist:%s", release.wire, headline, blocked)
        return None

    body_text = fetch_release_body(release)
    if not is_english(body_text):
        counters["non_english"] += 1
        return None

    reason = materiality.prefilter_reason(headline, body_text)
    if reason:
        counters["prefilter_dropped"] += 1
        log.debug("PR drop [%s] %.80s — %s", release.wire, headline, reason)
        return None

    blocked = issuer_mod.is_blocklisted(headline, body_text)
    if blocked:
        counters["blocklisted"] += 1
        log.info("PR drop [%s] %.80s — blocklist:%s", release.wire, headline, blocked)
        return None

    tickers = issuer_mod.extract_tickers(headline, body_text, release.metadata_tickers)
    if not tickers:
        counters["no_ticker"] += 1
        return None

    company, match_kind = issuer_mod.resolve_issuer(
        ticker_index, tickers, release.issuer_name, headline, body_text
    )
    if company is None:
        counters["issuer_fail"] += 1
        log.info("PR drop [%s] %.80s — issuer unresolved (tickers=%s)",
                 release.wire, headline, tickers)
        return None
    if match_kind == "unverified":
        if not issuer_mod.names_company(company["norm_name"], headline, body_text):
            counters["issuer_fail"] += 1
            log.info("PR drop [%s] %.80s — %s never named (tag without issuer)",
                     release.wire, headline, company["ticker"])
            return None
        counters["issuer_unverified"] += 1
        log.info("PR pass-through with unverified issuer [%s] %.80s ticker=%s",
                 release.wire, headline, company["ticker"])

    fingerprints = build_fingerprints(headline, body_text)
    if _is_cross_wire_dup(fp_entries, company["ticker"], fingerprints):
        counters["cross_wire_dup"] += 1
        log.info("PR drop [%s] %.80s — cross-wire duplicate", release.wire, headline)
        return None

    published_iso = _iso_published(release.published)
    counters["llm_calls"] += 1
    result = materiality.classify_release(
        headline, body_text, company["name"], company["ticker"], published_iso
    )
    if isinstance(result, materiality.Drop):
        counters["llm_dropped"] += 1
        log.info("PR drop [%s] %.80s — %s", release.wire, headline, result.reason)
        return None

    briefing = result
    # High significance rides the tier-1 alert/filter lane; Low (only
    # reachable with PR_REQUIRE_MATERIAL=0) is routine.
    max_tier = {"High": 1, "Low": 3}.get(briefing.significance, 2)

    return FilingEvent(
        edgar_id=_synthetic_edgar_id(release.wire, release.guid),
        signal_type="PR",
        cik=company["cik"],
        ticker=company["ticker"],
        company_name=company["name"],
        filing_date=published_iso,
        edgar_url=release.url,
        accession_number="",
        max_tier=max_tier,
        items=[],
        exhibits=[],
        briefing=FilingEventBriefing(
            # The release's own title, not an LLM rewrite: a press release
            # already has a headline written for readers (8-Ks don't).
            headline=headline,
            summary=briefing.summary,
            primary_event_type=briefing.primary_event_type,
            significance=briefing.significance,
            sentiment=briefing.sentiment,
            investor_takeaway=briefing.investor_takeaway,
            catalysts=briefing.catalysts,
            deal_terms=briefing.deal_terms,
            mode=briefing.mode,
        ),
        event_types=briefing.event_types,
        source=release.wire,
        issuer_name=release.issuer_name,
        content_fingerprint=fingerprints["exact"],
        headline_fingerprint=fingerprints["headline"],
        content_simhash=fingerprints["simhash"],
    )


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def _publish(event: FilingEvent, fp_entries: list[dict], counters: dict) -> None:
    """Publish (or, in dry-run, log) an event and remember its fingerprints
    for cross-wire dedup."""
    if _dry_run():
        log.info("PR DRY RUN would publish: [%s] %s ticker=%s tier=%d types=%s",
                 event.source, event.briefing.headline, event.ticker,
                 event.max_tier, event.event_types)
    else:
        publish_filing(event.to_json())
    counters["published"] += 1
    fp_entries.append({
        "ticker": event.ticker,
        "exact": event.content_fingerprint,
        "headline": event.headline_fingerprint,
        "simhash": event.content_simhash,
        "ts": datetime.now(timezone.utc).isoformat(),
    })


def _new_counters() -> dict:
    return {
        "fetched": 0, "non_english": 0, "prefilter_dropped": 0,
        "blocklisted": 0, "no_ticker": 0, "issuer_fail": 0,
        "issuer_unverified": 0, "cross_wire_dup": 0, "llm_calls": 0,
        "llm_dropped": 0, "llm_failed": 0, "published": 0,
        "stale": 0,
    }


async def pr_poll_loop(get_ticker_index) -> None:
    """Poll every enabled wire forever.

    get_ticker_index: zero-arg callable returning the current inverted
    ticker index (main.py owns the SEC ticker map and its 24h refresh).
    """
    loop = asyncio.get_event_loop()
    interval = _poll_interval()

    seen = seen_store.load_seen(PR_SEEN_FILE)
    fp_entries = _load_fingerprints()
    llm_attempts: dict[str, int] = {}       # seen-key → failed LLM attempts
    backoff: dict[str, float] = {}          # wire → not-before monotonic ts
    backoff_step: dict[str, int] = {}
    empty_polls: dict[str, int] = {}

    wires = enabled_wires()
    log.info("PR ingest enabled: %d wire(s) [%s], polling every %ds, seen=%d",
             len(wires), ", ".join(w.name for w in wires), interval, len(seen))

    while True:
        ticker_index = get_ticker_index()
        if not ticker_index:
            log.warning("PR poll skipped — ticker index empty")
            await asyncio.sleep(interval)
            continue

        counters = _new_counters()
        llm_budget = _LLM_CAP_PER_CYCLE

        for wire in wires:
            if time.monotonic() < backoff.get(wire.name, 0):
                continue

            wire_entries: int | None = None   # None = only 304s this cycle
            try:
                for feed_url in wire.resolved_feed_urls():
                    raw = await loop.run_in_executor(
                        None, lambda u=feed_url: fetch_pr_url(u, conditional=True)
                    )
                    if raw is None:      # 304 Not Modified — feed is healthy
                        continue
                    releases = parse_wire_feed(raw, wire)
                    wire_entries = (wire_entries or 0) + len(releases)

                    for release in reversed(releases):   # oldest first
                        key = f"{wire.name}:{release.guid}"
                        if key in seen:
                            continue
                        counters["fetched"] += 1
                        if llm_budget <= 0:
                            continue    # stays unseen — next cycle picks it up

                        llm_calls_before = counters["llm_calls"]
                        try:
                            event = await loop.run_in_executor(
                                None, process_release,
                                release, ticker_index, fp_entries, counters,
                            )
                        except Exception as exc:
                            llm_budget -= counters["llm_calls"] - llm_calls_before
                            counters["llm_failed"] += 1
                            attempts = llm_attempts.get(key, 0) + 1
                            if attempts >= _MAX_LLM_ATTEMPTS:
                                seen[key] = datetime.now(timezone.utc).isoformat()
                                llm_attempts.pop(key, None)
                                log.warning("PR giving up after %d attempts (%s): %s",
                                            attempts, key, exc)
                            else:
                                llm_attempts[key] = attempts
                                log.warning("PR classify failed attempt %d (%s): %s",
                                            attempts, key, exc)
                            continue

                        # Only actual LLM calls consume the per-cycle budget;
                        # deterministic drops are free.
                        llm_budget -= counters["llm_calls"] - llm_calls_before
                        seen[key] = datetime.now(timezone.utc).isoformat()
                        llm_attempts.pop(key, None)

                        if event is None:
                            continue

                        _publish(event, fp_entries, counters)
                        # Crash-safety like the 8-K loop: persist right after
                        # each published (LLM-expensive) event
                        seen_store.save_seen(seen, PR_SEEN_FILE)
                        _save_fingerprints(fp_entries)
                        log.info("Published PR: [%s] %s ticker=%s tier=%d",
                                 wire.name, event.briefing.headline,
                                 event.ticker, event.max_tier)

                backoff_step.pop(wire.name, None)
                if wire_entries == 0:   # parsed a fresh response, found no items
                    empty_polls[wire.name] = empty_polls.get(wire.name, 0) + 1
                    if empty_polls[wire.name] >= 5:
                        log.warning("pr_feed_empty wire=%s polls=%d — possible format drift",
                                    wire.name, empty_polls[wire.name])
                elif wire_entries is not None:
                    empty_polls[wire.name] = 0

            except Exception as exc:
                step = min(backoff_step.get(wire.name, 0) + 1, 5)
                backoff_step[wire.name] = step
                delay = min(60 * (2 ** (step - 1)), 1800)   # 60s → 30min cap
                backoff[wire.name] = time.monotonic() + delay
                log.warning("PR wire %s failed (%s) — backing off %ds",
                            wire.name, exc, delay)

            await asyncio.sleep(5)   # stagger between wires

        seen_store.save_seen(seen, PR_SEEN_FILE)
        _save_fingerprints(fp_entries)

        if counters["fetched"]:
            log.info("PR poll: %s", " ".join(f"{k}={v}" for k, v in counters.items() if v))

        await asyncio.sleep(interval)


# ---------------------------------------------------------------------------
# FMP poll loop (default source)
# ---------------------------------------------------------------------------
# Polling and processing are decoupled: the poller hits FMP every
# PR_FMP_POLL_SECONDS and only enqueues releases it hasn't seen; PR_WORKERS
# workers run the gate chain (article fetch + LLM) concurrently. A burst of
# releases at 8:00 ET therefore costs one LLM round-trip of latency, not
# one per release in the burst, and a slow LLM call never delays the poll.

_STATS_EVERY_SECONDS = 60


def _age_minutes(published_iso: str) -> float | None:
    try:
        dt = datetime.fromisoformat(published_iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 60


async def fmp_poll_loop(get_ticker_index) -> None:
    """Poll FMP's latest press releases forever.

    get_ticker_index: zero-arg callable returning the current inverted
    SEC ticker index — a release whose symbol isn't in it isn't a US
    SEC registrant and is dropped as `no_ticker`/`issuer_fail`.
    """
    loop = asyncio.get_running_loop()
    poll_every = _env_float("PR_FMP_POLL_SECONDS", 1.0, 0.25)
    workers = int(_env_float("PR_WORKERS", 4, 1))
    # Older than this when first seen → not news any more. Also what keeps
    # a cold start (empty seen-file after a deploy) from briefing the whole
    # backlog the endpoint returns.
    max_age = _env_float("PR_MAX_AGE_MINUTES", 30, 1)

    seen = seen_store.load_seen(PR_SEEN_FILE)
    fp_entries = _load_fingerprints()
    queue: asyncio.Queue = asyncio.Queue()
    in_flight: set[str] = set()
    attempts: dict[str, int] = {}
    retry_at: dict[str, float] = {}
    counters = _new_counters()
    state = {"dirty": False}

    def mark_seen(key: str) -> None:
        seen[key] = datetime.now(timezone.utc).isoformat()
        state["dirty"] = True

    async def worker() -> None:
        while True:
            release = await queue.get()
            key = f"fmp:{release.guid}"
            try:
                event = await loop.run_in_executor(
                    None, process_release,
                    release, get_ticker_index(), fp_entries, counters,
                )
            except Exception as exc:
                counters["llm_failed"] += 1
                n = attempts.get(key, 0) + 1
                if n >= _MAX_LLM_ATTEMPTS:
                    mark_seen(key)
                    attempts.pop(key, None)
                    log.warning("PR giving up after %d attempts (%s): %s", n, key, exc)
                else:
                    attempts[key] = n
                    retry_at[key] = time.monotonic() + 30 * n
                    log.warning("PR processing failed attempt %d (%s): %s", n, key, exc)
                event = None
            else:
                mark_seen(key)
                attempts.pop(key, None)
            finally:
                in_flight.discard(key)
                queue.task_done()

            if event is not None:
                _publish(event, fp_entries, counters)
                log.info("Published PR: [%s] %s ticker=%s tier=%d",
                         event.source, event.briefing.headline,
                         event.ticker, event.max_tier)
                # Crash-safety: persist right after each (LLM-expensive) publish
                seen_store.save_seen(seen, PR_SEEN_FILE)
                _save_fingerprints(fp_entries)
                state["dirty"] = False

    worker_tasks = [asyncio.create_task(worker()) for _ in range(workers)]
    log.info("PR ingest enabled: FMP every %.2fs, %d worker(s), max age %dm, seen=%d%s",
             poll_every, workers, max_age, len(seen),
             " [DRY RUN]" if _dry_run() else "")

    backoff_step = 0
    last_stats = time.monotonic()
    try:
        while True:
            started = time.monotonic()
            if not get_ticker_index():
                log.warning("PR poll skipped — ticker index empty")
                await asyncio.sleep(30)
                continue
            try:
                rows = await loop.run_in_executor(None, fmp.fetch_latest)
                backoff_step = 0
            except Exception as exc:
                backoff_step = min(backoff_step + 1, 6)
                delay = min(2 ** backoff_step, 60)
                log.warning("FMP press-release poll failed (%s) — retrying in %ds", exc, delay)
                await asyncio.sleep(delay)
                continue

            releases, other_wire = fmp.parse_fmp_rows(rows)
            for release in reversed(releases):          # oldest first
                key = f"fmp:{release.guid}"
                if key in seen or key in in_flight:
                    continue
                if retry_at.get(key, 0) > time.monotonic():
                    continue
                retry_at.pop(key, None)
                age = _age_minutes(release.published)
                if age is not None and age > max_age:
                    counters["stale"] += 1
                    mark_seen(key)
                    continue
                counters["fetched"] += 1
                in_flight.add(key)
                queue.put_nowait(release)

            if state["dirty"] and not in_flight:
                seen_store.save_seen(seen, PR_SEEN_FILE)
                state["dirty"] = False

            if time.monotonic() - last_stats >= _STATS_EVERY_SECONDS:
                if counters["fetched"] or counters["stale"]:
                    log.info("PR poll (last %ds): %s queue=%d other_wire_rows=%d",
                             _STATS_EVERY_SECONDS,
                             " ".join(f"{k}={v}" for k, v in counters.items() if v),
                             queue.qsize(), other_wire)
                # Reset in place: workers increment these keys from executor
                # threads, so they must never be missing.
                for k in counters:
                    counters[k] = 0
                last_stats = time.monotonic()

            await asyncio.sleep(max(0.0, poll_every - (time.monotonic() - started)))
    finally:
        for t in worker_tasks:
            t.cancel()
