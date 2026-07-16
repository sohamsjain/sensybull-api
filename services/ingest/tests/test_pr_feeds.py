"""Wire adapter parsing against the pr/ fixtures."""

from press_release.feeds import WIRES, parse_wire_feed


def test_globenewswire_fields(fixture_text):
    releases = parse_wire_feed(
        fixture_text("pr/globenewswire.xml").encode(), WIRES["globenewswire"]
    )
    assert len(releases) == 3

    first = releases[0]
    assert first.guid == first.url
    assert first.wire == "globenewswire"
    assert first.url.startswith("https://www.globenewswire.com/news-release/")
    assert "Micron Technology Announces" in first.headline
    # Issuer from dc:contributor (live feed drops the legal suffix)
    assert first.issuer_name == "Micron Technology"
    # Raw category values pass through; extract_tickers turns
    # "Nasdaq:MU" into MU and rejects the ISIN by shape
    assert "Nasdaq:MU" in first.metadata_tickers
    assert "US5951121038" in first.metadata_tickers
    assert "definitive agreement" in first.body_html
    assert first.language == "en"

    law_firm = releases[1]
    assert law_firm.issuer_name == "The Rosen Law Firm PA"


def test_globenewswire_metadata_tickers_resolve(fixture_text):
    from press_release.issuer import extract_tickers

    releases = parse_wire_feed(
        fixture_text("pr/globenewswire.xml").encode(), WIRES["globenewswire"]
    )
    first = releases[0]
    tickers = extract_tickers(first.headline, "", first.metadata_tickers)
    # Exchange prefix stripped, ISIN and any prose rejected by shape
    assert tickers == ["MU"]


def test_prnewswire_fields(fixture_text):
    releases = parse_wire_feed(
        fixture_text("pr/prnewswire.xml").encode(), WIRES["prnewswire"]
    )
    assert len(releases) == 2
    first = releases[0]
    assert first.headline.startswith("Tesla Reports")
    # Issuing org from dc:contributor (confirmed in the live feed)
    assert first.issuer_name == "Tesla, Inc."
    assert first.metadata_tickers == []
    assert "NASDAQ: TSLA" in first.body_html
    # Third-party research shop carries ITS name, not the ticker's company —
    # exactly what resolve_issuer rejects
    assert releases[1].issuer_name == "NewResearch Partners"


def test_accesswire_fields(fixture_text):
    releases = parse_wire_feed(
        fixture_text("pr/accesswire.xml").encode(), WIRES["accesswire"]
    )
    assert len(releases) == 1
    assert "FDA Approval" in releases[0].headline
    assert releases[0].guid == releases[0].url


def test_accesswire_disabled_without_env(monkeypatch):
    """The known public URL serves an HTML page, not RSS (live probe) —
    the wire must stay off until PR_FEED_URLS_ACCESSWIRE is set."""
    monkeypatch.delenv("PR_FEED_URLS_ACCESSWIRE", raising=False)
    assert WIRES["accesswire"].resolved_feed_urls() == []


def test_garbage_input_returns_empty():
    assert parse_wire_feed(b"this is not xml at all", WIRES["prnewswire"]) == []
    assert parse_wire_feed(b"<rss><channel></channel></rss>", WIRES["prnewswire"]) == []
    assert parse_wire_feed(
        b"<rss><channel><item><title>t</title></item></channel></rss>",
        WIRES["prnewswire"],
    ) == []  # item with no guid/link is skipped


def test_feed_url_override(monkeypatch):
    monkeypatch.setenv("PR_FEED_URLS_BUSINESSWIRE", "https://example.com/a.rss, https://example.com/b.rss")
    assert WIRES["businesswire"].resolved_feed_urls() == [
        "https://example.com/a.rss", "https://example.com/b.rss",
    ]
    monkeypatch.delenv("PR_FEED_URLS_BUSINESSWIRE")
    assert WIRES["businesswire"].resolved_feed_urls() == []
