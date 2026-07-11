"""Deterministic grounding checks — the anti-hallucination gate."""

import grounding
from grounding import (
    build_corpus,
    corpus_number_values,
    iso_date_grounded,
    narrative_problems,
    normalize,
    ungrounded_dates,
    ungrounded_names,
    ungrounded_numbers,
    verify_catalysts,
    verify_deal_terms,
)


class TestNormalize:
    def test_case_punctuation_whitespace(self):
        assert normalize("Ecovative  Design,   LLC.") == "ecovative design llc"

    def test_apostrophes_removed(self):
        assert normalize("Element Solutions' board") == "element solutions board"


class TestNumbers:
    def test_magnitude_equivalence(self):
        corpus = corpus_number_values("a $30,000,000 investment")
        assert ungrounded_numbers("a $30 million deal", corpus) == []
        assert ungrounded_numbers("a $30M deal", corpus) == []

    def test_reverse_magnitude_equivalence(self):
        corpus = corpus_number_values("raised $2.5 billion of notes")
        assert ungrounded_numbers("issues $2,500,000,000 in debt", corpus) == []

    def test_price_decimal_equivalence(self):
        corpus = corpus_number_values("at a price of $10 per share")
        assert ungrounded_numbers("priced at $10.00", corpus) == []

    def test_fabricated_amounts_flagged(self):
        corpus = corpus_number_values(
            "This amendment refiles Exhibit 2.1 to the Current Report."
        )
        flagged = ungrounded_numbers(
            "a $30 million investment of 3,000,000 shares at $10.00 per share",
            corpus,
        )
        assert len(flagged) == 3

    def test_small_bare_integers_ignored(self):
        assert ungrounded_numbers("elected 3 of 7 directors", set()) == []

    def test_bare_years_ignored(self):
        # years are the date checks' job; "in 2026" alone is not a number claim
        assert ungrounded_numbers("expected in 2026", set()) == []

    def test_percentages_verified(self):
        corpus = corpus_number_values("a stake of 8.2% of shares outstanding")
        assert ungrounded_numbers("an 8.2% stake", corpus) == []
        assert ungrounded_numbers("a 9.5% stake", corpus) == ["9.5%"]


class TestDates:
    def test_iso_catalyst_matches_prose_date(self):
        corpus = normalize("expected to close on July 15, 2026.")
        assert iso_date_grounded("2026-07-15", corpus)

    def test_iso_catalyst_matches_slash_date(self):
        corpus = normalize("the deadline is 7/15/2026")
        assert iso_date_grounded("2026-07-15", corpus)

    def test_fabricated_iso_date_rejected(self):
        corpus = normalize("dated as of July 6, 2026")
        assert not iso_date_grounded("2026-07-15", corpus)

    def test_prose_date_claims(self):
        corpus = normalize("the merger agreement dated July 6, 2026")
        assert ungrounded_dates("signed on July 6, 2026", corpus) == []
        assert ungrounded_dates("closes on July 15, 2026", corpus) == ["July 15, 2026"]


class TestNames:
    def test_fabricated_counterparty_flagged(self):
        corpus = normalize("Element Solutions Inc amends Item 9.01 of its report.")
        assert ungrounded_names(
            "agreement to acquire Ecovative Design LLC", corpus
        ) == ["Ecovative Design LLC"]

    def test_present_name_passes(self):
        corpus = normalize("entered into an agreement with Atsion Capital Partners LP")
        assert ungrounded_names("a forward purchase with Atsion Capital Partners", corpus) == []

    def test_allowlist_covers_subject_company(self):
        assert ungrounded_names(
            "Solstice Advanced Materials files an amendment",
            normalize("this amendment refiles an exhibit"),
            allowed=("Solstice Advanced Materials Inc.",),
        ) == []

    def test_sentence_initial_capital_not_a_name(self):
        assert ungrounded_names("New investment could support growth", "") == []

    def test_short_ticker_allowlist_is_word_bounded(self):
        # "ESI" must not whitelist "Ecovative D[esi]gn LLC"
        assert ungrounded_names(
            "acquires Ecovative Design LLC", "", allowed=("ESI",)
        ) == ["Ecovative Design LLC"]

    def test_possessive_matches(self):
        corpus = normalize("Element Solutions Inc, a specialty chemicals company")
        assert ungrounded_names("expands Element Solutions' portfolio", corpus) == []


