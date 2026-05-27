"""
main.py — 8-K ingest pipeline.

Polls SEC EDGAR for new 8-K filings, extracts items and exhibits,
generates LLM briefings, and publishes events to Redis.
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


async def poll_loop() -> None:
    loop = asyncio.get_event_loop()

    log.info("Loading ticker map...")
    ticker_map = await loop.run_in_executor(None, load_ticker_map)
    log.info("Ticker map loaded (%d entries)", len(ticker_map))
    last_ticker_refresh = time.time()

    seen = load_seen()
    log.info("Seen set loaded (%d entries). Polling every %ds.", len(seen), POLL_INTERVAL)

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

        try:
            root = await loop.run_in_executor(None, fetch_feed)
            entries = parse_feed_entries(root)
            new = [e for e in entries if e["id"] not in seen]

            if new:
                log.info("%d new filing(s) found", len(new))

            for entry in reversed(new):
                try:
                    detail = await loop.run_in_executor(
                        None, fetch_filing_detail, entry["url"]
                    )
                    filing = build_filing(entry, detail, ticker_map)

                    fetchable = [
                        ex for ex in detail.get("exhibits", [])
                        if _is_fetchable_exhibit(ex["type"])
                    ][:3]
                    exhibit_texts: dict[str, str] = {}
                    for ex in fetchable:
                        html = await loop.run_in_executor(
                            None, fetch_exhibit_text, ex["url"]
                        )
                        if html:
                            exhibit_texts[ex["type"]] = html

                    filing.briefing = await loop.run_in_executor(
                        None, generate_briefing, filing, exhibit_texts
                    )

                    accession = ""
                    m = re.search(r"/(\d{18})/", filing.url)
                    if m:
                        raw = m.group(1)
                        accession = f"{raw[:10]}-{raw[10:12]}-{raw[12:]}"

                    event_types = (
                        filing.briefing.event_types if filing.briefing else ["Other"]
                    )

                    event = FilingEvent(
                        edgar_id=entry["id"],
                        signal_type="8-K",
                        cik=filing.cik,
                        ticker=filing.ticker or "",
                        company_name=filing.title,
                        filing_date=filing.updated,
                        edgar_url=filing.url,
                        accession_number=accession,
                        max_tier=min((it.tier for it in filing.items), default=3),
                        items=[
                            FilingEventItem(
                                number=it.number,
                                title=it.title,
                                tier=it.tier,
                                category=it.category,
                                text=it.text,
                            )
                            for it in filing.items
                        ],
                        exhibits=[
                            FilingEventExhibit(
                                type=ex.type,
                                description=ex.description,
                                url=ex.url,
                            )
                            for ex in filing.exhibits
                        ],
                        briefing=FilingEventBriefing(
                            headline=filing.briefing.headline,
                            bullets=filing.briefing.bullets,
                            company_context=filing.briefing.company_context,
                        ) if filing.briefing else None,
                        event_types=event_types,
                    )
                    publish_filing(event.to_json())

                    seen[entry["id"]] = entry["updated"]
                    save_seen(seen)
                    log.info("Published: %s  ticker=%s  items=%d",
                             filing.title, filing.ticker or "—", len(filing.items))
                except Exception as exc:
                    log.warning("Skipping %s: %s", entry.get("id", "?"), exc)

        except Exception as exc:
            log.warning("Feed fetch failed: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(poll_loop())
