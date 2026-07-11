"""
main.py — SEC 8-K ingest pipeline.

Polls SEC EDGAR for new 8-K filings (the only ingested form family after
the July 2026 multi-form rollback), extracts Item sections, generates LLM
briefings, and publishes events to Redis.
"""

import asyncio
import logging
import re
import time

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
from forms import FEED_QUERIES, FORM_REGISTRY
from parser import build_filing
from publisher import publish_filing
from seen import load_seen, save_seen

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TICKER_REFRESH_INTERVAL = 24 * 60 * 60
FEED_QUERY_DELAY = 0.25  # politeness between getcurrent requests


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
        mode=briefing.mode,
    )


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
    )
    publish_filing(event.to_json())
    log.info("Published: [%s] %s  ticker=%s  tier=%d",
             entry["form_type"], filing.title, filing.ticker or "—", max_tier)


async def poll_loop() -> None:
    loop = asyncio.get_event_loop()

    log.info("Loading ticker map...")
    ticker_map = await loop.run_in_executor(None, load_ticker_map)
    log.info("Ticker map loaded (%d entries)", len(ticker_map))
    last_ticker_refresh = time.time()

    seen = load_seen()
    log.info("Seen set loaded (%d entries). Ingesting %s. Polling every %ds.",
             len(seen), ", ".join(FORM_REGISTRY), POLL_INTERVAL)

    while True:
        if time.time() - last_ticker_refresh >= TICKER_REFRESH_INTERVAL:
            log.info("Refreshing ticker map...")
            fresh = await loop.run_in_executor(None, load_ticker_map)
            if fresh:
                ticker_map = fresh
                last_ticker_refresh = time.time()
                log.info("Ticker map refreshed (%d entries)", len(ticker_map))
            else:
                log.warning("Ticker map refresh returned empty — keeping stale copy")

        for query in FEED_QUERIES:
            try:
                entries: list[dict] = []
                for page in range(query.pages):
                    root = await loop.run_in_executor(
                        None, fetch_feed, query.type_param, query.count,
                        page * query.count,
                    )
                    page_entries = parse_feed_entries(root)
                    entries.extend(page_entries)
                    if len(page_entries) < query.count:
                        break  # last page
                    await asyncio.sleep(FEED_QUERY_DELAY)

                published = 0
                for entry in reversed(entries):  # oldest first
                    form_type = entry.get("form_type", "")
                    if form_type not in FORM_REGISTRY:
                        continue  # exact whitelist: prefix noise dies here
                    if entry["id"] in seen:
                        continue

                    try:
                        await _process_entry(entry, ticker_map, loop)
                        seen[entry["id"]] = entry["updated"]
                        published += 1
                        # Crash-safety: persist immediately after each
                        # published event (LLM work is expensive to redo)
                        save_seen(seen)
                    except Exception as exc:
                        log.warning("Skipping %s: %s", entry.get("id", "?"), exc)

                # One batched save per query covers skipped/noise entries
                save_seen(seen)
                if published:
                    log.info("[%s] published %d event(s)", query.type_param, published)
            except Exception as exc:
                log.warning("Feed fetch failed for type=%s: %s", query.type_param, exc)

            await asyncio.sleep(FEED_QUERY_DELAY)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(poll_loop())
