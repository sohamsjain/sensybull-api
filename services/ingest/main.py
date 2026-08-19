"""
main.py — ingest worker: SEC 8-K pipeline + press-release pipeline.

The 8-K loop polls SEC EDGAR for new 8-K filings (the only ingested form
family after the July 2026 multi-form rollback), extracts Item sections,
generates LLM briefings, and publishes events to Redis.

The press-release loop (press_release/pipeline.py, off unless
PR_INGEST_ENABLED=1) polls newswire RSS feeds and publishes first-party
material announcements through the same Redis contract. Both loops run
in this one worker, each under a crash-restart supervisor.
"""

import asyncio
import logging
import re

from briefing import generate_briefing
from events import FilingEvent, FilingEventBriefing, FilingEventExhibit, FilingEventItem
from fetcher import (
    POLL_INTERVAL,
    _is_fetchable_exhibit,
    fetch_exhibit_text,
    fetch_feed,
    fetch_filing_detail,
    load_ticker_map,
    parse_feed_entries,
)
from forms import ALLOWED_FORMS
from parser import build_filing, strip_html
from taxonomy import TAXONOMY_VERSION
from press_release.fingerprint import build_fingerprints
from press_release.issuer import build_ticker_index
from press_release.pipeline import pr_ingest_enabled, pr_poll_loop
from publisher import publish_filing
from seen import load_seen, save_seen

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TICKER_REFRESH_INTERVAL = 24 * 60 * 60


class TickerMaps:
    """Shared SEC ticker map + inverted index, refreshed daily in one place
    so both pipelines see the same data."""

    def __init__(self) -> None:
        self.cik_map: dict[str, dict] = {}      # padded CIK → {ticker, name}
        self.ticker_index: dict[str, dict] = {} # TICKER → {cik, name, norm_name}

    def update(self, cik_map: dict[str, dict]) -> None:
        if cik_map:
            self.cik_map = cik_map
            self.ticker_index = build_ticker_index(cik_map)


def _accession_from_url(url: str) -> str:
    m = re.search(r"/(\d{18})/", url)
    if not m:
        return ""
    raw = m.group(1)
    return f"{raw[:10]}-{raw[10:12]}-{raw[12:]}"


def _briefing_payload(briefing) -> FilingEventBriefing | None:
    if briefing is None:
        return None
    return FilingEventBriefing(
        headline=briefing.headline,
        summary=briefing.summary,
        primary_event_type=briefing.primary_event_type,
        significance=briefing.significance,
        sentiment=briefing.sentiment,
        investor_takeaway=briefing.investor_takeaway,
        catalysts=briefing.catalysts,
        deal_terms=briefing.deal_terms,
        taxonomy=briefing.taxonomy,
        taxonomy_version=TAXONOMY_VERSION if briefing.taxonomy else "",
        mode=briefing.mode,
    )


def _derived_headline(text: str) -> str:
    """First substantive line of an exhibit/primary body — press releases
    embedded as EX-99 open with their own headline, which is what a
    matching wire event hashed. Skips exhibit captions and short fragments."""
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 25:
            continue
        if re.match(r"(?i)^exhibit\s", s):
            continue
        return s[:200]
    return ""


def _exhibit_fingerprints(exhibit_texts: dict[str, str], primary_plain: str) -> list[dict]:
    """Fingerprints of each fetched exhibit + the primary document, used by
    the API to match this filing against an already-published PR event."""
    out: list[dict] = []
    for ex_type, html in exhibit_texts.items():
        plain = strip_html(html).strip()
        if not plain:
            continue
        fp = build_fingerprints(_derived_headline(plain), plain)
        out.append({"source": ex_type, **fp})
    if primary_plain:
        fp = build_fingerprints(_derived_headline(primary_plain), primary_plain)
        out.append({"source": "primary", **fp})
    return out


