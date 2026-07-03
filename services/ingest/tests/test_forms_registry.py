"""Registry contents: what's in, what's out, and rollout filtering."""

import forms
from forms import FORM_REGISTRY, active_feed_queries, enabled_forms, get_spec


class TestRegistryScope:
    def test_core_forms_present_with_tiers(self):
        expected_tiers = {
            "8-K": 3, "SC 13D": 1, "SC 13D/A": 2, "SC 13G": 3,
            "SC TO-T": 1, "SC TO-I": 1, "SC 14D9": 2, "SC 13E3": 1,
            "S-4": 2, "PREM14A": 1, "DEFM14A": 2, "PREC14A": 1,
            "DEFC14A": 1, "DFAN14A": 2, "25": 1, "25-NSE": 1,
            "15-12B": 1, "NT 10-K": 1, "NT 10-Q": 2, "CB": 2, "4": 2,
        }
        for form, tier in expected_tiers.items():
            spec = get_spec(form)
            assert spec is not None, f"{form} missing from registry"
            assert spec.tier == tier, f"{form} tier {spec.tier} != {tier}"

    def test_noise_forms_absent(self):
        """Amendments and routine variants must be dropped by the whitelist."""
        for noise in ("SC 13G/A", "S-4/A", "SC TO-C", "DEF 14A", "PRE 14A",
                      "425", "4/A", "424B5", "253G2", "15-15D", "10-K"):
            assert get_spec(noise) is None, f"{noise} should not be ingested"

    def test_form4_has_no_llm(self):
        spec = get_spec("4")
        assert spec.llm is False
        assert spec.strategy == "form4_xml"

    def test_subject_forms_marked(self):
        for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC TO-T", "SC 14D9",
                     "SC 13E3", "DFAN14A", "25", "CB"):
            assert get_spec(form).subject_from == "feed_subject"
        for form in ("8-K", "SC TO-I", "S-4", "NT 10-K", "15-12B"):
            assert get_spec(form).subject_from == "filer"

    def test_llm_forms_have_hints_and_defaults(self):
        from briefing import EVENT_TYPES
        for form, spec in FORM_REGISTRY.items():
            if spec.llm:
                assert spec.llm_hint, f"{form} missing llm_hint"
            assert spec.default_event_type in EVENT_TYPES, \
                f"{form} default_event_type {spec.default_event_type!r} not canonical"


class TestRolloutFlag:
    def test_default_all_enabled(self, monkeypatch):
        monkeypatch.delenv("INGEST_FORMS", raising=False)
        assert enabled_forms() == set(FORM_REGISTRY)

    def test_csv_restricts(self, monkeypatch):
        monkeypatch.setenv("INGEST_FORMS", "8-K, SC 13D, bogus-form")
        assert enabled_forms() == {"8-K", "SC 13D"}

    def test_feed_queries_follow_enabled_forms(self, monkeypatch):
        monkeypatch.setenv("INGEST_FORMS", "8-K")
        queries = active_feed_queries()
        assert [q.type_param for q in queries] == ["8-K"]

        monkeypatch.setenv("INGEST_FORMS", "8-K,SC 13D,4")
        params = [q.type_param for q in active_feed_queries()]
        assert set(params) == {"8-K", "SC 13D", "4"}

    def test_form4_query_pages(self):
        q = next(q for q in forms.FEED_QUERIES if q.type_param == "4")
        assert q.count == 100
        assert q.pages == 3
