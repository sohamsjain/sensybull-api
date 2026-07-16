"""Wire adapter parsing against the pr/ fixtures."""

from press_release.feeds import WIRES, parse_wire_feed


def test_globenewswire_fields(fixture_text):
    releases = parse_wire_feed(
        fixture_text("pr/globenewswire.xml").encode(), WIRES["globenewswire"]
    )
    assert len(releases) == 3

    first = releases[0]
    assert first.guid == "GNW-1000001"
    assert first.wire == "globenewswire"
    assert first.url.startswith("https://www.globenewswire.com/news-release/")
    assert "Micron Technology Announces" in first.headline
    assert first.issuer_name == "Micron Technology, Inc."
    assert first.metadata_tickers == ["MU"]
    assert "definitive agreement" in first.body_html
    assert first.raw_categories == ["Mergers and Acquisitions"]

    law_firm = releases[1]
    assert law_firm.issuer_name == "The Rosen Law Firm PA"


def test_prnewswire_fields(fixture_text):
    releases = parse_wire_feed(
        fixture_text("pr/prnewswire.xml").encode(), WIRES["prnewswire"]
    )
    assert len(releases) == 2
    first = releases[0]
    assert first.headline.startswith("Tesla Reports")
    # PRN has no structured issuer field configured
    assert first.issuer_name == ""
    assert first.metadata_tickers == []
    assert "NASDAQ: TSLA" in first.body_html


def test_accesswire_fields(fixture_text):
    releases = parse_wire_feed(
        fixture_text("pr/accesswire.xml").encode(), WIRES["accesswire"]
    )
    assert len(releases) == 1
    assert "FDA Approval" in releases[0].headline
    assert releases[0].guid == releases[0].url


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
