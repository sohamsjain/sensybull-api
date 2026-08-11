"""Tests for deal_terms key/value normalization.

Mirrored by sensybull-web `src/lib/__tests__/deal-terms.test.ts` — the two
implementations are expected to agree case for case.
"""

from app.utils.deal_terms import (
    normalize_deal_terms,
    normalize_term_key,
    normalize_term_value,
)


class TestNormalizeTermValue:
    def test_title_cases_lowercase_labels(self):
        assert normalize_term_value("definitive agreement signed") == \
            "Definitive Agreement Signed"
        assert normalize_term_value("stock") == "Stock"
        assert normalize_term_value("vote pending") == "Vote Pending"

    def test_keeps_small_words_lowercase_inside_the_value(self):
        assert normalize_term_value("merger of equals") == "Merger of Equals"
        assert normalize_term_value("sale of assets to acme") == \
            "Sale of Assets to Acme"

    def test_capitalizes_small_words_at_the_edges(self):
        assert normalize_term_value("the board") == "The Board"
        assert normalize_term_value("shares issued for") == "Shares Issued For"

    def test_preserves_existing_capitalization(self):
        assert normalize_term_value("SPAC merger") == "SPAC Merger"
        assert normalize_term_value("Agility Robotics, Inc.") == \
            "Agility Robotics, Inc."
        assert normalize_term_value("NASDAQ listing approved") == \
            "NASDAQ Listing Approved"

    def test_leaves_figures_untouched(self):
        assert normalize_term_value("$2,500,000,000") == "$2,500,000,000"
        assert normalize_term_value("45%") == "45%"
        assert normalize_term_value("expected in Q4 2026") == \
            "Expected in Q4 2026"
        assert normalize_term_value("2026-12-31") == "2026-12-31"

    def test_cases_compound_segments_independently(self):
        assert normalize_term_value("stock-for-stock") == "Stock-for-Stock"
        assert normalize_term_value("all-cash tender offer") == \
            "All-Cash Tender Offer"
        assert normalize_term_value("cash/stock") == "Cash/Stock"

    def test_sentence_cases_prose_length_values(self):
        assert normalize_term_value(
            "definitive agreement signed and announced by the board"
        ) == "Definitive agreement signed and announced by the board"

    def test_collapses_whitespace(self):
        assert normalize_term_value("  vote   pending\n") == "Vote Pending"
        assert normalize_term_value("   ") == ""

    def test_is_idempotent(self):
        once = normalize_term_value("definitive agreement signed")
        assert normalize_term_value(once) == once
        assert normalize_term_value("Stock-for-Stock") == "Stock-for-Stock"

    def test_handles_leading_punctuation(self):
        assert normalize_term_value("(subject to approval)") == \
            "(Subject to Approval)"


class TestNormalizeTermKey:
    def test_canonicalizes_to_snake_case(self):
        assert normalize_term_key("Deal Value") == "deal_value"
        assert normalize_term_key("dealValue") == "deal_value"
        assert normalize_term_key("deal_value") == "deal_value"
        assert normalize_term_key("  Deal-Status  ") == "deal_status"

    def test_empty_for_unusable_keys(self):
        assert normalize_term_key("") == ""
        assert normalize_term_key("   ") == ""
        assert normalize_term_key(None) == ""


class TestNormalizeDealTerms:
    def test_normalizes_keys_and_values(self):
        assert normalize_deal_terms({
            "Deal Status": "definitive agreement signed",
            "consideration_type": "stock",
            "dealType": "SPAC merger",
        }) == {
            "deal_status": "Definitive Agreement Signed",
            "consideration_type": "Stock",
            "deal_type": "SPAC Merger",
        }

    def test_drops_empty_entries(self):
        assert normalize_deal_terms({
            "deal_value": "",
            "": "cash",
            "deal_status": "   ",
            "counterparty": "acme corp",
        }) == {"counterparty": "Acme Corp"}

    def test_first_key_wins_on_collision(self):
        assert normalize_deal_terms({
            "deal_value": "$1B",
            "Deal Value": "$2B",
        }) == {"deal_value": "$1B"}

    def test_passes_non_string_values_through(self):
        assert normalize_deal_terms({"share_count": 2150000}) == \
            {"share_count": 2150000}

    def test_non_dict_input(self):
        assert normalize_deal_terms(None) == {}
        assert normalize_deal_terms([("deal_value", "$1B")]) == {}
        assert normalize_deal_terms({}) == {}
