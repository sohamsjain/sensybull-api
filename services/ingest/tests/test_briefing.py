"""Briefing prompt rendering, generation, and fallbacks (Groq mocked)."""

import json
from unittest.mock import MagicMock, patch

import briefing as briefing_module
import taxonomy
from briefing import (
    EVENT_TYPES,
    VOICE_RULES,
    _build_user_message,
    _coerce_deal_terms,
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

    def test_event_types_are_the_taxonomy_top_tier(self):
        """The user-facing list is the taxonomy's primary tier plus Other —
        one simple category per event, never the leaf or the middle tier."""
        assert EVENT_TYPES == [*taxonomy.PRIMARY_LABELS.values(), "Other"]
        assert len(EVENT_TYPES) == 9

    def test_prompt_offers_leaves_not_categories(self):
        """The model classifies against the specific leaves; the simple
        categories are derived afterwards and never shown to it."""
        prompt = _system_prompt("8-K")
        assert "ceo_departure" in prompt
        assert "covenant_violation" in prompt
        assert '"primary_category"' in prompt
        # The display labels would invite the model to answer with a bucket
        assert "Strategic Transactions" not in prompt

    def test_prompt_carries_the_disambiguation_hints(self):
        prompt = _system_prompt("8-K")
        assert "settlement_agreement" in prompt
        assert "guidance_withdrawal" in prompt


class TestUserMessage:
    def test_truncation_is_logged_so_the_cap_can_be_tuned_from_data(self, caplog):
        """The cap trades filing text for prompt room; we can't tell whether
        that trade is worth it unless every truncation is visible."""
        # One long item plus one long exhibit already overruns the total
        # cap — the ordinary shape of a newsworthy 8-K.
        filing = _filing(items=[Item("1.01", "Material Agreement",
                                     "x " * 3_000, 2, "Contract")])
        with caplog.at_level("WARNING"):
            msg = _build_user_message(filing, {"EX-99.1": "y " * 4_000})
        assert "truncated" in caplog.text
        assert len(msg) <= briefing_module._TOTAL_TEXT_CAP + 40

    def test_no_warning_when_the_source_fits(self, caplog):
        with caplog.at_level("WARNING"):
            _build_user_message(_item_filing(), {})
        assert "truncated" not in caplog.text

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


class TestCoerceDealTerms:
    def test_plain_strings_pass_through(self):
        assert _coerce_deal_terms({
            "deal_value": "$11.5B", "consideration_type": "mixed",
        }) == {"deal_value": "$11.5B", "consideration_type": "mixed"}

    def test_strips_whitespace_and_drops_empties(self):
        assert _coerce_deal_terms({
            "deal_value": "  $2.1B  ", "premium": "", "counterparty": None,
        }) == {"deal_value": "$2.1B"}

    def test_numbers_become_strings(self):
        assert _coerce_deal_terms({"share_count": 2150000}) == {
            "share_count": "2150000"}

    def test_single_value_object_is_unwrapped(self):
        # The AbbVie case: the model wrapped a total it had to add up, and a
        # bare str() would have stored "{'$sum': '11500000000'}".
        assert _coerce_deal_terms({
            "deal_value": {"$sum": "11500000000"},
        }) == {"deal_value": "11500000000"}

    def test_single_element_list_is_unwrapped(self):
        assert _coerce_deal_terms({"deal_value": ["$500M"]}) == {
            "deal_value": "$500M"}

    def test_multi_value_container_is_dropped(self):
        # Several tranches with no stated total: formatting these into one
        # value would be a guess, so the field is omitted entirely.
        assert _coerce_deal_terms({
            "deal_value": ["$500M", "$7B", "$4B"], "deal_type": "Debt raise",
        }) == {"deal_type": "Debt raise"}

    def test_deep_nesting_is_dropped(self):
        assert _coerce_deal_terms({
            "deal_value": {"a": {"b": {"c": "$1B"}}},
        }) == {}

    def test_booleans_are_dropped(self):
        assert _coerce_deal_terms({"deal_value": True}) == {}

    def test_non_dict_input_yields_empty(self):
        assert _coerce_deal_terms([("deal_value", "$1B")]) == {}
        assert _coerce_deal_terms(None) == {}

    def test_non_string_keys_are_dropped(self):
        assert _coerce_deal_terms({7: "$1B", "deal_value": "$2B"}) == {
            "deal_value": "$2B"}

    def test_nested_object_never_reaches_the_briefing(self):
        filing = _item_filing()
        client = _mock_groq({
            "headline": "SmallCap agrees to a debt raise of up to $11.5B",
            "summary": "SmallCap Industries Inc. is issuing senior notes.",
            "primary_category": "debt_issuance",
            "categories": ["debt_issuance"],
            "significance": "Medium",
            "sentiment": "Neutral",
            "deal_terms": {"deal_value": {"$sum": "11500000000"},
                           "deal_type": "Debt raise"},
        })
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.deal_terms["deal_value"] == "11500000000"
        assert all(isinstance(v, str) for v in result.deal_terms.values())
        assert "{" not in result.deal_terms["deal_value"]


class TestFactsOnlyBriefing:
    def test_headline_from_item_categories(self):
        filing = _filing(form_type="8-K/A", items=[
            Item("1.01", "Entry into a Material Definitive Agreement",
                 "", 2, "Contract"),
        ])
        b = facts_only_briefing(filing)
        assert b.headline == "8-K/A filed: Contract"
        assert b.primary_event_type == "Strategic Transactions"
        assert b.mode == "facts_only"
        # No LLM ran, so no leaf-level claim is made
        assert b.taxonomy == []

    def test_item_type_mapping_deterministic(self):
        filing = _filing(form_type="8-K", items=[
            Item("2.02", "Results of Operations", "", 2, "Earnings"),
            Item("5.02", "Departure of Directors", "", 2, "Leadership"),
        ])
        b = facts_only_briefing(filing)
        assert b.event_types == ["Financial Results", "Leadership & Governance"]

    def test_item_mapping_stays_canonical(self):
        for number, label in taxonomy.ITEM_CATEGORIES.items():
            assert label in EVENT_TYPES, f"{number} maps to non-canonical {label!r}"

    def test_significance_from_tier(self):
        filing = _filing(form_type="8-K", items=[
            Item("1.03", "Bankruptcy", "", 1, "Bankruptcy"),
        ])
        b = facts_only_briefing(filing)
        assert b.significance == "High"
        assert b.primary_event_type == "Risk Events"

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
            "primary_category": "supply_or_distribution_agreement",
            "categories": ["supply_or_distribution_agreement"],
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
        assert result.primary_event_type == "Operations & Strategy"
        # the specific leaf is kept alongside the simple category
        assert result.taxonomy == ["supply_or_distribution_agreement"]
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
            "primary_category": "blockbuster_deal",     # not a taxonomy leaf
            "categories": ["blockbuster_deal", "SUPPLY_OR_DISTRIBUTION_AGREEMENT"],
            "significance": "Massive",                  # not canonical
            "sentiment": "Euphoric",                    # not canonical
            "investor_takeaway": "",
        })
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "llm"
        # The unknown leaf is dropped; the recognizable one still classifies
        assert result.primary_event_type == "Operations & Strategy"
        assert result.event_types == ["Operations & Strategy"]
        assert result.taxonomy == ["supply_or_distribution_agreement"]
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
        assert result.primary_event_type == "Strategic Transactions"
        assert result.event_types == ["Strategic Transactions"]

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
            "primary_category": "supply_or_distribution_agreement",
            "categories": ["supply_or_distribution_agreement"],
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
    def test_default_chain_drops_decommissioned_models(self):
        """Every model Groq has retired on us so far must stay out."""
        chain = briefing_module._load_model_chain()
        for retired in ("meta-llama/llama-4-scout-17b-16e-instruct",
                        "llama-3.3-70b-versatile",
                        "llama-3.1-8b-instant"):
            assert retired not in chain
        assert len(chain) >= 2

    def test_env_override_parses_and_trims(self, monkeypatch):
        monkeypatch.setenv("GROQ_MODELS", " model-a , model-b ,")
        assert briefing_module._load_model_chain() == ["model-a", "model-b"]

    def test_model_kwargs_only_name_models_in_the_chain(self):
        """A stale kwargs entry would silently target a model we never call."""
        for model in briefing_module._MODEL_KWARGS:
            assert model in briefing_module._DEFAULT_MODEL_CHAIN


