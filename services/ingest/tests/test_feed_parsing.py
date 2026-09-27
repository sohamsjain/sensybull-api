"""Atom feed entry parsing: form types, title cleaning, whitelist filtering."""

import xml.etree.ElementTree as ET

from fetcher import _clean_title, parse_feed_entries
from forms import ALLOWED_FORMS


def _entries(fixture_text, name):
    return parse_feed_entries(ET.fromstring(fixture_text(name)))


class TestParseFeedEntries:
    def test_form_type_from_category_term(self, fixture_text):
        entries = _entries(fixture_text, "feed_mixed.atom")
        assert [e["form_type"] for e in entries] == [
            "8-K", "SC 13D", "SC 13D", "SC 13G/A", "425", "S-4/A", "NT 10-K",
        ]

    def test_only_8k_survives_registry_whitelist(self, fixture_text):
        """The EDGAR prefix query returns other forms; the exact-form
        whitelist (applied in main.py) must admit only the 8-K family."""
        entries = _entries(fixture_text, "feed_mixed.atom")
        admitted = [e for e in entries if e["form_type"] in ALLOWED_FORMS]
        assert [e["title"] for e in admitted] == ["Apple Inc."]

    def test_cik_from_url(self, fixture_text):
        entries = _entries(fixture_text, "feed_mixed.atom")
        assert entries[0]["cik"] == "320193"


class TestCleanTitle:
    def test_strips_form_prefix_and_role_suffix(self):
        assert _clean_title(
            "8-K - Apple Inc. (0000320193) (Filer)", "8-K",
        ) == "Apple Inc."

    def test_8ka_amendment_title(self):
        assert _clean_title(
            "8-K/A - Tesla, Inc. (0001318605) (Filer)", "8-K/A",
        ) == "Tesla, Inc."


def test_fetch_url_stops_reading_at_max_bytes(monkeypatch):
    import io
    import fetcher

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(fetcher.urllib.request, "urlopen",
                        lambda req, timeout: _Resp(b"x" * 1000))
    assert fetcher.fetch_url("https://www.sec.gov/x", max_bytes=100) == b"x" * 100
    assert len(fetcher.fetch_url("https://www.sec.gov/x")) == 1000
