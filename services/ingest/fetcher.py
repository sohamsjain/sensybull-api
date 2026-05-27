"""
fetcher.py — All SEC EDGAR HTTP calls and feed/index parsing.

Pure functions, no state, no imports from other project modules.
"""

import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET

from dotenv import load_dotenv

load_dotenv()

ATOM_NS      = "http://www.w3.org/2005/Atom"
EDGAR_BASE   = "https://www.sec.gov"
FEED_URL     = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=8-K&dateb=&owner=include&count=40"
    "&search_text=&output=atom"
)
TICKERS_URL  = "https://www.sec.gov/files/company_tickers.json"
POLL_INTERVAL = 600  # seconds

_user_agent = os.environ.get("SEC_USER_AGENT", "")
if not _user_agent:
    raise RuntimeError("SEC_USER_AGENT environment variable is not set. Add it to .env")
USER_AGENT = _user_agent


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch_url(url: str, retries: int = 3, base_timeout: int = 45) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(retries):
        timeout = base_timeout + attempt * 20  # 45 → 65 → 85 s
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    raise last_exc


# ---------------------------------------------------------------------------
# ATOM feed
# ---------------------------------------------------------------------------

def fetch_feed() -> ET.Element:
    return ET.fromstring(fetch_url(FEED_URL))


def _clean_title(raw: str) -> str:
    title = re.sub(r"^8-K\s*[-–]\s*", "", raw, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(\d{10}\)\s*\(\w+\)\s*$", "", title).strip()
    return title


def parse_feed_entries(root: ET.Element) -> list[dict]:
    entries = []
    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        entry_id = (entry.findtext(f"{{{ATOM_NS}}}id") or "").strip()
        title    = _clean_title((entry.findtext(f"{{{ATOM_NS}}}title") or "").strip())
        updated  = (entry.findtext(f"{{{ATOM_NS}}}updated") or "").strip()
        link_el  = entry.find(f"{{{ATOM_NS}}}link")
        url      = link_el.get("href", "") if link_el is not None else ""

        cik = ""
        parts = url.split("/")
        for i, part in enumerate(parts):
            if part == "data" and i + 1 < len(parts):
                cik = parts[i + 1]
                break

        entries.append({"id": entry_id, "title": title, "updated": updated,
                        "url": url, "cik": cik})
    return entries


# ---------------------------------------------------------------------------
# Filing index
# ---------------------------------------------------------------------------

def _strip_tags(html: str) -> str:
    """Minimal tag removal for index table cell text."""
    return re.sub(r"<[^>]+>", "", html).strip()


def _parse_index_documents(index_html: str) -> list[dict]:
    """Parse the EDGAR filing index table into a list of document dicts.

    Uses a cell-level scan rather than row-level regex so that nested
    <table> elements inside a cell cannot prematurely end a row match.
    """
    docs = []
    # Collect all <td> cell texts in document order, then group them into
    # rows of exactly 5 (Seq | Description | Document | Type | Size) or 4.
    # We only ever need cells [1] description, [2] document, [3] type.
    cells_raw = re.findall(r"<td[^>]*>(.*?)</td>", index_html,
                           re.DOTALL | re.IGNORECASE)

    # EDGAR index tables always have 5 columns; tolerate 4-column rows too.
    for stride in (5, 4):
        if len(cells_raw) >= stride:
            break

    i = 0
    while i + stride - 1 < len(cells_raw):
        grp = cells_raw[i:i + stride]
        # column layout: [seq, description, document, type, (size)]
        cell_doc  = grp[2]
        cell_type = grp[3]
        cell_desc = grp[1]

        href_match = re.search(r"href=[\"']([^\"']+)[\"']", cell_doc,
                               re.IGNORECASE)
        if href_match:
            href    = href_match.group(1)
            doc_url = href if href.startswith("http") else EDGAR_BASE + href
            docs.append({
                "type":        _strip_tags(cell_type),
                "description": _strip_tags(cell_desc),
                "url":         doc_url,
            })
            i += stride
        else:
            # Misaligned — advance one cell and re-try
            i += 1

    return docs


def fetch_filing_detail(index_url: str) -> dict:
    """Fetch filing index page and primary 8-K HTML.

    Returns:
        {
            "primary_html": str,       # raw HTML of the primary 8-K document
            "exhibits":     list[dict] # [{type, description, url}, ...]
        }
    primary_html is an empty string on any failure; exhibits may be empty.
    """
    if not index_url:
        return {"primary_html": "", "exhibits": []}

    try:
        index_html = fetch_url(index_url).decode("utf-8", errors="replace")
    except Exception:
        return {"primary_html": "", "exhibits": []}

    docs     = _parse_index_documents(index_html)
    exhibits = [d for d in docs if d["type"].upper().startswith("EX-")]

    # Primary document: prefer an explicit 8-K* type match; fall back to the
    # first non-exhibit HTML document in the index (handles edge cases where
    # the type field is blank, mis-labelled, or uses a variant like 8-K12B).
    def _is_8k_type(t: str) -> bool:
        t = t.upper()
        return t.startswith("8-K") or t in ("FORM 8-K",)

    primary_url = next((d["url"] for d in docs if _is_8k_type(d["type"])), "")
    if not primary_url:
        primary_url = next(
            (d["url"] for d in docs
             if not d["type"].upper().startswith("EX-")
             and re.search(r"\.htm", d["url"], re.IGNORECASE)),
            "",
        )
    if "/ix?doc=" in primary_url:
        primary_url = EDGAR_BASE + primary_url.split("/ix?doc=")[1]

    primary_html = ""
    if primary_url:
        time.sleep(0.1)  # SEC fair-access politeness
        try:
            primary_html = fetch_url(primary_url).decode("utf-8", errors="replace")
        except Exception:
            pass

    return {"primary_html": primary_html, "exhibits": exhibits}


# ---------------------------------------------------------------------------
# Exhibit text fetching
# ---------------------------------------------------------------------------

def _is_fetchable_exhibit(ex_type: str) -> bool:
    return ex_type.upper().startswith("EX-99")


def fetch_exhibit_text(url: str) -> str:
    """Fetch exhibit HTML. Returns empty string for PDFs, binary, or on failure."""
    if not url or url.lower().endswith(".pdf"):
        return ""
    try:
        time.sleep(0.1)  # SEC fair-access politeness
        raw = fetch_url(url)
        # Quick binary check — if no HTML markers in first 2KB, skip
        head = raw[:2048].decode("utf-8", errors="replace").lower()
        if "<html" not in head and "<body" not in head:
            return ""
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Ticker map
# ---------------------------------------------------------------------------

def load_ticker_map() -> dict[str, dict]:
    """Returns {padded_cik: {ticker, name}}. Empty dict on failure."""
    try:
        raw = json.loads(fetch_url(TICKERS_URL).decode("utf-8", errors="replace"))
        result: dict[str, dict] = {}
        for entry in raw.values():
            padded_cik = str(entry["cik_str"]).zfill(10)
            result[padded_cik] = {
                "ticker": entry.get("ticker", ""),
                "name":   entry.get("title", ""),
            }
        return result
    except Exception:
        return {}
