"""Form whitelist: the 8-K family is in, everything else is out."""

from forms import ALLOWED_FORMS, LLM_HINTS


class TestAllowedForms:
    def test_only_8k_family_present(self):
        assert ALLOWED_FORMS == {"8-K", "8-K/A"}

    def test_rolled_back_forms_absent(self):
        """July 2026 rollback: every non-8-K form must stay out."""
        for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC TO-T", "SC TO-I",
                     "SC 14D9", "SC 13E3", "S-4", "PREM14A", "DEFM14A",
                     "PREC14A", "DEFC14A", "DFAN14A", "25", "25-NSE",
                     "15-12B", "15-12G", "15F-12B", "NT 10-K", "NT 10-Q",
                     "CB", "4"):
            assert form not in ALLOWED_FORMS, f"{form} should not be ingested"

    def test_prefix_noise_absent(self):
        """EDGAR prefix-matches the 8-K feed query; variants must be dropped."""
        for noise in ("8-K12B", "8-K12G3", "8-K15D5"):
            assert noise not in ALLOWED_FORMS, f"{noise} should not be ingested"

    def test_every_allowed_form_has_an_llm_hint(self):
        assert set(LLM_HINTS) == ALLOWED_FORMS

    def test_8ka_hint_warns_about_amendments(self):
        hint = LLM_HINTS["8-K/A"]
        assert "AMENDMENT" in hint
        assert "NEVER reconstruct or guess" in hint
