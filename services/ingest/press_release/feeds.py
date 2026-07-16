"""
feeds.py — newswire RSS adapters + polite HTTP fetching.

One WireConfig per newswire. All four wires publish RSS 2.0; they differ
only in which item elements carry the issuing organization and tickers,
and in whether the item body is inlined (content:encoded / description)
or requires fetching the article page.

Feed URLs are defaults, overridable per wire via PR_FEED_URLS_<WIRE>
(comma-separated) so production can react to feed moves/drift without a
deploy. Fetching uses conditional GET (ETag / Last-Modified) — wires serve
the same document to every poller and 304s keep us polite.

VALIDATE BEFORE ENABLING: run `python tools/probe_pr_feeds.py` (from
services/ingest) against production egress to confirm each wire's feed
URL, issuer field, and body coverage; the fixtures in
tests/fixtures/pr/ encode the expected shapes.
"""

import gzip
import logging
import os
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_DC_NS = "http://purl.org/dc/elements/1.1/"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

# Bodies shorter than this (plain text) trigger an article-page fetch for
# wires that allow it — feed descriptions are often teaser-length.
_MIN_INLINE_BODY_CHARS = 600

# GlobeNewswire's feed endpoint is slow to first byte; 30s produced
# spurious timeouts in live probing.
_DEFAULT_TIMEOUT = 60


def _user_agent() -> str:
    # Deliberately not SEC_USER_AGENT (that one is EDGAR-specific and embeds
    # a contact email in SEC's required format).
    return os.environ.get(
        "PR_USER_AGENT",
        "Mozilla/5.0 (compatible; SensybullFeedReader/1.0)",
    )


@dataclass
class PRRelease:
    guid: str
    wire: str
    url: str
    headline: str
    body_html: str
    published: str                      # ISO-8601 or RFC-822 as published
    issuer_name: str = ""               # structured issuer field, "" if wire has none
    metadata_tickers: list[str] = field(default_factory=list)
    raw_categories: list[str] = field(default_factory=list)
    language: str = ""                  # dc:language when the wire provides it


@dataclass
class WireConfig:
    name: str
    feed_urls: list[str]
    issuer_tags: list[str] = field(default_factory=list)   # item child tags holding issuer
    ticker_tags: list[str] = field(default_factory=list)   # item child tags holding tickers
    fetch_article_when_thin: bool = True

    def resolved_feed_urls(self) -> list[str]:
        override = os.environ.get(f"PR_FEED_URLS_{self.name.upper()}", "")
        urls = [u.strip() for u in override.split(",") if u.strip()]
        return urls or self.feed_urls


# ---------------------------------------------------------------------------
# HTTP (conditional GET; per-URL cache validators kept in memory)
# ---------------------------------------------------------------------------

_validators: dict[str, dict[str, str]] = {}


def fetch_pr_url(url: str, retries: int = 3, conditional: bool = False) -> bytes | None:
    """Fetch a URL with retries/backoff.

    With conditional=True, sends stored ETag/Last-Modified and returns None
    on 304 Not Modified. Raises on final failure like fetcher.fetch_url.
    """
    headers = {
        "User-Agent": _user_agent(),
        "Accept-Encoding": "gzip",
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    if conditional:
        v = _validators.get(url, {})
        if v.get("etag"):
            headers["If-None-Match"] = v["etag"]
        if v.get("last_modified"):
            headers["If-Modified-Since"] = v["last_modified"]

    req = urllib.request.Request(url, headers=headers)
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=_DEFAULT_TIMEOUT) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "") == "gzip":
                    raw = gzip.decompress(raw)
                if conditional:
                    _validators[url] = {
                        "etag": resp.headers.get("ETag", ""),
                        "last_modified": resp.headers.get("Last-Modified", ""),
                    }
                return raw
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return None
            last_exc = exc
        except Exception as exc:
            last_exc = exc
        if attempt < retries - 1:
            time.sleep(2 ** attempt)
    raise last_exc


# ---------------------------------------------------------------------------
# Generic RSS 2.0 parsing
# ---------------------------------------------------------------------------

