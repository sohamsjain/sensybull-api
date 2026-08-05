"""Briefing prompt rendering, generation, and fallbacks (Groq mocked)."""

import json
from unittest.mock import MagicMock, patch

import briefing as briefing_module
from briefing import (
    EVENT_TYPES,
    VOICE_RULES,
    _build_user_message,
    _system_prompt,
    facts_only_briefing,
    generate_briefing,
)
from models import Exhibit, Filing, Item


def _filing(**overrides):
    base = dict(
        id="urn:test:1", title="SmallCap Industries Inc.", cik="0000012345",
        ticker="SMCP", updated="2026-07-03T15:30:00-04:00",
        url="https://example.test/index.htm",
    )
    base.update(overrides)
    return Filing(**base)


# Long enough to clear the minimum-source gate.
_AGREEMENT_ITEM_TEXT = (
    "On July 1, 2026, SmallCap Industries Inc. entered into a supply "
    "agreement with Widget Partners LLC providing for the purchase of "
    "2,150,000 units over five years for aggregate consideration of "
    "$4,300,000. The agreement includes customary termination provisions "
    "and is expected to support the Company's expansion plans."
)


def _item_filing(**overrides):
    return _filing(items=[Item(
        "1.01", "Entry into a Material Definitive Agreement",
        _AGREEMENT_ITEM_TEXT, 2, "Contract",
    )], **overrides)


def _mock_groq(payload: dict):
    client = MagicMock()
    response = MagicMock()
    response.choices[0].message.content = json.dumps(payload)
    client.chat.completions.create.return_value = response
    return client


class TestSystemPrompt:
    def test_8k_prompt_shape(self):
        prompt = _system_prompt("8-K")
        assert "Given a 8-K filing" in prompt
        assert "buyside special-situations analyst" in prompt
        assert '"headline"' in prompt and '"catalysts"' in prompt
        # The literal JSON example must survive templating
        assert '{"date": "YYYY-MM-DD" or null, "event": "description"}' in prompt

    def test_stick_to_the_text_guidance_present(self):
        prompt = _system_prompt("8-K")
        assert "ONLY facts stated in the filing text" in prompt
        assert "insufficient_content" in prompt

    def test_8ka_prompt_warns_about_amendments(self):
        prompt = _system_prompt("8-K/A")
        assert "AMENDMENT" in prompt
        assert "NEVER reconstruct or guess" in prompt

    def test_empty_form_falls_back_to_8k(self):
        assert "Given a 8-K filing" in _system_prompt("")

    def test_voice_rules_present(self):
        """Filings speak as the company ("we were awarded ..."); the
        briefing must speak about it."""
        prompt = _system_prompt("8-K")
        assert VOICE_RULES in prompt
        for pronoun in ('"we"', '"our"', '"us"'):
            assert pronoun in prompt

    def test_no_perspective_phrasing(self):
        """Asking for a summary "from the company's perspective" reads as
        a voice instruction and produced first-person summaries; the prompt
        names the subject company instead."""
        assert "perspective" not in _system_prompt("8-K").lower()

    def test_event_types_are_the_narrow_material_list(self):
        assert "Other" in EVENT_TYPES
        assert len(EVENT_TYPES) <= 12
        # Rolled-back multi-form categories must be gone
        for gone in ("Insider Buying", "Tender Offer", "Activist Initial",
                     "Going Dark", "Late Filing"):
            assert gone not in EVENT_TYPES


class TestUserMessage:
    def test_amendment_includes_form_line(self):
        msg = _build_user_message(_filing(form_type="8-K/A"), {})
        assert "Form: 8-K/A" in msg

    def test_exhibit_index_included(self):
        filing = _filing(form_type="8-K/A", exhibits=[
            Exhibit("EX-2.1",
                    "AGREEMENT AND PLAN OF MERGER, DATED AS OF JULY 6, 2026",
                    "https://example.test/ex21.htm"),
        ])
        msg = _build_user_message(filing, {})
        assert "--- Exhibit Index" in msg
        assert "EX-2.1: AGREEMENT AND PLAN OF MERGER" in msg

    def test_8k_message_unchanged(self):
        msg = _build_user_message(_filing(), {})
        assert "Form:" not in msg
        assert msg.startswith("Company: SmallCap Industries Inc.")


