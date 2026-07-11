"""Registry contents: the 8-K family is in, everything else is out."""

from forms import FEED_QUERIES, FORM_REGISTRY, get_spec


class TestRegistryScope:
    def test_only_8k_family_present(self):
        assert set(FORM_REGISTRY) == {"8-K", "8-K/A"}

    def test_rolled_back_forms_absent(self):
        """July 2026 rollback: every non-8-K form must stay out."""
        for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC TO-T", "SC TO-I",
                     "SC 14D9", "SC 13E3", "S-4", "PREM14A", "DEFM14A",
                     "PREC14A", "DEFC14A", "DFAN14A", "25", "25-NSE",
                     "15-12B", "15-12G", "15F-12B", "NT 10-K", "NT 10-Q",
                     "CB", "4"):
            assert get_spec(form) is None, f"{form} should not be ingested"

    def test_noise_forms_absent(self):
        """Amendments and prefix noise must be dropped by the whitelist."""
        for noise in ("8-K12B", "8-K12G3", "8-K15D5", "425", "424B5", "10-K"):
            assert get_spec(noise) is None, f"{noise} should not be ingested"

    def test_8ka_hint_warns_about_amendments(self):
        spec = get_spec("8-K/A")
        assert "AMENDMENT" in spec.llm_hint
        assert "NEVER reconstruct or guess" in spec.llm_hint

    def test_single_8k_feed_query(self):
        assert [q.type_param for q in FEED_QUERIES] == ["8-K"]
