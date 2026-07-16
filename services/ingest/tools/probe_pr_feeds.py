"""
probe_pr_feeds.py — one-shot field inventory of the newswire RSS feeds.

Run from services/ingest before enabling PR ingest in an environment:

    PYTHONPATH=. python tools/probe_pr_feeds.py

For each configured wire (press_release/feeds.py WIRES, honoring
PR_FEED_URLS_<WIRE> overrides) it fetches the feed, reports HTTP status,
item count, which item elements are present (issuer/ticker/body fields),
and prints the first item raw — enough to confirm the adapter mappings
and capture a fixture for tests/fixtures/pr/.
"""

import sys
import xml.etree.ElementTree as ET
from collections import Counter

from press_release.feeds import WIRES, fetch_pr_url


def probe(name: str) -> None:
    config = WIRES[name]
    urls = config.resolved_feed_urls()
    if not urls:
        print(f"\n=== {name}: no feed URL configured (set PR_FEED_URLS_{name.upper()}) ===")
        return
    for url in urls:
        print(f"\n=== {name}: {url} ===")
        try:
            raw = fetch_pr_url(url, retries=1)
        except Exception as exc:
            print(f"  FETCH FAILED: {exc}")
            continue
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            print(f"  NOT XML: {exc}; first 300 bytes: {raw[:300]!r}")
            continue

        items = list(root.iter("item"))
        print(f"  items: {len(items)}")
        if not items:
            continue

        tag_counts: Counter = Counter()
        for item in items:
            for child in item:
                tag_counts[child.tag] += 1
        print("  item child elements (tag: count):")
        for tag, count in tag_counts.most_common():
            print(f"    {tag}: {count}")

        print("  --- first item raw ---")
        print("  " + ET.tostring(items[0], encoding="unicode")[:2000])


if __name__ == "__main__":
    names = sys.argv[1:] or list(WIRES)
    for name in names:
        probe(name)