class TestSourceGate:
    """No LLM call when there's nothing to summarize."""

    def test_empty_filing_never_reaches_llm(self):
        groq = MagicMock()
        with patch.object(briefing_module, "Groq", groq):
            result = generate_briefing(_filing(form_type="8-K"), {})
        groq.assert_not_called()
        assert result.mode == "facts_only"
        assert result.summary == ""

    def test_exhibit_only_amendment_never_reaches_llm(self):
        """Exhibit-only 8-K/A: no item text, nothing to summarize —
        publish facts-only without paying for an LLM call."""
        filing = _filing(
            form_type="8-K/A", ticker="SOLS",
            title="Solstice Advanced Materials Inc.",
            exhibits=[Exhibit(
                "EX-2.1",
                "AGREEMENT AND PLAN OF MERGER, DATED AS OF JULY 6, 2026",
                "https://example.test/ex21.htm",
            )],
        )
        groq = MagicMock()
        with patch.object(briefing_module, "Groq", groq):
            result = generate_briefing(filing, {})
        groq.assert_not_called()
        assert result.mode == "facts_only"
        assert result.summary == ""

    def test_short_item_text_never_reaches_llm(self):
        filing = _filing(form_type="8-K", items=[
            Item("9.01", "Financial Statements", "See exhibits.", 3, "Financials"),
        ])
        groq = MagicMock()
        with patch.object(briefing_module, "Groq", groq):
            result = generate_briefing(filing, {})
        groq.assert_not_called()
        assert result.mode == "facts_only"


class TestFactsOnlyBriefing:
    def test_headline_from_item_categories(self):
        filing = _filing(form_type="8-K/A", items=[
            Item("1.01", "Entry into a Material Definitive Agreement",
                 "", 2, "Contract"),
        ])
        b = facts_only_briefing(filing)
        assert b.headline == "8-K/A filed: Contract"
        assert b.primary_event_type == "Material Agreement"
        assert b.mode == "facts_only"

    def test_item_type_mapping_deterministic(self):
        filing = _filing(form_type="8-K", items=[
            Item("2.02", "Results of Operations", "", 2, "Earnings"),
            Item("5.02", "Departure of Directors", "", 2, "Leadership"),
        ])
        b = facts_only_briefing(filing)
        assert b.event_types == ["Earnings", "Leadership Change"]

    def test_item_mapping_stays_canonical(self):
        from briefing import _ITEM_EVENT_TYPES
        for number, label in _ITEM_EVENT_TYPES.items():
            assert label in EVENT_TYPES, f"{number} maps to non-canonical {label!r}"

    def test_significance_from_tier(self):
        filing = _filing(form_type="8-K", items=[
            Item("1.03", "Bankruptcy", "", 1, "Bankruptcy"),
        ])
        b = facts_only_briefing(filing)
        assert b.significance == "High"
        assert b.primary_event_type == "Bankruptcy"

    def test_no_items_falls_back_to_other(self):
        b = facts_only_briefing(_filing(form_type="8-K"))
        assert b.event_types == ["Other"]
        assert b.headline == "8-K filed — see filing for details"


