"""
issuer.py — was this press release issued by the company itself?

Newswires are full of third-party releases tagged with popular tickers —
above all securities-law-firm "shareholder alert" spam. A release only
passes when:

  1. it is not blocklisted (law-firm / class-action patterns),
  2. it names at least one US-listed ticker we can resolve via the SEC
     ticker map, and
  3. the wire's issuing-organization field (or the headline/dateline)
     fuzzy-matches that ticker's registered company name.

A final LLM gate (press_release/materiality.py, "issued_by_company")
backstops wires whose feeds carry no structured issuer field.
"""

import difflib
import logging
import re

log = logging.getLogger(__name__)

# Corporate suffix tokens dropped during name normalization. Order-independent;
# stripped only from the tail so "Co" inside a name survives.
_SUFFIX_TOKENS = {
    "inc", "incorporated", "corp", "corporation", "company", "co",
    "ltd", "limited", "plc", "llc", "lp", "llp",
    "holdings", "holding", "group", "sa", "nv", "ag", "se", "the",
}

_FUZZY_RATIO_THRESHOLD = 0.85

# Exchange-qualified ticker mentions, e.g. "(NYSE: MU)" or "Nasdaq – ABCD".
# Deliberately US-listed exchanges only: the SEC ticker map (and the rest of
# the product) covers US listings, so OTC/TSX/foreign mentions don't resolve.
_TICKER_RE = re.compile(
    r"\b(NYSE American|NYSE MKT|NYSE|NASDAQ|Nasdaq|AMEX|CBOE)"
    r"\s*[:–—-]\s*"
    r"([A-Z]{1,5}(?:\.[A-Z])?)\b"
)

# How much body text to scan for tickers/issuer clues. The issuing company
# is always identified in the headline or dateline, never only at the tail.
_SCAN_CHARS = 1_500

# Law-firm / shareholder-suit spam. Matched against headline + body head,
# case-insensitive. Extend freely — a false positive here only suppresses a
# release that was never first-party material news.
_BLOCKLIST_PATTERNS = [
    r"shareholder\s+(alert|notice|rights|reminder)",
    r"investor\s+(alert|notice|rights)",
    r"class\s+action",
    r"securities\s+(fraud|litigation|class)",
    r"lead\s+plaintiff",
    r"investors?\s+(who|that)\s+(purchased|acquired|lost|suffered)",
    r"deadline\s+(alert|reminder|approaching)",
    r"upcoming\s+deadline",
    r"law\s+(firm|offices?)",
    r"attorneys?\s+at\s+law",
    r"investigat(es|ion|ing)\b.{0,80}\bon\s+behalf",
    r"encourages?\s+investors",
    r"reminds?\s+investors",
    r"losses?\s+in\s+excess\s+of",
    r"contact\s+the\s+firm",
]

# Known securities-plaintiff firms that flood the wires with ticker-tagged
# releases. Names, not patterns — matched as substrings of the lowercased text.
_LAW_FIRM_NAMES = [
    "rosen law",
    "pomerantz",
    "glancy prongay",
    "bronstein, gewirtz",
    "levi & korsinsky",
    "kessler topaz",
    "robbins geller",
    "hagens berman",
    "kahn swick",
    "bragar eagel",
    "schall law",
    "portnoy law",
    "johnson fistel",
    "kirby mcinerney",
    "faruqi & faruqi",
    "howard g. smith",
    "wolf haldenstein",
    "bernstein liebhard",
]

_compiled_blocklist = [re.compile(p, re.IGNORECASE) for p in _BLOCKLIST_PATTERNS]


