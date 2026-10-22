"""
article.py — the release text out of a wire's article page.

A fetched article page is mostly chrome: navigation, share buttons,
"related news", cookie banners. Feeding the whole page to the ticker scan
(first 1,500 chars) and the LLM (first 12k chars) means both read menus
instead of the release. Every wire stamps the release's first line with a
dateline marker and closes it with a source footer, so the release is the
span between the two.
"""

import re

from parser import strip_html

# "HOUSTON, Oct. 10, 2026 /PRNewswire/ --", "(GLOBE NEWSWIRE) --",
# "(BUSINESS WIRE)--", "/ACCESS Newswire/ --", legacy "/ACCESSWIRE/".
_DATELINE_RE = re.compile(
    r"/\s*PR\s?Newswire\s*/"
    r"|\(\s*GLOBE\s+NEWSWIRE\s*\)"
    r"|\(\s*BUSINESS\s+WIRE\s*\)"
    r"|/\s*ACCESS\s?Newswire\s*/"
    r"|/\s*ACCESSWIRE\s*/",
    re.IGNORECASE,
)

_FOOTER_RE = re.compile(
    r"^\s*(View original content"
    r"|SOURCE\s"
    r"|View source version on businesswire\.com"
    r"|View the original press release on"
    r"|Primary Logo"
    r"|Tags:)",
    re.IGNORECASE | re.MULTILINE,
)


# PR Newswire credits the issuing organization above every release:
# "NEWS PROVIDED BY / CenterPoint Energy →" (the name links to a stable
# /news/<org-slug>/ profile). Law-firm alerts are credited to the firm, so
# this is the structured issuer field the issuer check needs.
_PROVIDED_BY_RE = re.compile(r"News provided by\s*\n\s*([^\n]{2,120})", re.IGNORECASE)


# GlobeNewswire credits it under the headline: "October 22, 2026 08:00 ET |
# Source: Eos SENOLYTIX, Inc." (the name links to the org's profile). FMP
# tags rows by ticker matching — it filed that release, about the drug
# PTC-2105, under PTC — so this is the field that catches its mis-tags.
_GNW_SOURCE_LINK_RE = re.compile(
    r"Source:\s*(?:<(?!a\b)[^>]*>\s*)*<a\b[^>]*>([^<]{2,120})</a>", re.IGNORECASE)
_GNW_SOURCE_RE = re.compile(r"(?:^|\|)[^\S\n]*Source:\s*([^\n|]{2,120})",
                            re.IGNORECASE | re.MULTILINE)


def extract_issuer(page_html: str) -> str:
    """The page's issuing-organization credit, or "" when it has none."""
    text = strip_html(page_html)
    m = _PROVIDED_BY_RE.search(text)
    if m:
        return m.group(1).strip().rstrip("→ ").strip()
    # Only the page header: a "Source:" line inside the release (a table's
    # data credit) says nothing about who issued it. The linked name first,
    # so a "Follow" button beside it isn't read as part of it.
    dateline = _DATELINE_RE.search(page_html)
    m = _GNW_SOURCE_LINK_RE.search(page_html, 0, dateline.start() if dateline else len(page_html))
    if m:
        return " ".join(strip_html(m.group(1)).split())
    dateline = _DATELINE_RE.search(text)
    m = _GNW_SOURCE_RE.search(text, 0, dateline.start() if dateline else len(text))
    return m.group(1).strip() if m else ""


# A headline-anchored span shorter than this is page chrome, not a release
_MIN_ANCHORED_CHARS = 300


def _after_headline(text: str, headline: str) -> int:
    """Offset just past the last place the page prints the release's own
    headline with a release-length span after it, or -1. The last one:
    <title> and share widgets repeat the headline above the article."""
    probe = " ".join(headline.split())[:60].lower()
    if len(probe) < 15:
        return -1
    flat = text.lower()
    best, pos = -1, flat.find(probe)
    while pos != -1:
        line_end = flat.find("\n", pos)
        start = len(flat) if line_end == -1 else line_end + 1
        if len(flat) - start >= _MIN_ANCHORED_CHARS:
            best = start
        pos = flat.find(probe, pos + 1)
    return best


def extract_release_text(page_html: str, headline: str = "") -> str:
    """Plain text of the release on an article page, or "" when the page
    carries neither a recognisable dateline nor the release's headline
    (caller keeps its inline body).

    The dateline marker is the reliable anchor; some releases don't print
    one (foreign issuers on GlobeNewswire often open "CITY – Oct. 13, 2026
    –" with no "(GLOBE NEWSWIRE)"), so the text after the release's own
    headline is the fallback."""
    text = strip_html(page_html)
    m = _DATELINE_RE.search(text)
    if m:
        # The dateline's city starts the line the marker sits on
        start = text.rfind("\n", 0, m.start()) + 1
    else:
        start = _after_headline(text, headline) if headline else -1
        if start < 0:
            return ""
    body = text[start:]
    end = _FOOTER_RE.search(body)
    if end:
        body = body[:end.start()]
    return body.strip()
