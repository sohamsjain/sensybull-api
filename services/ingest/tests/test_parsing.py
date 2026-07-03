"""Index-header, ownership items, document excerpt, and build_filing strategies."""

from fetcher import parse_index_header
from forms import get_spec
from parser import (
    build_filing,
    extract_document_excerpt,
    extract_ownership_items,
    strip_html,
)


class TestIndexHeader:
    def test_subject_and_filed_by(self, fixture_text):
        header = parse_index_header(fixture_text("index_13d.html"))
        assert header["subject"] == {
            "cik": "0000012345", "name": "SmallCap Industries Inc.",
        }
        assert header["filed_by"] == {
            "cik": "0000900011", "name": "Activist Capital LP",
        }

    def test_no_header_returns_nones(self):
        header = parse_index_header("<html><body>no company blocks</body></html>")
        assert header == {"subject": None, "filed_by": None}


class TestOwnershipItems:
    def test_extracts_integer_items(self, fixture_text):
        plain = strip_html(fixture_text("doc_13d.html"))
        items = extract_ownership_items(plain)
        numbers = [it["number"] for it in items]
        assert numbers == ["1", "2", "3", "4", "5", "6"]  # Item 7 dropped
        item4 = next(it for it in items if it["number"] == "4")
        assert "Purpose of Transaction" in item4["title"]
        assert "strategic alternatives" in item4["text"]
        item5 = next(it for it in items if it["number"] == "5")
        assert "8.2%" in item5["text"]

    def test_no_items_in_free_text(self):
        assert extract_ownership_items("Just some prose without headers.") == []


class TestDocumentExcerpt:
    def test_skips_boilerplate_and_truncates_at_signature(self, fixture_text):
        plain = strip_html(fixture_text("doc_prem14a.html"))
        excerpt = extract_document_excerpt(plain)
        assert "$12.50 per share" in excerpt
        assert "termination fee" in excerpt
        assert "UNITED STATES" not in excerpt
        assert "duly caused this report" not in excerpt  # after signature block

    def test_caps_length(self):
        long_text = ("A meaningful line of prose that is definitely longer "
                     "than sixty characters in total length.\n") * 2000
        excerpt = extract_document_excerpt(long_text, cap=1000)
        assert len(excerpt) <= 1000 + len("\n[...truncated]")
        assert excerpt.endswith("[...truncated]")


class TestBuildFiling:
    def _entry(self, **overrides):
        base = {
            "id": "urn:test:1",
            "title": "SmallCap Industries Inc.",
            "updated": "2026-07-03T15:30:00-04:00",
            "url": "https://www.sec.gov/Archives/edgar/data/12345/000090001126000123/index.htm",
            "cik": "12345",
            "form_type": "SC 13D",
            "role": "Subject",
        }
        base.update(overrides)
        return base

    def test_ownership_strategy_subject_from_header(self, fixture_text, ticker_map):
        detail = {
            "primary_html": fixture_text("doc_13d.html"),
            "exhibits": [],
            "subject": {"cik": "0000012345", "name": "SmallCap Industries Inc."},
            "filed_by": {"cik": "0000900011", "name": "Activist Capital LP"},
        }
        filing = build_filing(self._entry(), detail, ticker_map, get_spec("SC 13D"))
        assert filing.ticker == "SMCP"
        assert filing.cik == "0000012345"
        assert filing.filed_by == "Activist Capital LP"
        assert filing.form_type == "SC 13D"
        assert len(filing.items) == 6
        assert all(it.tier == 1 for it in filing.items)
        assert all(it.category == "Activist Stake" for it in filing.items)

    def test_filer_role_without_header_gets_no_ticker(self, ticker_map):
        """A 13D entry under the FILER's CIK must not inherit the filer's
        ticker when the subject can't be resolved."""
        entry = self._entry(cik="320193", role="Filed By")  # AAPL's cik as filer
        detail = {"primary_html": "", "exhibits": [], "subject": None, "filed_by": None}
        filing = build_filing(entry, detail, ticker_map, get_spec("SC 13D"))
        assert filing.ticker == ""
        assert filing.cik == ""

    def test_document_strategy_excerpt(self, fixture_text, ticker_map):
        entry = self._entry(form_type="PREM14A", role="Filer")
        detail = {
            "primary_html": fixture_text("doc_prem14a.html"),
            "exhibits": [],
            "subject": None,
            "filed_by": None,
        }
        filing = build_filing(entry, detail, ticker_map, get_spec("PREM14A"))
        assert filing.items == []
        assert "$12.50 per share" in filing.document_excerpt
        assert filing.ticker == "SMCP"  # PREM14A is filer-attributed

    def test_8k_strategy_unchanged(self, ticker_map):
        entry = self._entry(form_type="8-K", role="Filer", cik="320193")
        html = ("<html><body><p>Item 1.01 Entry into a Material Definitive "
                "Agreement</p><p>On July 1, 2026, the Company entered into a "
                "material agreement with a counterparty covering the supply "
                "of widgets for the next five years.</p></body></html>")
        detail = {"primary_html": html, "exhibits": [], "subject": None, "filed_by": None}
        filing = build_filing(entry, detail, ticker_map, get_spec("8-K"))
        assert filing.ticker == "AAPL"
        assert [it.number for it in filing.items] == ["1.01"]
        assert filing.items[0].tier == 2  # from ITEM_TIERS, not form tier
        assert filing.document_excerpt == ""

    def test_legacy_call_without_spec(self, ticker_map):
        """build_filing(entry, detail, ticker_map) keeps 8-K behavior."""
        entry = self._entry(form_type="8-K", role="Filer", cik="320193")
        detail = {"primary_html": "", "exhibits": []}
        filing = build_filing(entry, detail, ticker_map)
        assert filing.ticker == "AAPL"
        assert filing.items == []