class TestGenerateBriefing:
    def test_success_path(self):
        filing = _item_filing()
        client = _mock_groq({
            "headline": "SmallCap signs a five-year supply deal worth $4,300,000",
            "summary": "SmallCap Industries Inc. entered into a supply "
                       "agreement with Widget Partners LLC for 2,150,000 "
                       "units, worth $4,300,000 over five years.",
            "primary_event_type": "Material Agreement",
            "event_types": ["Material Agreement"],
            "significance": "Medium",
            "sentiment": "Positive",
            "investor_takeaway": "Supports the expansion plans.",
            "deal_terms": {"counterparty": "Widget Partners LLC",
                           "deal_value": "$4,300,000"},
            "catalysts": [{"date": None, "event": "Expansion milestones"}],
        })
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "llm"
        assert result.primary_event_type == "Material Agreement"
        assert result.significance == "Medium"
        assert result.deal_terms["counterparty"] == "Widget Partners LLC"
        assert result.catalysts == [{"date": None, "event": "Expansion milestones"}]
        # System prompt actually sent included the 8-K guidance
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        assert "FORM-SPECIFIC GUIDANCE (8-K)" in sent[0]["content"]

    def test_invalid_enums_normalized(self):
        filing = _item_filing()
        client = _mock_groq({
            "headline": "SmallCap signs a supply deal",
            "summary": "A supply agreement was signed.",
            "primary_event_type": "Blockbuster Deal",   # not canonical
            "event_types": ["Blockbuster Deal", "material agreement"],
            "significance": "Massive",                  # not canonical
            "sentiment": "Euphoric",                    # not canonical
            "investor_takeaway": "",
        })
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "llm"
        assert result.primary_event_type == "Other"
        assert result.event_types == ["Material Agreement"]  # case-normalized
        assert result.significance == "Medium"
        assert result.sentiment == "Neutral"

    def test_insufficient_content_escape_hatch(self):
        filing = _item_filing()
        client = _mock_groq({"insufficient_content": True})
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "facts_only"

    def test_failure_falls_back_to_item_mapping(self):
        filing = _item_filing()
        with patch.object(briefing_module, "Groq",
                          side_effect=RuntimeError("api down")):
            result = generate_briefing(filing, {})
        assert result.mode == "facts_only"
        assert result.primary_event_type == "Material Agreement"
        assert result.event_types == ["Material Agreement"]

    def test_failure_8k_falls_back_to_other(self):
        filing = _filing(form_type="8-K", items=[Item(
            "8.01", "Other Events",
            "The Company announced that its board of directors has approved "
            "a series of governance updates described in the press release "
            "attached hereto, which is incorporated by reference into this "
            "Item 8.01 of this Current Report on Form 8-K as filed today.",
            3, "Other",
        )])
        with patch.object(briefing_module, "Groq",
                          side_effect=RuntimeError("api down")):
            result = generate_briefing(filing, {})
        assert result.primary_event_type == "Other"
        assert result.mode == "facts_only"

    def test_empty_headline_gets_facts_only_headline(self):
        filing = _item_filing()
        client = _mock_groq({
            "headline": "",
            "summary": "A supply agreement was signed.",
            "primary_event_type": "Material Agreement",
            "event_types": ["Material Agreement"],
            "significance": "Medium",
            "sentiment": "Positive",
            "investor_takeaway": "",
        })
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "llm"
        assert result.headline == "8-K filed: Contract"


class _FakeGroqError(Exception):
    """Mimics a groq SDK error carrying an HTTP status and error code."""

    def __init__(self, message, status_code=404, code="model_not_found"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class TestModelChainConfig:
    def test_default_chain_drops_decommissioned_scout(self):
        chain = briefing_module._load_model_chain()
        assert "meta-llama/llama-4-scout-17b-16e-instruct" not in chain
        assert len(chain) >= 2

    def test_env_override_parses_and_trims(self, monkeypatch):
        monkeypatch.setenv("GROQ_MODELS", " model-a , model-b ,")
        assert briefing_module._load_model_chain() == ["model-a", "model-b"]


class TestModelUnavailable:
    def test_detects_404_status(self):
        assert briefing_module._is_model_unavailable(_FakeGroqError("nope"))

    def test_detects_model_not_found_code_without_status(self):
        exc = _FakeGroqError("nope", status_code=None)
        assert briefing_module._is_model_unavailable(exc)

    def test_detects_by_message_only(self):
        exc = Exception("The model `x` does not exist or you do not have access")
        assert briefing_module._is_model_unavailable(exc)

    def test_ordinary_error_is_not_model_unavailable(self):
        assert not briefing_module._is_model_unavailable(RuntimeError("boom"))


class TestModelFallback:
    """A model Groq no longer serves must degrade to the next model, not
    take the whole call down (the outage this fix addresses)."""

    def test_model_not_found_falls_through_to_next_model(self):
        filing = _item_filing()
        good = {
            "headline": "SmallCap signs a five-year supply deal worth $4,300,000",
            "summary": "A supply agreement was signed with Widget Partners LLC.",
            "primary_event_type": "Material Agreement",
            "event_types": ["Material Agreement"],
            "significance": "Medium",
            "sentiment": "Positive",
            "investor_takeaway": "Supports expansion.",
        }
        response = MagicMock()
        response.choices[0].message.content = json.dumps(good)
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _FakeGroqError(
                "The model `meta-llama/llama-4-scout-17b-16e-instruct` "
                "does not exist or you do not have access to it."),
            response,
        ]
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "llm"
        assert result.primary_event_type == "Material Agreement"
        # Primary failed with 404, then the fallback model was tried.
        models = [c.kwargs["model"]
                  for c in client.chat.completions.create.call_args_list]
        assert models == briefing_module._MODEL_CHAIN[:2]

    def test_model_not_found_on_whole_chain_degrades_to_facts_only(self):
        filing = _item_filing()
        client = MagicMock()
        client.chat.completions.create.side_effect = _FakeGroqError(
            "model_not_found")
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "facts_only"
        assert result.primary_event_type == "Material Agreement"
        # Every model in the chain was attempted before giving up.
        assert (client.chat.completions.create.call_count
                == len(briefing_module._MODEL_CHAIN))