async def _process_entry(entry: dict, ticker_map: dict, loop) -> None:
    """8-K pipeline: fetch → parse items → LLM briefing → publish."""
    detail = await loop.run_in_executor(
        None, fetch_filing_detail, entry["url"], entry["form_type"]
    )
    filing = build_filing(entry, detail, ticker_map)

    fetchable = [
        ex for ex in detail.get("exhibits", [])
        if _is_fetchable_exhibit(ex["type"])
    ][:3]
    exhibit_texts: dict[str, str] = {}
    for ex in fetchable:
        html = await loop.run_in_executor(None, fetch_exhibit_text, ex["url"])
        if html:
            exhibit_texts[ex["type"]] = html

    filing.briefing = await loop.run_in_executor(
        None, generate_briefing, filing, exhibit_texts
    )

    max_tier = min((it.tier for it in filing.items), default=3)

    primary_plain = strip_html(detail.get("primary_html", "")).strip() \
        if detail.get("primary_html") else ""
    exhibit_fps = _exhibit_fingerprints(exhibit_texts, primary_plain)
    # The filing's own fingerprint mirrors its first EX-99 (the embedded
    # press release) so a late-arriving wire copy can be matched against it.
    own_fp = exhibit_fps[0] if exhibit_fps else {}

    event = FilingEvent(
        edgar_id=entry["id"],
        signal_type=entry["form_type"],
        cik=filing.cik,
        ticker=filing.ticker or "",
        company_name=filing.title,
        filing_date=filing.updated,
        edgar_url=filing.url,
        accession_number=_accession_from_url(filing.url),
        max_tier=max_tier,
        items=[
            FilingEventItem(
                number=it.number, title=it.title, tier=it.tier,
                category=it.category, text=it.text,
            )
            for it in filing.items
        ],
        exhibits=[
            FilingEventExhibit(type=ex.type, description=ex.description, url=ex.url)
            for ex in filing.exhibits
        ],
        briefing=_briefing_payload(filing.briefing),
        event_types=filing.briefing.event_types if filing.briefing else ["Other"],
        source="edgar",
        content_fingerprint=own_fp.get("exact", ""),
        headline_fingerprint=own_fp.get("headline", ""),
        content_simhash=own_fp.get("simhash", ""),
        exhibit_fingerprints=exhibit_fps,
    )
    publish_filing(event.to_json())
    log.info("Published: [%s] %s  ticker=%s  tier=%d",
             entry["form_type"], filing.title, filing.ticker or "—", max_tier)


async def poll_loop(tickers: TickerMaps) -> None:
    loop = asyncio.get_event_loop()

    seen = load_seen()
    log.info("Seen set loaded (%d entries). Ingesting %s. Polling every %ds.",
             len(seen), ", ".join(sorted(ALLOWED_FORMS)), POLL_INTERVAL)

    while True:
        try:
            root = await loop.run_in_executor(None, fetch_feed, "8-K")
            entries = parse_feed_entries(root)

            published = 0
            for entry in reversed(entries):  # oldest first
                form_type = entry.get("form_type", "")
                if form_type not in ALLOWED_FORMS:
                    continue  # exact whitelist: prefix noise dies here
                if entry["id"] in seen:
                    continue

                try:
                    await _process_entry(entry, tickers.cik_map, loop)
                    seen[entry["id"]] = entry["updated"]
                    published += 1
                    # Crash-safety: persist immediately after each
                    # published event (LLM work is expensive to redo)
                    save_seen(seen)
                except Exception as exc:
                    log.warning("Skipping %s: %s", entry.get("id", "?"), exc)

            # One batched save per poll covers skipped/noise entries
            save_seen(seen)
            if published:
                log.info("Published %d event(s)", published)
        except Exception as exc:
            log.warning("Feed fetch failed: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


async def ticker_refresh_loop(tickers: TickerMaps) -> None:
    """Owns the SEC ticker map: initial load + 24h refresh, shared by both
    pipelines via the TickerMaps holder."""
    loop = asyncio.get_event_loop()
    if tickers.cik_map:
        await asyncio.sleep(TICKER_REFRESH_INTERVAL)  # main() just loaded it
    while True:
        fresh = await loop.run_in_executor(None, load_ticker_map)
        if fresh:
            tickers.update(fresh)
            log.info("Ticker map refreshed (%d entries)", len(fresh))
        elif not tickers.cik_map:
            log.warning("Ticker map load failed and no cached copy — retrying in 60s")
            await asyncio.sleep(60)
            continue
        else:
            log.warning("Ticker map refresh returned empty — keeping stale copy")
        await asyncio.sleep(TICKER_REFRESH_INTERVAL)


async def _supervise(name: str, coro_factory) -> None:
    """Restart a pipeline task if it ever crashes — one bad pipeline must
    never take down the other."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Pipeline %r crashed — restarting in 30s", name)
            await asyncio.sleep(30)


async def main() -> None:
    tickers = TickerMaps()

    log.info("Loading ticker map...")
    tickers.update(load_ticker_map())
    log.info("Ticker map loaded (%d entries)", len(tickers.cik_map))
    if not tickers.cik_map:
        log.warning("Initial ticker map load failed — refresh loop will retry")

    tasks = [
        _supervise("ticker-refresh", lambda: ticker_refresh_loop(tickers)),
        _supervise("edgar", lambda: poll_loop(tickers)),
    ]
    if pr_ingest_enabled():
        tasks.append(_supervise("press-release",
                                lambda: pr_poll_loop(lambda: tickers.ticker_index)))
    else:
        log.info("Press-release ingest disabled (set PR_INGEST_ENABLED=1 to enable)")

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
