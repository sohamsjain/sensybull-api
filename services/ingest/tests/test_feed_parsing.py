"""Atom feed entry parsing: form types, roles, title cleaning."""

import xml.etree.ElementTree as ET

from fetcher import _clean_title, parse_feed_entries


def _entries(fixture_text, name):
    return parse_feed_entries(ET.fromstring(fixture_text(name)))


class TestParseFeedEntries:
    def test_form_type_from_category_term(self, fixture_text):
        entries = _entries(fixture_text, "feed_mixed.atom")
        assert [e["form_type"] for e in entries] == [
            "8-K", "SC 13D", "SC 13D", "SC 13G/A", "425", "S-4/A", "NT 10-K",
        ]

    def test_role_extraction(self, fixture_text):
        entries = _entries(fixture_text, "feed_mixed.atom")
        by_title = {e["title"]: e["role"] for e in entries}
        assert by_title["Apple Inc."] == "Filer"
        assert by_title["SmallCap Industries Inc."] == "Subject"
        assert by_title["Activist Capital LP"] == "Filed By"

    def test_form4_issuer_and_reporting_roles(self, fixture_text):
        entries = _entries(fixture_text, "feed_form4.atom")
        roles = [e["role"] for e in entries]
        assert roles == ["Issuer", "Reporting", "Filer"]

    def test_subject_entry_cik_is_subject_company(self, fixture_text):
        entries = _entries(fixture_text, "feed_mixed.atom")
        subject = next(e for e in entries if e["role"] == "Subject")
        assert subject["cik"] == "12345"
        filed_by = next(e for e in entries if e["role"] == "Filed By")
        assert filed_by["cik"] == "900011"

    def test_twins_share_accession_in_url(self, fixture_text):
        entries = _entries(fixture_text, "feed_mixed.atom")
        pair = [e for e in entries if e["form_type"] == "SC 13D"]
        assert len(pair) == 2
        assert pair[0]["url"].split("/")[-1] == pair[1]["url"].split("/")[-1]
        assert pair[0]["id"] != pair[1]["id"]


class TestCleanTitle:
    def test_strips_form_prefix_and_role_suffix(self):
        assert _clean_title(
            "SC 13D/A - SmallCap Industries Inc. (0000012345) (Subject)",
            "SC 13D/A",
        ) == "SmallCap Industries Inc."

    def test_handles_filed_by_two_word_role(self):
        assert _clean_title(
            "SC 13D - Activist Capital LP (0000900011) (Filed by)",
            "SC 13D",
        ) == "Activist Capital LP"

    def test_8k_backwards_compatible(self):
        assert _clean_title(
            "8-K - Apple Inc. (0000320193) (Filer)", "8-K",
        ) == "Apple Inc."
