"""Validation of the evidence block on the way into the database."""

from app.utils.evidence import MAX_ENTRIES, sanitize_evidence

SEC_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/a8k.htm"


def _entry(**overrides):
    base = {
        "quote": "the Company entered into a definitive merger agreement",
        "event_type": "Acquisition",
        "source": "Item 1.01",
        "doc_url": SEC_URL,
        "url": f"{SEC_URL}#:~:text=the%20Company%20entered",
        "highlighted": True,
    }
    base.update(overrides)
    return base


class TestShape:
    def test_valid_entry_passes_through(self):
        assert sanitize_evidence([_entry()]) == [_entry()]

    def test_non_list_input_is_empty(self):
        for raw in (None, "quote", {"quote": "x"}, 7):
            assert sanitize_evidence(raw) == []

    def test_entries_without_a_quote_are_dropped(self):
        assert sanitize_evidence([{"url": SEC_URL}, {"quote": "   "}, "x"]) == []

    def test_entry_count_is_capped(self):
        assert len(sanitize_evidence([_entry()] * 20)) == MAX_ENTRIES

    def test_long_quote_is_truncated_not_dropped(self):
        [out] = sanitize_evidence([_entry(quote="word " * 400)])
        assert len(out["quote"]) == 600

    def test_missing_labels_become_empty_strings(self):
        [out] = sanitize_evidence([{"quote": "some quoted filing text"}])
        assert out["event_type"] == "" and out["source"] == ""
        assert out["url"] == "" and out["highlighted"] is False


class TestLinkSafety:
    def test_off_host_link_loses_the_link_but_keeps_the_quote(self):
        [out] = sanitize_evidence([_entry(url="https://evil.test/x",
                                          doc_url="https://evil.test/x")])
        assert out["quote"].startswith("the Company entered")
        assert out["url"] == "" and out["doc_url"] == ""
        assert out["highlighted"] is False

    def test_non_https_link_is_rejected(self):
        [out] = sanitize_evidence([_entry(url="http://www.sec.gov/a.htm",
                                          doc_url="http://www.sec.gov/a.htm")])
        assert out["url"] == ""

    def test_javascript_url_is_rejected(self):
        [out] = sanitize_evidence([_entry(url="javascript:alert(1)",
                                          doc_url="javascript:alert(1)")])
        assert out["url"] == ""

    def test_lookalike_host_is_rejected(self):
        [out] = sanitize_evidence([_entry(url="https://www.sec.gov.evil.test/a.htm",
                                          doc_url="https://www.sec.gov.evil.test/a.htm")])
        assert out["url"] == ""

    def test_bad_fragment_url_falls_back_to_the_document(self):
        [out] = sanitize_evidence([_entry(url="https://evil.test/x")])
        assert out["url"] == SEC_URL
        # It no longer scrolls anywhere, so it must not claim to.
        assert out["highlighted"] is False

    def test_highlighted_flag_is_coerced_to_a_boolean(self):
        [out] = sanitize_evidence([_entry(highlighted="yes")])
        assert out["highlighted"] is True
