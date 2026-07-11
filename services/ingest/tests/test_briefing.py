"""Briefing prompt rendering, generation, and anti-hallucination gates (Groq mocked)."""

import json
from unittest.mock import MagicMock, patch

import briefing as briefing_module
from briefing import (
    EVENT_TYPES,
    _build_user_message,
    _system_prompt,
    facts_only_briefing,
    generate_briefing,
)
from forms import get_spec
from models import Exhibit, Filing, Item


def _filing(**overrides):
    base = dict(
        id="urn:test:1", title="SmallCap Industries Inc.", cik="0000012345",
        ticker="SMCP", updated="2026-07-03T15:30:00-04:00",
        url="https://example.test/index.htm",
    )
    base.update(overrides)
    return Filing(**base)


# Long enough to clear the minimum-source gate, with concrete grounded facts.
_ACTIVIST_EXCERPT = (
    "Activist Capital LP has acquired 2,150,000 shares, representing 8.2% of "
    "the outstanding common stock of SmallCap Industries Inc. The reporting "
    "person acquired the shares for aggregate consideration of $4,300,000 "
    "and intends to engage with the board of directors regarding strategic "
    "alternatives, including a possible sale of the company."
)


def _mock_groq(payload: dict):
    client = MagicMock()
    response = MagicMock()
    response.choices[0].message.content = json.dumps(payload)
    client.chat.completions.create.return_value = response
    return client


class TestSystemPrompt:
    def test_8k_prompt_keeps_historical_shape(self):
        prompt = _system_prompt(get_spec("8-K"))
        assert "Given a 8-K filing" in prompt
        assert "buyside special-situations analyst" in prompt
        assert '"headline"' in prompt and '"catalysts"' in prompt
        # The literal JSON example must survive templating
        assert '{"date": "YYYY-MM-DD" or null, "event": "description"}' in prompt

    def test_grounding_rules_present(self):
        prompt = _system_prompt(get_spec("8-K"))
        assert "CRITICAL GROUNDING RULES" in prompt
        assert "insufficient_content" in prompt
        assert "Never invent or recall a counterparty" in prompt

    def test_8ka_prompt_warns_about_amendments(self):
        prompt = _system_prompt(get_spec("8-K/A"))
        assert "AMENDMENT" in prompt
        assert "NEVER reconstruct or guess" in prompt

    def test_13d_prompt_includes_hint_and_category(self):
        prompt = _system_prompt(get_spec("SC 13D"))
        assert "Given a SC 13D filing (Activist Stake)" in prompt
        assert "FORM-SPECIFIC GUIDANCE (SC 13D)" in prompt
        assert "Purpose of Transaction" in prompt

    def test_none_spec_falls_back_to_8k(self):
        assert "Given a 8-K filing" in _system_prompt(None)

    def test_new_event_types_canonical(self):
        assert "Insider Buying" in EVENT_TYPES
        assert "Late Filing" in EVENT_TYPES


class TestUserMessage:
    def test_includes_form_and_filed_by(self):
        filing = _filing(form_type="SC 13D", filed_by="Activist Capital LP")
        msg = _build_user_message(filing, {})
        assert "Form: SC 13D" in msg
        assert "Filed by: Activist Capital LP" in msg

    def test_document_excerpt_block(self):
        filing = _filing(form_type="PREM14A",
                         document_excerpt="Merger at $12.50 per share.")
        msg = _build_user_message(filing, {})
        assert "--- Filing Document (PREM14A) ---" in msg
        assert "$12.50 per share" in msg

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
        assert "Filed by:" not in msg
        assert msg.startswith("Company: SmallCap Industries Inc.")