def normalize_company_name(name: str) -> str:
    """Lowercase, strip punctuation, drop trailing corporate-suffix tokens."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    tokens = name.split()
    while tokens and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    # Leading "the" contributes nothing to identity either
    while tokens and tokens[0] == "the":
        tokens.pop(0)
    return " ".join(tokens)


def build_ticker_index(ticker_map: dict[str, dict]) -> dict[str, dict]:
    """Invert fetcher.load_ticker_map's {padded_cik: {ticker, name}}.

    Returns {TICKER: {cik, name, norm_name}} — the reverse lookup a
    press release needs (it names a ticker, not a CIK).
    """
    index: dict[str, dict] = {}
    for padded_cik, info in ticker_map.items():
        ticker = (info.get("ticker") or "").upper()
        if not ticker:
            continue
        index[ticker] = {
            "cik": padded_cik,
            "name": info.get("name", ""),
            "norm_name": normalize_company_name(info.get("name", "")),
        }
    return index


def extract_tickers(headline: str, body: str, metadata_tickers: list[str] | None = None) -> list[str]:
    """Candidate US-listed tickers, metadata first, then exchange-qualified
    mentions in the headline + body head. De-duplicated, order preserved."""
    out: list[str] = []
    for t in metadata_tickers or []:
        t = t.strip().upper()
        # Metadata sometimes arrives exchange-qualified ("NYSE:MU")
        if ":" in t:
            t = t.rsplit(":", 1)[1].strip()
        if t and t not in out:
            out.append(t)

    scan = f"{headline}\n{body[:_SCAN_CHARS]}"
    for m in _TICKER_RE.finditer(scan):
        t = m.group(2).upper()
        if t not in out:
            out.append(t)
    return out


def is_blocklisted(headline: str, body: str) -> str | None:
    """Return the matched pattern/name when the release is law-firm or
    shareholder-suit spam, else None."""
    scan = f"{headline}\n{body[:_SCAN_CHARS]}"
    for pattern in _compiled_blocklist:
        m = pattern.search(scan)
        if m:
            return m.group(0)
    lowered = scan.lower()
    for name in _LAW_FIRM_NAMES:
        if name in lowered:
            return name
    return None


def _names_match(norm_issuer: str, norm_company: str) -> bool:
    if not norm_issuer or not norm_company:
        return False
    if norm_issuer == norm_company:
        return True

    # Token containment: every token of the shorter name appears in the
    # longer one, with at least one distinctive (len >= 4) token shared.
    short, long_ = sorted((norm_issuer.split(), norm_company.split()), key=len)
    if short and set(short) <= set(long_) and any(len(t) >= 4 for t in short):
        return True

    return difflib.SequenceMatcher(None, norm_issuer, norm_company).ratio() >= _FUZZY_RATIO_THRESHOLD


def _dateline_issuer(headline: str, body: str) -> str:
    """Heuristic issuer when the wire has no structured field: the text
    immediately before the first exchange-qualified ticker mention."""
    scan = f"{headline}\n{body[:_SCAN_CHARS]}"
    m = _TICKER_RE.search(scan)
    if not m:
        return ""
    prefix = scan[:m.start()].strip().strip("(").strip()
    # Company name is the tail of the prefix — take the last clause
    tail = re.split(r"[\n—;|]|\s[-–]\s|--", prefix)[-1].strip()
    # Drop a leading dateline like "SAN JOSE, Calif., July 15, 2026 /PRNewswire/ --"
    tail = re.sub(r"^.*?/\s*$", "", tail).strip()
    return tail[-120:]


def resolve_issuer(
    ticker_index: dict[str, dict],
    candidate_tickers: list[str],
    issuer_name: str,
    headline: str,
    body: str,
) -> tuple[dict | None, str]:
    """Match the release's issuer against candidate tickers' registered names.

    Returns (company_info, match_kind):
      company_info — the ticker_index entry (plus "ticker") for the matched
        company, or None when no candidate resolves.
      match_kind — "issuer_field" | "dateline" | "unverified" | "none".
        "unverified" means a ticker resolved but no issuer text was
        available to verify against; callers rely on the blocklist + the
        LLM first-party gate and should monitor this rate.
    """
    resolved = [
        {**ticker_index[t], "ticker": t}
        for t in candidate_tickers if t in ticker_index
    ]
    if not resolved:
        return None, "none"

    norm_issuer = normalize_company_name(issuer_name) if issuer_name else ""
    if norm_issuer:
        for info in resolved:
            if _names_match(norm_issuer, info["norm_name"]):
                return info, "issuer_field"
        # A structured issuer field that matches NO candidate ticker is the
        # law-firm/ticker-farming signature — reject outright.
        log.info("Issuer mismatch: field=%r vs candidates=%s",
                 issuer_name, [r["ticker"] for r in resolved])
        return None, "none"

    dateline = _dateline_issuer(headline, body)
    norm_dateline = normalize_company_name(dateline) if dateline else ""
    if norm_dateline:
        for info in resolved:
            if _names_match(norm_dateline, info["norm_name"]):
                return info, "dateline"

    # Ticker resolved but nothing to verify the issuer against — pass with
    # the weaker guarantee; blocklist ran before us, LLM gate runs after.
    return resolved[0], "unverified"