class TestPerModelKwargs:
    def test_reasoning_effort_sent_for_reasoning_models(self):
        filing = _item_filing()
        client = _mock_groq({
            "headline": "SmallCap signs a five-year supply deal",
            "summary": "A supply agreement was signed with Widget Partners LLC.",
            "primary_category": "supply_or_distribution_agreement",
            "categories": ["supply_or_distribution_agreement"],
            "significance": "Medium",
            "sentiment": "Positive",
            "investor_takeaway": "Supports expansion.",
        })
        with patch.object(briefing_module, "Groq", return_value=client):
            generate_briefing(filing, {})
        kwargs = client.chat.completions.create.call_args.kwargs
        expected = briefing_module._MODEL_KWARGS.get(briefing_module._MODEL_CHAIN[0], {})
        for key, value in expected.items():
            assert kwargs[key] == value

    def test_rejected_extra_kwarg_retries_plain_on_same_model(self):
        """A 400 on our own extras must not cost us the model."""
        filing = _item_filing()
        good = {
            "headline": "SmallCap signs a five-year supply deal",
            "summary": "A supply agreement was signed with Widget Partners LLC.",
            "primary_category": "supply_or_distribution_agreement",
            "categories": ["supply_or_distribution_agreement"],
            "significance": "Medium",
            "sentiment": "Positive",
            "investor_takeaway": "Supports expansion.",
        }
        response = MagicMock()
        response.choices[0].message.content = json.dumps(good)
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            _FakeGroqError("'reasoning_effort' is not supported",
                           status_code=400, code="invalid_request_error"),
            response,
        ]
        with patch.object(briefing_module, "_MODEL_KWARGS",
                          {briefing_module._MODEL_CHAIN[0]: {"reasoning_effort": "low"}}), \
             patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "llm"
        calls = client.chat.completions.create.call_args_list
        assert [c.kwargs["model"] for c in calls] == [briefing_module._MODEL_CHAIN[0]] * 2
        assert "reasoning_effort" in calls[0].kwargs
        assert "reasoning_effort" not in calls[1].kwargs

    def test_ordinary_400_is_not_treated_as_our_parameter(self):
        exc = _FakeGroqError("context length exceeded", status_code=400,
                             code="invalid_request_error")
        assert not briefing_module._is_unsupported_parameter(exc, ["reasoning_effort"])


