"""build_filing: 8-K item extraction, tiers, and ticker resolution."""

from parser import build_filing


def _entry(**overrides):
    base = {
        "id": "urn:test:1",
        "title": "Apple Inc.",
        "updated": "2026-07-03T15:30:00-04:00",
        "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/index.htm",
        "cik": "320193",
        "form_type": "8-K",
    }
    base.update(overrides)
    return base


class TestBuildFiling:
    def test_8k_items_extracted_with_tiers(self, ticker_map):
        html = ("<html><body><p>Item 1.01 Entry into a Material Definitive "
                "Agreement</p><p>On July 1, 2026, the Company entered into a "
                "material agreement with a counterparty covering the supply "
                "of widgets for the next five years.</p></body></html>")
        detail = {"primary_html": html, "exhibits": []}
        filing = build_filing(_entry(), detail, ticker_map)
        assert filing.ticker == "AAPL"
        assert [it.number for it in filing.items] == ["1.01"]
        assert filing.items[0].tier == 2  # from ITEM_TIERS
        assert filing.items[0].category == "Contract"

    def test_exhibit_list_item_dropped(self, ticker_map):
        html = ("<html><body><p>Item 2.02 Results of Operations</p>"
                "<p>The Company announced results for the quarter ended "
                "June 30, 2026, reporting revenue growth across segments.</p>"
                "<p>Item 9.01 Financial Statements and Exhibits</p>"
                "<p>(d) Exhibits.</p></body></html>")
        detail = {"primary_html": html, "exhibits": []}
        filing = build_filing(_entry(), detail, ticker_map)
        assert [it.number for it in filing.items] == ["2.02"]

    def test_xbrl_exhibits_filtered(self, ticker_map):
        detail = {"primary_html": "", "exhibits": [
            {"type": "EX-99.1", "description": "Press release", "url": "u1"},
            {"type": "EX-101.SCH", "description": "XBRL schema", "url": "u2"},
            {"type": "EX-104", "description": "Cover page XBRL", "url": "u3"},
        ]}
        filing = build_filing(_entry(), detail, ticker_map)
        assert [ex.type for ex in filing.exhibits] == ["EX-99.1"]

    def test_unknown_cik_gets_no_ticker(self, ticker_map):
        entry = _entry(cik="999999", title="Unknown Co")
        detail = {"primary_html": "", "exhibits": []}
        filing = build_filing(entry, detail, ticker_map)
        assert filing.ticker == ""
        assert filing.items == []

    def test_form_type_preserved(self, ticker_map):
        entry = _entry(form_type="8-K/A")
        filing = build_filing(entry, {"primary_html": "", "exhibits": []}, ticker_map)
        assert filing.form_type == "8-K/A"
