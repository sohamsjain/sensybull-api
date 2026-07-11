"""
main.py — multi-form SEC ingest pipeline.

Polls SEC EDGAR for new filings across the event-driven form types in
forms.FORM_REGISTRY (8-K, SC 13D, tenders, merger/contested proxies,
delistings, NT late-filings, Form 4 insider buys, ...), extracts content
per form strategy, generates LLM briefings, and publishes events to Redis.
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
    fetch_form4_xml,
    load_ticker_map,
    parse_feed_entries,
)
from forms import FormSpec, active_feed_queries, enabled_forms, get_spec
from form4 import (
    CLUSTER_MIN_INSIDERS,
    MIN_BUY_USD,
    build_form4_briefing,
    parse_form4_xml,
    qualifying_buy_value,
)
from form4_state import load_buys, record_buy, save_buys
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


async def _process_document_entry(entry: dict, spec: FormSpec,
                                  ticker_map: dict, loop) -> None:
    """8-K / ownership / document strategies: fetch → parse → LLM → publish."""
    want_header = spec.subject_from == "feed_subject"
    detail = await loop.run_in_executor(
        None, fetch_filing_detail, entry["url"], spec.form, want_header
    )
    filing = build_filing(entry, detail, ticker_map, spec)

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

    if spec.strategy == "8k_items":
        max_tier = min((it.tier for it in filing.items), default=3)
    else:
        max_tier = spec.tier

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
        filed_by=filing.filed_by,
    )
    publish_filing(event.to_json())
    log.info("Published: [%s] %s  ticker=%s  tier=%d",
             entry["form_type"], filing.title, filing.ticker or "—", max_tier)


async def _process_form4_entry(entry: dict, ticker_map: dict, loop,
                               form4_state: dict) -> bool:
    """Form 4: XML parse → qualify → cluster check → publish. No LLM.

    Returns True if an event was published (most Form 4s are noise and
    return False after being marked seen by the caller).
    """
    xml_text = await loop.run_in_executor(None, fetch_form4_xml, entry["url"])
    f4 = parse_form4_xml(xml_text)
    if f4 is None:
        return False

    value = qualifying_buy_value(f4, MIN_BUY_USD)
    if not value:
        return False

    accession = _accession_from_url(entry["url"])
    window_buys = record_buy(form4_state, f4.issuer_cik, {
        "owner_cik": f4.owner_cik,
        "owner_name": f4.owner_name,
        "value": value,
        "date": entry["updated"],
        "accession": accession,
    })
    save_buys(form4_state)

    distinct_owners = {b["owner_cik"] for b in window_buys}
    max_tier = 1 if len(distinct_owners) >= CLUSTER_MIN_INSIDERS else 2

    briefing = build_form4_briefing(f4, value, window_buys)
    ticker = f4.issuer_ticker or ticker_map.get(f4.issuer_cik, {}).get("ticker", "")

    event = FilingEvent(
        edgar_id=entry["id"],
        signal_type="4",
        cik=f4.issuer_cik,
        ticker=ticker,
        company_name=f4.issuer_name or entry["title"],
        filing_date=entry["updated"],
        edgar_url=entry["url"],
        accession_number=accession,
        max_tier=max_tier,
        items=[],
        exhibits=[],
        briefing=_briefing_payload(briefing),
        event_types=briefing.event_types,
        filed_by=f4.owner_name,
    )
    publish_filing(event.to_json())
    log.info("Published: [4] %s  %s  tier=%d", ticker or f4.issuer_name,
             briefing.headline, max_tier)
    return True


async def poll_loop() -> None:
    loop = asyncio.get_event_loop()

    log.info("Loading ticker map...")
    ticker_map = await loop.run_in_executor(None, load_ticker_map)
    log.info("Ticker map loaded (%d entries)", len(ticker_map))
    last_ticker_refresh = time.time()

    seen = load_seen()
    form4_state = load_buys()
    enabled = enabled_forms()
    queries = active_feed_queries()
    log.info("Seen set loaded (%d entries). %d forms enabled, %d feed queries. "
             "Polling every %ds.", len(seen), len(enabled), len(queries), POLL_INTERVAL)

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

        for query in queries:
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
                    spec = get_spec(form_type)
                    if spec is None or form_type not in enabled:
                        continue  # exact whitelist: amendments/prefix noise die here

                    # Subject filings appear once per associated company
                    # (subject + filer twins share an accession) — the
                    # acc: key ensures exactly one event per filing. Subject
                    # attribution comes from the index-page header inside
                    # build_filing, so either twin is safe to process.
                    accession = _accession_from_url(entry["url"])
                    acc_key = f"acc:{accession}" if accession else ""
                    if entry["id"] in seen or (acc_key and acc_key in seen):
                        continue

                    try:
                        if spec.strategy == "form4_xml":
                            did_publish = await _process_form4_entry(
                                entry, ticker_map, loop, form4_state)
                        else:
                            await _process_document_entry(entry, spec, ticker_map, loop)
                            did_publish = True

                        seen[entry["id"]] = entry["updated"]
                        if acc_key:
                            seen[acc_key] = entry["updated"]
                        if did_publish:
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
