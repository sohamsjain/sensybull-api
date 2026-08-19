"""The event taxonomy and its collapse onto the simple user-facing category."""

import taxonomy


class TestStructure:
    def test_every_leaf_hangs_under_a_labelled_primary(self):
        for node in taxonomy.BY_TERTIARY.values():
            assert node.primary in taxonomy.PRIMARY_LABELS
            assert node.label in taxonomy.CATEGORIES

    def test_leaf_slugs_are_unique_across_the_whole_tree(self):
        """Leaves are the model's answer space, so a slug repeated under two
        parents would make the collapse to a category ambiguous."""
        seen = [
            tertiary
            for secondaries in taxonomy._TAXONOMY.values()
            for leaves in secondaries.values()
            for tertiary in leaves
        ]
        assert len(seen) == len(set(seen))
        assert len(seen) == len(taxonomy.BY_TERTIARY)

    def test_categories_are_the_primary_tier_plus_other(self):
        assert taxonomy.CATEGORIES[-1] == taxonomy.OTHER
        assert taxonomy.CATEGORIES[:-1] == list(taxonomy.PRIMARY_LABELS.values())


class TestCollapse:
    def test_leaf_maps_to_its_primary_label(self):
        assert taxonomy.display_category("ceo_departure") == "Leadership & Governance"
        assert taxonomy.display_category("covenant_violation") == "Capital & Financing"
        assert taxonomy.display_category("cybersecurity_incident") == "Risk Events"

    def test_unknown_leaf_is_other(self):
        assert taxonomy.display_category("ceo_vacation") == taxonomy.OTHER
        assert taxonomy.display_category("") == taxonomy.OTHER

    def test_sibling_leaves_collapse_to_one_category(self):
        """The whole point of the taxonomy: three specific events, one
        simple category for the reader."""
        labels = taxonomy.to_labels(
            ["ceo_departure", "cfo_appointment", "director_appointment"])
        assert labels == ["Leadership & Governance"]

    def test_distinct_buckets_stay_distinct_and_ordered(self):
        labels = taxonomy.to_labels(["merger_agreement", "debt_issuance"])
        assert labels == ["Strategic Transactions", "Capital & Financing"]

    def test_no_leaves_means_other(self):
        assert taxonomy.to_labels([]) == [taxonomy.OTHER]


class TestValidation:
    def test_case_and_whitespace_normalized(self):
        assert taxonomy.validate_tertiaries([" CEO_Departure "]) == ["ceo_departure"]

    def test_unknown_and_non_string_entries_dropped(self):
        raw = ["ceo_departure", "not_a_leaf", 7, None, "executive_leadership"]
        assert taxonomy.validate_tertiaries(raw) == ["ceo_departure"]

    def test_secondary_and_primary_slugs_are_not_valid_answers(self):
        """Only leaves classify — a model answering with a group heading
        must fall through to Other rather than silently pass."""
        assert taxonomy.validate_tertiaries(["board_of_directors"]) == []
        assert taxonomy.validate_tertiaries(["risk_events"]) == []

    def test_duplicates_collapse_and_the_cap_holds(self):
        raw = ["ceo_departure", "ceo_departure", "cfo_appointment",
               "director_appointment", "bylaw_amendment"]
        assert taxonomy.validate_tertiaries(raw) == [
            "ceo_departure", "cfo_appointment", "director_appointment"]

    def test_non_list_input_is_empty(self):
        assert taxonomy.validate_tertiaries("ceo_departure") == []
        assert taxonomy.validate_tertiaries(None) == []


class TestPromptBlock:
    def test_every_leaf_is_offered_to_the_model(self):
        for tertiary in taxonomy.BY_TERTIARY:
            assert tertiary in taxonomy.PROMPT_BLOCK

    def test_display_labels_are_absent(self):
        """Showing the model the buckets invites it to answer with one."""
        for label in taxonomy.PRIMARY_LABELS.values():
            assert label not in taxonomy.PROMPT_BLOCK

    def test_hints_only_reference_real_leaves(self):
        import re
        for slug in re.findall(r"\b[a-z][a-z_]{6,}\b", taxonomy.PROMPT_HINTS):
            if slug in taxonomy.BY_TERTIARY:
                continue
            # prose words are fine; slugs that look like leaves must be real
            assert "_" not in slug, f"{slug!r} in PROMPT_HINTS is not a leaf"


class TestLegacyAndItemMaps:
    def test_item_categories_are_canonical(self):
        for number, label in taxonomy.ITEM_CATEGORIES.items():
            assert label in taxonomy.CATEGORIES, number

    def test_catch_all_items_are_deliberately_unmapped(self):
        """7.01 (Reg FD) and 8.01 (Other Events) say nothing about what
        happened, so a facts-only briefing must not guess a category."""
        assert "7.01" not in taxonomy.ITEM_CATEGORIES
        assert "8.01" not in taxonomy.ITEM_CATEGORIES

    def test_every_legacy_label_folds_into_a_current_one(self):
        for old, new in taxonomy.LEGACY_LABELS.items():
            assert new in taxonomy.CATEGORIES, old