def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _item_fields(item: ET.Element) -> dict[str, list[str]]:
    """Collect item children as {localname: [texts]} (namespace-agnostic)."""
    fields: dict[str, list[str]] = {}
    for child in item:
        text = (child.text or "").strip()
        if text:
            fields.setdefault(_localname(child.tag), []).append(text)
    return fields


def parse_wire_feed(data: bytes, config: WireConfig) -> list[PRRelease]:
    """Parse one RSS document for a wire. Returns [] on any structural failure."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        log.warning("PR feed parse error (%s): %s", config.name, exc)
        return []

    releases: list[PRRelease] = []
    for item in root.iter("item"):
        f = _item_fields(item)
        url = (f.get("link") or [""])[0]
        guid = (f.get("guid") or [url])[0]
        headline = (f.get("title") or [""])[0]
        if not guid or not headline:
            continue

        # Prefer the full-content module over the (often teaser) description
        body_html = (f.get("encoded") or f.get("description") or [""])[0]
        published = (f.get("pubdate") or f.get("date") or [""])[0]

        issuer = ""
        for tag in config.issuer_tags:
            vals = f.get(tag.lower())
            if vals:
                issuer = vals[0]
                break

        tickers: list[str] = []
        for tag in config.ticker_tags:
            for val in f.get(tag.lower(), []):
                tickers.extend(t.strip() for t in val.split(",") if t.strip())

        releases.append(PRRelease(
            guid=guid,
            wire=config.name,
            url=url,
            headline=headline,
            body_html=body_html,
            published=published,
            issuer_name=issuer,
            metadata_tickers=tickers,
            raw_categories=f.get("category", []),
            language=(f.get("language") or [""])[0],
        ))
    return releases


def fetch_release_body(release: PRRelease) -> str:
    """Return the release's body HTML, fetching the article page when the
    inline body is teaser-thin. Never raises."""
    if len(release.body_html) >= _MIN_INLINE_BODY_CHARS or not release.url:
        return release.body_html
    try:
        raw = fetch_pr_url(release.url, retries=2)
        if raw:
            return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        log.debug("Article fetch failed (%s): %s", release.url, exc)
    return release.body_html


# ---------------------------------------------------------------------------
# Wire registry
# ---------------------------------------------------------------------------
# Feed URLs and field mappings reflect each wire's documented/observed RSS
# shape; confirm with tools/probe_pr_feeds.py before enabling in production
# (this sandbox has no egress to the wires). All are config-overridable.

WIRES: dict[str, WireConfig] = {
    "globenewswire": WireConfig(
        name="globenewswire",
        feed_urls=[
            # Org-class 1 = public companies
            "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/"
            "GlobeNewswire%20-%20News%20about%20Public%20Companies",
        ],
        issuer_tags=["contributor", "creator"],   # dc:contributor / dc:creator
        # Live probe (2026-07-16): the ticker rides in
        # <category domain=".../rss/stock">Nasdaq:RLMD</category>. Category
        # values also include ISINs — extract_tickers' shape check drops
        # those. (dc:identifier is an internal release number, NOT a ticker.)
        ticker_tags=["category"],
    ),
    "prnewswire": WireConfig(
        name="prnewswire",
        feed_urls=["https://www.prnewswire.com/rss/news-releases-list.rss"],
        # Live probe (2026-07-16): items carry dc:contributor = issuing org
        issuer_tags=["contributor"],
        ticker_tags=[],
    ),
    "businesswire": WireConfig(
        name="businesswire",
        # No stable public all-news token confirmed — ships disabled until a
        # URL is provided via PR_FEED_URLS_BUSINESSWIRE.
        feed_urls=[],
        issuer_tags=["creator"],
        ticker_tags=[],
    ),
    "accesswire": WireConfig(
        name="accesswire",
        # Live probe (2026-07-16): accesswire.com/rss/latest serves an HTML
        # app page, not RSS — no public feed URL confirmed. Ships disabled
        # until one is provided via PR_FEED_URLS_ACCESSWIRE.
        feed_urls=[],
        issuer_tags=[],
        ticker_tags=[],
    ),
}


def enabled_wires() -> list[WireConfig]:
    """Wires with at least one (default or overridden) feed URL."""
    return [w for w in WIRES.values() if w.resolved_feed_urls()]
