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


def extract_issuer(page_html: str) -> str:
    """The page's issuing-organization credit, or "" when it has none."""
    m = _PROVIDED_BY_RE.search(strip_html(page_html))
    if not m:
        return ""
    return m.group(1).strip().rstrip("→ ").strip()


def extract_release_text(page_html: str) -> str:
    """Plain text of the release on an article page, or "" when the page
    carries no recognisable dateline (caller keeps its inline body)."""
    text = strip_html(page_html)
    m = _DATELINE_RE.search(text)
    if not m:
        return ""
    # The dateline's city starts the line the marker sits on
    start = text.rfind("\n", 0, m.start()) + 1
    body = text[start:]
    end = _FOOTER_RE.search(body)
    if end:
        body = body[:end.start()]
    return body.strip()
