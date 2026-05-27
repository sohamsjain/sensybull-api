"""
parser.py — HTML stripping, item extraction, and Filing assembly.

Pure functions, no I/O, no imports from other project modules except models.
"""

import re
from html.parser import HTMLParser

from models import Exhibit, Filing, Item

# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------

ITEM_TIERS: dict[str, int] = {
    # Tier 1 — highest signal
    "1.03": 1, "1.05": 1, "2.06": 1, "3.01": 1, "4.02": 1, "5.01": 1,
    # Tier 2 — high value
    "1.01": 2, "1.02": 2, "2.01": 2, "2.02": 2, "2.03": 2, "2.05": 2, "5.02": 2,
    # Tier 3 — low signal (everything else defaults here too)
    "3.02": 3, "3.03": 3, "4.01": 3, "5.03": 3, "5.04": 3, "5.05": 3,
    "5.06": 3, "5.07": 3, "5.08": 3, "6.01": 3, "7.01": 3, "8.01": 3, "9.01": 3,
}

_ITEM_CATEGORIES: dict[str, str] = {
    "1.01": "Contract",           "1.02": "Contract Ended",
    "1.03": "Bankruptcy",         "1.04": "Mine Safety",
    "2.01": "Asset Deal",         "2.02": "Earnings",
    "2.03": "New Debt",           "2.04": "Debt Acceleration",
    "2.05": "Restructuring",      "2.06": "Impairment",
    "3.01": "Delisting Risk",     "3.02": "Unregistered Securities",
    "3.03": "Rights Modified",    "4.01": "Auditor Change",
    "4.02": "Restatement",        "5.01": "Change of Control",
    "5.02": "Leadership",         "5.03": "Bylaws Change",
    "5.04": "Trading Suspension", "5.05": "Ethics Policy",
    "5.06": "Shell Company",      "5.07": "Shareholder Vote",
    "5.08": "Director Nominations","6.01": "ABS Material",
    "7.01": "Reg FD",             "8.01": "Other",
    "9.01": "Financials",
}

# ---------------------------------------------------------------------------
# HTML stripping
# ---------------------------------------------------------------------------

_BLOCK_TAGS = frozenset({
    "p", "div", "br", "tr", "td", "th", "li",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "table", "thead", "tbody", "section", "article", "header", "footer",
})


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._in_skip: int = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        t = tag.lower()
        if t in ("script", "style"):
            self._in_skip += 1
        elif t in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style"):
            self._in_skip = max(0, self._in_skip - 1)

    def handle_data(self, data: str) -> None:
        if self._in_skip == 0:
            # Collapse inline whitespace (source line-wrapping, &nbsp;, tabs)
            # within a text node to a single space.  Block structure (\n for
            # <p>, <div>, etc.) comes from handle_starttag, never from data.
            self._parts.append(re.sub(r'[ \t\u00a0\n]+', ' ', data))

    def get_text(self) -> str:
        return "".join(self._parts)


def strip_html(html_str: str) -> str:
    stripper = _HTMLStripper()
    stripper.feed(html_str)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Item text cleaning
# ---------------------------------------------------------------------------

_BOILERPLATE = re.compile(
    r"(?im)^[^\S\n]*(united states|securities and exchange|washington,?\s*d\.?c\.?|"
    r"form\s+8-k|commission\s+file|pursuant\s+to\s+section\s+13|"
    r"page\s+\d+\s*$)"
)

_SIGNATURE_BLOCK = re.compile(
    r"(?im)^[^\S\n]*(?:"
    r"signatures?\s*$"
    r"|pursuant\s+to\s+the\s+requirements\s+of\s+the\s+securities"
    r"|forward[\s\-]+looking\s+(?:statements?|information)"
    r"|cautionary\s+(?:note|statement|language)"
    r"|special\s+note\s+regarding\s+forward"
    r")"
)


