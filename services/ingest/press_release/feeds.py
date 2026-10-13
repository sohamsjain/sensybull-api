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
import threading
import time
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from parser import strip_html
from press_release.article import extract_issuer, extract_release_text

log = logging.getLogger(__name__)

_DC_NS = "http://purl.org/dc/elements/1.1/"
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

# Bodies shorter than this (plain text) trigger an article-page fetch for
# wires that allow it — feed descriptions are often teaser-length.
_MIN_INLINE_BODY_CHARS = 600

# GlobeNewswire's feed endpoint is slow to first byte; 30s produced
# spurious timeouts in live probing.
_DEFAULT_TIMEOUT = 60
# Article pages are on the latency path of every release: fail fast and
# fall back to the teaser rather than hold a worker for a minute.
_ARTICLE_TIMEOUT = 10


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
    # The inline body is only a teaser whatever its length (FMP's `text`),
    # so the article page is always fetched.
    body_is_teaser: bool = False
    # Set by fetch_release_body when the text came from the article page
    # rather than the inline body/teaser.
    body_from_article: bool = False
    fetch_error: str = ""               # why the article fetch failed, if it did


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


# Article pages are HTML for readers; asking for them with the feed's
# RSS Accept header is the kind of mismatch a wire's bot filter rejects.
_HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def fetch_pr_url(url: str, retries: int = 3, conditional: bool = False,
                 timeout: float = _DEFAULT_TIMEOUT, html: bool = False) -> bytes | None:
    """Fetch a URL with retries/backoff.

    With conditional=True, sends stored ETag/Last-Modified and returns None
    on 304 Not Modified. Raises on final failure like fetcher.fetch_url.
    """
    headers = {
        "User-Agent": _user_agent(),
        "Accept-Encoding": "gzip",
        "Accept": _HTML_ACCEPT if html else "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    }
    if html:
        headers["Accept-Language"] = "en-US,en;q=0.9"
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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


# A wire host that fails this many article fetches in a row is skipped for
# _HOST_COOLDOWN seconds, then tried again once. Oct 2026: every Business
# Wire fetch timed out, and each one held a worker 21s (10s × 2 tries) to
# end up with the teaser anyway.
_HOST_FAILURE_LIMIT = 3
_HOST_COOLDOWN = 600
_host_lock = threading.Lock()
_host_failures: dict[str, int] = {}
_host_skip_until: dict[str, float] = {}


def _host_of(url: str) -> str:
    return (urllib.parse.urlsplit(url).hostname or "").lower()


def _host_skipped(host: str) -> bool:
    with _host_lock:
        return time.monotonic() < _host_skip_until.get(host, 0.0)


def _record_fetch(host: str, ok: bool) -> None:
    with _host_lock:
        if ok:
            if _host_failures.pop(host, 0) >= _HOST_FAILURE_LIMIT:
                log.info("Article fetches from %s are working again", host)
            _host_skip_until.pop(host, None)
            return
        n = _host_failures.get(host, 0) + 1
        _host_failures[host] = n
        if n >= _HOST_FAILURE_LIMIT:
            _host_skip_until[host] = time.monotonic() + _HOST_COOLDOWN
            log.warning("Article fetches from %s failed %d times in a row — "
                        "skipping it for %ds", host, n, _HOST_COOLDOWN)


def fetch_release_body(release: PRRelease) -> str:
    """Plain text of the release, fetching the article page when the inline
    body is a teaser. Never raises.

    From a fetched page only the release itself is kept (dateline to source
    footer, see article.py) — the whole page is mostly navigation, which
    would crowd the release out of the ticker scan and the LLM prompt.
    The page's issuer credit (PR Newswire's "News provided by") fills
    release.issuer_name when the source gave none.
    """
    inline = strip_html(release.body_html).strip() if release.body_html else ""
    thin = release.body_is_teaser or len(inline) < _MIN_INLINE_BODY_CHARS
    if not thin or not release.url:
        return inline
    host = _host_of(release.url)
    if _host_skipped(host):
        release.fetch_error = f"{host} skipped after repeated failures"
        return inline
    try:
        raw = fetch_pr_url(release.url, retries=2, timeout=_ARTICLE_TIMEOUT, html=True)
        _record_fetch(host, True)
        if raw:
            page = raw.decode("utf-8", errors="replace")
            if not release.issuer_name:
                release.issuer_name = extract_issuer(page)
            extracted = extract_release_text(page, release.headline)
            if len(extracted) > len(inline):
                release.body_from_article = True
                return extracted
            log.info("Article page has no recognisable release text (%s)", release.url)
    except Exception as exc:
        # INFO, not DEBUG: a wire that 403s or times out silently turns
        # every release from it into a teaser, which is worth seeing.
        log.info("Article fetch failed (%s): %s", release.url, exc)
        release.fetch_error = str(exc)
        _record_fetch(host, False)
    return inline


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
