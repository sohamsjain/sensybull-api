"""Issuer verification: normalization, ticker extraction, blocklist, fuzzy match."""

import pytest

from press_release.issuer import (
    build_ticker_index,
    extract_tickers,
    is_blocklisted,
    normalize_company_name,
    resolve_issuer,
)


@pytest.fixture
def ticker_index(ticker_map):
    return build_ticker_index(ticker_map)


# ── normalize_company_name ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Micron Technology, Inc.", "micron technology"),
    ("Apple Inc.", "apple"),
    ("Tesla, Inc.", "tesla"),
    ("The Boeing Company", "boeing"),
    ("ACME Holdings Ltd", "acme"),
    ("Sensata Technologies Holding plc", "sensata technologies"),
    ("E.ON SE", "e on"),
    ("Brookfield Asset Management Ltd.", "brookfield asset management"),
    ("", ""),
])
def test_normalize_company_name(raw, expected):
    assert normalize_company_name(raw) == expected


# ── ticker extraction ─────────────────────────────────────────────────────

def test_extract_tickers_from_body():
    body = "BOISE, Idaho -- Micron Technology, Inc. (Nasdaq: MU) today announced..."
    assert extract_tickers("headline", body) == ["MU"]


def test_extract_tickers_variants():
    assert extract_tickers("", "(NYSE: BRK.A) and (NYSE American: ABCD)") == ["BRK.A", "ABCD"]
    assert extract_tickers("", "Widgets Corp (NASDAQ:WDGT) news") == ["WDGT"]
    assert extract_tickers("", "En-dash style (Nasdaq – TSLA)") == ["TSLA"]


def test_extract_tickers_excludes_otc_and_foreign():
    assert extract_tickers("", "PennyCo (OTC: PNNY) and Maple Inc (TSX: MPL)") == []


def test_extract_tickers_metadata_first_and_dedup():
    got = extract_tickers("t", "Body (NASDAQ: MU).", metadata_tickers=["NYSE:MU", "AAPL"])
    assert got == ["MU", "AAPL"]


def test_extract_tickers_ignores_deep_body_mentions():
    body = "x" * 2000 + " (NASDAQ: MU)"
    assert extract_tickers("headline", body) == []


# ── blocklist ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headline", [
    "SHAREHOLDER ALERT: XYZ Investors with Losses are Urged to Contact the Firm",
    "Class Action Filed Against Acme Corp - ACME",
    "Deadline Reminder: Lead Plaintiff Motion Due August 1",
    "Rosen Law Firm Encourages Investors to Secure Counsel",
    "Pomerantz LLP Investigates Claims on Behalf of Investors of Acme",
    "INVESTOR ALERT: Kessler Topaz Announces Securities Fraud Suit",
    "Levi & Korsinsky Reminds Investors of Upcoming Deadline",
])
def test_blocklist_catches_law_firm_spam(headline):
    assert is_blocklisted(headline, "") is not None


@pytest.mark.parametrize("headline", [
    "Micron Technology Announces Definitive Agreement to Acquire ChipWorks",
    "Tesla Reports Second Quarter 2026 Financial Results",
    "SmallCap Industries Receives FDA Approval for XYZ-100 Therapy",
    "Acme Corp Announces CEO Transition",
    "Acme Prices $500 Million Senior Notes Offering",
])
def test_blocklist_passes_real_company_news(headline):
    assert is_blocklisted(headline, "") is None


# ── resolve_issuer ────────────────────────────────────────────────────────

def test_issuer_field_match(ticker_index):
    company, kind = resolve_issuer(
        ticker_index, ["TSLA"], "Tesla Inc", "headline", "body"
    )
    assert company["ticker"] == "TSLA"
    assert company["cik"] == "0001318605"
    assert kind == "issuer_field"


def test_issuer_field_fuzzy_match(ticker_index):
    # Registered name "SmallCap Industries Inc." vs wire's slightly different form
    company, kind = resolve_issuer(
        ticker_index, ["SMCP"], "Smallcap Industries, Incorporated", "h", "b"
    )
    assert company is not None and company["ticker"] == "SMCP"
    assert kind == "issuer_field"


def test_issuer_field_mismatch_rejects(ticker_index):
    # Law firm issuing a release tagged with someone else's ticker
    company, kind = resolve_issuer(
        ticker_index, ["TSLA"], "The Rosen Law Firm PA", "h", "b"
    )
    assert company is None
    assert kind == "none"


def test_dateline_match_when_no_issuer_field(ticker_index):
    body = "AUSTIN, Texas, July 15, 2026 /PRNewswire/ -- Tesla, Inc. (NASDAQ: TSLA) today announced results."
    company, kind = resolve_issuer(ticker_index, ["TSLA"], "", "Tesla Reports Q2", body)
    assert company is not None and company["ticker"] == "TSLA"
    assert kind == "dateline"


def test_unverified_when_nothing_to_match(ticker_index):
    company, kind = resolve_issuer(ticker_index, ["AAPL"], "", "Results announced", "No dateline here.")
    assert company is not None and company["ticker"] == "AAPL"
    assert kind == "unverified"


def test_unresolvable_ticker(ticker_index):
    company, kind = resolve_issuer(ticker_index, ["ZZZZ"], "Some Co", "h", "b")
    assert company is None
    assert kind == "none"