def _clean_item_text(text: str) -> str:
    # Truncate at the signature/closing boilerplate block
    m = _SIGNATURE_BLOCK.search(text)
    if m:
        text = text[:m.start()]

    # Remove standalone page numbers (bare 1–3 digit lines)
    text = re.sub(r"(?m)^\s*\d{1,3}\s*$", "", text)

    # Remove known boilerplate lines
    text = "\n".join(
        line for line in text.splitlines()
        if not _BOILERPLATE.match(line)
    )

    # Strip leading fragment — title continuation artifact from HTML line breaks.
    # A lowercase start is a definitive signal; also catch short orphaned phrases.
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip():
            s = line.strip()
            if s[0].islower() or len(s) < 60:
                lines.pop(i)
            break
    text = "\n".join(lines)

    # Collapse runs of blank/whitespace-only lines to a single blank line.
    # Handles \n \n (space between) produced by stripped &nbsp;-only lines.
    text = re.sub(r'\n[ \t]*\n([ \t]*\n)*', '\n\n', text)

    # Merge orphaned short lines (≤4 chars, e.g. "On") with the line that follows
    lines = text.splitlines()
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (line.strip() and len(line.strip()) <= 4
                and i + 1 < len(lines) and lines[i + 1].strip()):
            merged.append(line.rstrip() + " " + lines[i + 1].lstrip())
            i += 2
        else:
            merged.append(line.rstrip())
            i += 1
    return "\n".join(merged).strip()


# ---------------------------------------------------------------------------
# Item extraction
# ---------------------------------------------------------------------------

_ITEM_HEADER = re.compile(
    r"(?im)^[^\S\n]*item\s+(\d+\.\d+)[.\-\u2013\u2014\s]*([^\n]{0,250})"
)


def extract_items(plain_text: str) -> list[dict]:
    matches = list(_ITEM_HEADER.finditer(plain_text))

    # Keep the occurrence with the longest body when the same item number
    # appears more than once (table-of-contents header vs. actual section).
    candidates: dict[str, dict] = {}
    for i, m in enumerate(matches):
        number = m.group(1)
        title  = m.group(2).strip(" .-\u2013\u2014")
        body_start = m.end()
        body_end   = matches[i + 1].start() if i + 1 < len(matches) else len(plain_text)
        body = _clean_item_text(plain_text[body_start:body_end])

        if number not in candidates or len(body) > len(candidates[number]["text"]):
            candidates[number] = {"number": number, "title": title, "text": body}

    # Return in document order (first appearance of each number)
    order: list[str] = []
    seen: set[str] = set()
    for m in matches:
        n = m.group(1)
        if n not in seen:
            seen.add(n)
            order.append(n)

    return [candidates[n] for n in order if n in candidates]


# ---------------------------------------------------------------------------
# Filing assembly
# ---------------------------------------------------------------------------

def build_filing(entry: dict, detail: dict, ticker_map: dict) -> Filing:
    """Assemble a Filing dataclass from raw feed entry + filing detail + ticker map."""
    padded_cik  = entry["cik"].zfill(10)
    ticker_info = ticker_map.get(padded_cik, {})

    primary_html = detail.get("primary_html", "")
    plain        = strip_html(primary_html) if primary_html else ""
    raw_items    = extract_items(plain) if plain else []

    items = [
        Item(
            number=it["number"],
            title=it["title"],
            text=it["text"],
            tier=ITEM_TIERS.get(it["number"], 3),
            category=_ITEM_CATEGORIES.get(it["number"], ""),
        )
        for it in raw_items
        if it["number"] != "9.01"  # exhibit list — redundant with exhibits field
    ]

    exhibits = [
        Exhibit(type=ex["type"], description=ex["description"], url=ex["url"])
        for ex in detail.get("exhibits", [])
        if not ex["type"].upper().startswith("EX-101.")   # XBRL instance docs
        and not ex["type"].upper().startswith("EX-104")   # cover page XBRL
    ]

    return Filing(
        id=entry["id"],
        title=entry["title"],
        cik=entry["cik"],
        ticker=ticker_info.get("ticker", ""),
        updated=entry["updated"],
        url=entry["url"],
        items=items,
        exhibits=exhibits,
    )