class TestUnusableAnswers:
    """A model that answers with nothing parseable is a model problem —
    try the next one rather than publishing facts-only."""

    def _good_response(self):
        response = MagicMock()
        response.choices[0].message.content = json.dumps({
            "headline": "SmallCap signs a five-year supply deal",
            "summary": "A supply agreement was signed with Widget Partners LLC.",
            "primary_category": "supply_or_distribution_agreement",
            "categories": ["supply_or_distribution_agreement"],
            "significance": "Medium",
            "sentiment": "Positive",
            "investor_takeaway": "Supports expansion.",
        })
        return response

    def _response_with(self, content):
        response = MagicMock()
        response.choices[0].message.content = content
        return response

    def test_empty_completion_falls_through(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            self._response_with("   "), self._good_response()]
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(_item_filing(), {})
        assert result.mode == "llm"
        assert client.chat.completions.create.call_count == 2

    def test_unparseable_json_falls_through(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = [
            self._response_with("Here you go: {oops"), self._good_response()]
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(_item_filing(), {})
        assert result.mode == "llm"
        assert client.chat.completions.create.call_count == 2

    def test_unusable_answer_on_last_model_is_facts_only(self):
        client = MagicMock()
        client.chat.completions.create.return_value = self._response_with("")
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(_item_filing(), {})
        assert result.mode == "facts_only"
        assert client.chat.completions.create.call_count == len(briefing_module._MODEL_CHAIN)

    def test_transport_error_does_not_burn_the_chain(self):
        """A 500 says nothing about the model — retrying the rest is waste."""
        client = MagicMock()
        client.chat.completions.create.side_effect = _FakeGroqError(
            "server error", status_code=500, code="internal_server_error")
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(_item_filing(), {})
        assert result.mode == "facts_only"
        assert client.chat.completions.create.call_count == 1


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
            "primary_category": "supply_or_distribution_agreement",
            "categories": ["supply_or_distribution_agreement"],
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
        assert result.primary_event_type == "Operations & Strategy"
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
        assert result.primary_event_type == "Strategic Transactions"
        # Every model in the chain was attempted before giving up.
        assert (client.chat.completions.create.call_count
                == len(briefing_module._MODEL_CHAIN))