class TestSourceGate:
    """The LLM must never be asked about text that isn't there."""

    def test_empty_filing_never_reaches_llm(self):
        groq = MagicMock()
        with patch.object(briefing_module, "Groq", groq):
            result = generate_briefing(_filing(form_type="8-K"), {})
        groq.assert_not_called()
        assert result.mode == "facts_only"
        assert result.summary == ""
        assert result.deal_terms == {}
        assert result.catalysts == []

    def test_exhibit_only_amendment_never_reaches_llm(self):
        """Regression: SOLS 8-K/A — exhibit-only amendment, no item text.

        Production hallucinated a '$30M investment from a new strategic
        investor' out of thin air. The gate must keep the LLM out entirely.
        """
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
        assert "$" not in result.headline
        assert result.summary == ""
        assert result.deal_terms == {}
        assert result.catalysts == []

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
        b = facts_only_briefing(filing, get_spec("8-K/A"))
        assert b.headline == "8-K/A filed: Contract"
        assert b.primary_event_type == "Material Agreement"
        assert b.mode == "facts_only"

    def test_item_type_mapping_deterministic(self):
        filing = _filing(form_type="8-K", items=[
            Item("2.02", "Results of Operations", "", 2, "Earnings"),
            Item("5.02", "Departure of Directors", "", 2, "Leadership"),
        ])
        b = facts_only_briefing(filing, get_spec("8-K"))
        assert b.event_types == ["Earnings", "Leadership Change"]

    def test_significance_from_tier(self):
        filing = _filing(form_type="8-K", items=[
            Item("1.03", "Bankruptcy", "", 1, "Bankruptcy"),
        ])
        b = facts_only_briefing(filing, get_spec("8-K"))
        assert b.significance == "High"
        assert b.primary_event_type == "Bankruptcy"

    def test_form_default_type_when_no_items(self):
        b = facts_only_briefing(_filing(form_type="SC 13D"), get_spec("SC 13D"))
        assert b.event_types == ["Activist Initial"]