class TestNarrative:
    def test_grounded_narrative_clean(self):
        corpus = build_corpus(
            "Company: Acme Corp\n"
            "Acme Corp entered into a merger agreement with Beta Holdings LLC "
            "for $250 million, or $12.50 per share, expected to close on "
            "March 3, 2027."
        )
        assert narrative_problems(
            "Acme agrees to be acquired by Beta Holdings for $12.50 per share",
            corpus, allowed_names=("Acme Corp",),
        ) == []

    def test_hallucinated_narrative_rejected(self):
        corpus = build_corpus(
            "Company: Solstice Advanced Materials Inc.\n"
            "This Amendment No. 1 refiles Exhibit 2.1 to the Current Report "
            "originally filed on July 7, 2026."
        )
        problems = narrative_problems(
            "Solstice receives a $30 million investment of 3,000,000 shares "
            "of common stock at $10 per share from a new strategic investor, "
            "expected to close on July 15, 2026.",
            corpus, allowed_names=("Solstice Advanced Materials Inc.",),
        )
        assert any("$30 million" in p for p in problems)
        assert any("July 15, 2026" in p for p in problems)


class TestDealTerms:
    def test_fabricated_counterparty_dropped(self):
        corpus = build_corpus("This amendment amends Item 9.01 to include financial statements.")
        kept, dropped = verify_deal_terms(
            {"counterparty": "Ecovative Design LLC", "deal_type": "acquisition"},
            corpus,
        )
        assert kept == {}
        assert len(dropped) == 2

    def test_grounded_terms_kept(self):
        corpus = build_corpus(
            "definitive merger agreement with Beta Holdings LLC for total "
            "consideration of $250,000,000 in cash"
        )
        kept, dropped = verify_deal_terms(
            {"counterparty": "Beta Holdings LLC",
             "deal_value": "$250 million",
             "consideration_type": "cash",
             "deal_status": "definitive agreement signed"},
            corpus,
        )
        assert set(kept) == {"counterparty", "deal_value",
                             "consideration_type", "deal_status"}
        assert dropped == []

    def test_derived_premium_dropped(self):
        corpus = build_corpus("merger at $12.50 per share with Beta Holdings LLC")
        kept, dropped = verify_deal_terms({"premium": "45%"}, corpus)
        assert kept == {}
        assert len(dropped) == 1


class TestCatalysts:
    def test_grounded_catalyst_kept(self):
        corpus = build_corpus("special meeting to be held on September 12, 2026")
        kept, dropped = verify_catalysts(
            [{"date": "2026-09-12", "event": "special meeting"}], corpus)
        assert len(kept) == 1 and dropped == []

    def test_fabricated_catalyst_date_dropped(self):
        corpus = build_corpus("this amendment refiles an exhibit")
        kept, dropped = verify_catalysts(
            [{"date": "2026-07-15", "event": "expected closing date of investment"}],
            corpus)
        assert kept == [] and len(dropped) == 1

    def test_dateless_catalyst_checked_on_text(self):
        corpus = build_corpus("a shareholder vote will be scheduled")
        kept, _ = verify_catalysts([{"date": None, "event": "shareholder vote"}], corpus)
        assert len(kept) == 1


class TestCorpusHelpers:
    def test_grounding_module_has_no_llm_dependency(self):
        # the safety layer must stay pure — no Groq, no network
        import inspect
        src = inspect.getsource(grounding)
        assert "groq" not in src.lower()
