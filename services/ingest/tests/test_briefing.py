"""Briefing prompt rendering and generation (Groq mocked)."""

import json
from unittest.mock import MagicMock, patch

import briefing as briefing_module
from briefing import EVENT_TYPES, _build_user_message, _system_prompt, generate_briefing
from forms import get_spec
from models import Filing


def _filing(**overrides):
    base = dict(
        id="urn:test:1", title="SmallCap Industries Inc.", cik="0000012345",
        ticker="SMCP", updated="2026-07-03T15:30:00-04:00",
        url="https://example.test/index.htm",
    )
    base.update(overrides)
    return Filing(**base)


class TestSystemPrompt:
    def test_8k_prompt_keeps_historical_shape(self):
        prompt = _system_prompt(get_spec("8-K"))
        assert "Given a 8-K filing" in prompt
        assert "buyside special-situations analyst" in prompt
        assert '"headline"' in prompt and '"catalysts"' in prompt
        # The literal JSON example must survive templating
        assert '{"date": "YYYY-MM-DD" or null, "event": "description"}' in prompt

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

    def test_8k_message_unchanged(self):
        msg = _build_user_message(_filing(), {})
        assert "Form:" not in msg
        assert "Filed by:" not in msg
        assert msg.startswith("Company: SmallCap Industries Inc.")


class TestGenerateBriefing:
    def _mock_groq(self, payload: dict):
        client = MagicMock()
        response = MagicMock()
        response.choices[0].message.content = json.dumps(payload)
        client.chat.completions.create.return_value = response
        return client

    def test_success_path(self):
        filing = _filing(form_type="SC 13D",
                         document_excerpt="Activist stake of 8.2%")
        client = self._mock_groq({
            "headline": "An activist investor takes an 8.2% stake and wants strategic changes",
            "summary": "An activist crossed 5%.",
            "primary_event_type": "Activist Initial",
            "event_types": ["Activist Initial"],
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "Watch for a 13D/A.",
        })
        with patch.object(briefing_module, "Groq", return_value=client):
            result = generate_briefing(filing, {})
        assert result.primary_event_type == "Activist Initial"
        assert result.significance == "High"
        # System prompt actually sent included the 13D guidance
        sent = client.chat.completions.create.call_args.kwargs["messages"]
        assert "FORM-SPECIFIC GUIDANCE (SC 13D)" in sent[0]["content"]

    def test_failure_falls_back_to_form_default_type(self):
        filing = _filing(form_type="SC 13D")
        with patch.object(briefing_module, "Groq",
                          side_effect=RuntimeError("api down")):
            result = generate_briefing(filing, {})
        assert result.primary_event_type == "Activist Initial"
        assert result.event_types == ["Activist Initial"]

    def test_failure_8k_falls_back_to_other(self):
        with patch.object(briefing_module, "Groq",
                          side_effect=RuntimeError("api down")):
            result = generate_briefing(_filing(form_type="8-K"), {})
        assert result.primary_event_type == "Other"