class TestGenerateBriefing:
    def test_success_path(self):
        filing = _filing(form_type="SC 13D", filed_by="Activist Capital LP",
                         document_excerpt=_ACTIVIST_EXCERPT)
        client = _mock_groq({
            "headline": "An activist investor takes an 8.2% stake and wants strategic changes",
            "summary": "Activist Capital LP acquired 2,150,000 shares, an 8.2% "
                       "stake, for $4,300,000 and will push for strategic alternatives.",
            "primary_event_type": "Activist Initial",
            "event_types": ["Activist Initial"],
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "Watch for a 13D/A.",
        })
        with patch.object(briefing_module, "Groq", return_value=client), \
             patch.object(briefing_module, "_llm_verify", return_value=True):
            result = generate_briefing(filing, {})
        assert result.mode == "llm_verified"
        assert result.primary_event_type == "Activist Initial"
        assert result.significance == "High"
        assert "8.2%" in result.headline
        # System prompt actually sent included the 13D guidance
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        assert "FORM-SPECIFIC GUIDANCE (SC 13D)" in sent[0]["content"]

    def test_hallucinated_narrative_rejected(self):
        """Regression: ESI 8-K/A — item text says 'refiling financials', LLM
        invents an acquisition of a company pulled from model memory."""
        filing = _filing(
            form_type="8-K/A", ticker="ESI", title="Element Solutions Inc",
            items=[Item(
                "2.01", "Completion of Acquisition or Disposition of Assets",
                "This Amendment No. 1 to the Current Report on Form 8-K "
                "originally filed by Element Solutions Inc on July 6, 2026 is "
                "being filed solely to provide the financial statements and "
                "pro forma financial information required by Item 9.01(a) and "
                "9.01(b), which were previously omitted as permitted by the "
                "instructions to Form 8-K.",
                2, "Asset Deal",
            )],
        )
        client = _mock_groq({
            "headline": "Element Solutions Inc enters into agreement to acquire Ecovative Design LLC",
            "summary": "Element Solutions Inc has entered into a definitive "
                       "agreement to acquire Ecovative Design LLC, a leading "
                       "manufacturer of performance materials.",
            "primary_event_type": "M&A / Merger",
            "event_types": ["M&A / Merger", "Acquisition"],
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "Deal expected to be accretive.",
            "deal_terms": {"counterparty": "Ecovative Design LLC",
                           "deal_type": "acquisition",
                           "deal_status": "definitive agreement signed"},
        })
        with patch.object(briefing_module, "Groq", return_value=client), \
             patch.object(briefing_module, "_llm_verify", return_value=True):
            result = generate_briefing(filing, {})
        assert result.mode == "facts_only"
        assert "Ecovative" not in result.headline
        assert result.summary == ""
        assert result.deal_terms == {}

    def test_fabricated_deal_terms_and_catalysts_dropped(self):
        filing = _filing(form_type="SC 13D", filed_by="Activist Capital LP",
                         document_excerpt=_ACTIVIST_EXCERPT)
        client = _mock_groq({
            "headline": "An activist takes an 8.2% stake",
            "summary": "Activist Capital LP acquired 2,150,000 shares for $4,300,000.",
            "primary_event_type": "Activist Initial",
            "event_types": ["Activist Initial"],
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "",
            "deal_terms": {"deal_value": "$4,300,000",   # grounded — kept
                           "premium": "45%"},            # invented — dropped
            "catalysts": [{"date": "2026-09-01", "event": "board meeting"}],  # invented
        })
        with patch.object(briefing_module, "Groq", return_value=client), \
             patch.object(briefing_module, "_llm_verify", return_value=True):
            result = generate_briefing(filing, {})
        assert result.mode == "llm_verified"
        assert result.deal_terms == {"deal_value": "$4,300,000"}
        assert result.catalysts == []

    def test_ungrounded_takeaway_dropped_but_narrative_kept(self):
        filing = _filing(form_type="SC 13D", filed_by="Activist Capital LP",
                         document_excerpt=_ACTIVIST_EXCERPT)
        client = _mock_groq({
            "headline": "An activist takes an 8.2% stake",
            "summary": "Activist Capital LP acquired 2,150,000 shares.",
            "primary_event_type": "Activist Initial",
            "event_types": ["Activist Initial"],
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "Creates a $2.50/share arb spread.",  # derived number
        })
        with patch.object(briefing_module, "Groq", return_value=client), \
             patch.object(briefing_module, "_llm_verify", return_value=True):
            result = generate_briefing(filing, {})
        assert result.mode == "llm_verified"
        assert result.investor_takeaway == ""
        assert "8.2%" in result.headline

    def test_verifier_rejection_falls_back_to_facts_only(self):
        filing = _filing(form_type="SC 13D",
                         document_excerpt=_ACTIVIST_EXCERPT)
        client = _mock_groq({
            "headline": "An activist takes an 8.2% stake",
            "summary": "Activist Capital LP acquired 2,150,000 shares.",
            "primary_event_type": "Activist Initial",
            "event_types": ["Activist Initial"],
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "",
        })
        with patch.object(briefing_module, "Groq", return_value=client), \
             patch.object(briefing_module, "_llm_verify", return_value=False):
            result = generate_briefing(filing, {})
        assert result.mode == "facts_only"
        assert result.summary == ""

    def test_insufficient_content_escape_hatch(self):
        filing = _filing(form_type="SC 13D",
                         document_excerpt=_ACTIVIST_EXCERPT)
        client = _mock_groq({"insufficient_content": True})
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.mode == "facts_only"

    def test_failure_falls_back_to_form_default_type(self):
        filing = _filing(form_type="SC 13D",
                         document_excerpt=_ACTIVIST_EXCERPT)
        with patch.object(briefing_module, "Groq",
                          side_effect=RuntimeError("api down")):
            result = generate_briefing(filing, {})
        assert result.mode == "facts_only"
        assert result.primary_event_type == "Activist Initial"
        assert result.event_types == ["Activist Initial"]

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
