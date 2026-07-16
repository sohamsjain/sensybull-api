"""Materiality gate: prefilter + mocked-LLM classification paths."""

from unittest.mock import patch

import pytest

from press_release import materiality
from press_release.materiality import Drop, classify_release, prefilter_reason

_LONG_BODY = "Material business development text. " * 30


def _llm_response(**overrides):
    base = {
        "issued_by_company": True,
        "material": True,
        "headline": "Acme acquires Widgets Co for $500M",
        "summary": "Acme agreed to acquire Widgets Co for $500 million in cash.",
        "primary_event_type": "Acquisition",
        "event_types": ["Acquisition", "Material Agreement"],
        "deal_terms": {"deal_value": "$500M", "consideration_type": "cash"},
        "significance": "High",
        "sentiment": "Positive",
        "investor_takeaway": "Adds scale.",
        "catalysts": [{"date": "2026-09-01", "event": "Expected close"}],
    }
    base.update(overrides)
    return base


# ── prefilter ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headline", [
    "Acme to Present at the 2026 Growth Investor Conference",
    "Acme CEO to Participate in Fireside Chat",
    "Acme Named a Leader in Widget Automation",
    "Acme Wins Prestigious Design Award",
    "Acme Celebrates 25th Anniversary",
    "Acme to Host Webinar on Widget Trends",
])
def test_prefilter_drops_promo(headline):
    assert prefilter_reason(headline, _LONG_BODY) is not None


def test_prefilter_drops_thin_body():
    assert prefilter_reason("Acme Announces Acquisition", "too short") == "body_too_short"


def test_prefilter_passes_material_news():
    assert prefilter_reason("Acme Announces Definitive Merger Agreement", _LONG_BODY) is None


def test_prefilter_does_not_call_llm():
    with patch.object(materiality, "_chat_json") as mock_chat:
        reason = prefilter_reason("Acme to Present at a Conference", _LONG_BODY)
        assert reason is not None
        mock_chat.assert_not_called()


# ── classify_release ──────────────────────────────────────────────────────

def _classify():
    return classify_release("Raw headline", _LONG_BODY, "Acme Inc", "ACME",
                            "2026-07-15T08:30:00+00:00")


def test_publish_path():
    with patch.object(materiality, "_chat_json", return_value=_llm_response()):
        briefing = _classify()
    assert not isinstance(briefing, Drop)
    assert briefing.primary_event_type == "Acquisition"
    assert briefing.significance == "High"
    assert briefing.mode == "llm"
    assert briefing.event_types[0] == "Acquisition"
    assert briefing.deal_terms["deal_value"] == "$500M"


def test_not_first_party_drops():
    with patch.object(materiality, "_chat_json",
                      return_value=_llm_response(issued_by_company=False)):
        result = _classify()
    assert isinstance(result, Drop) and result.reason == "llm_not_first_party"


def test_not_material_drops():
    with patch.object(materiality, "_chat_json",
                      return_value=_llm_response(material=False)):
        result = _classify()
    assert isinstance(result, Drop) and result.reason == "llm_not_material"


def test_other_category_drops():
    with patch.object(materiality, "_chat_json",
                      return_value=_llm_response(primary_event_type="Other")):
        result = _classify()
    assert isinstance(result, Drop) and result.reason == "llm_no_material_category"


def test_insufficient_content_drops():
    with patch.object(materiality, "_chat_json",
                      return_value={"insufficient_content": True}):
        result = _classify()
    assert isinstance(result, Drop) and result.reason == "llm_insufficient_content"


def test_llm_failure_raises_to_caller():
    with patch.object(materiality, "_chat_json", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _classify()


def test_regulatory_clinical_is_valid_event_type():
    resp = _llm_response(primary_event_type="Regulatory / Clinical",
                         event_types=["Regulatory / Clinical"])
    with patch.object(materiality, "_chat_json", return_value=resp):
        briefing = _classify()
    assert briefing.primary_event_type == "Regulatory / Clinical"


def test_primary_prepended_when_missing_from_event_types():
    resp = _llm_response(primary_event_type="Acquisition", event_types=["Earnings"])
    with patch.object(materiality, "_chat_json", return_value=resp):
        briefing = _classify()
    assert briefing.event_types[0] == "Acquisition"
    assert "Earnings" in briefing.event_types


# ── pipeline-level: non-English drop ──────────────────────────────────────

def test_non_english_release_dropped_before_any_work():
    """Wires syndicate translations (dc:language) — drop pre-fetch, pre-LLM."""
    from press_release.feeds import PRRelease
    from press_release.pipeline import _new_counters, process_release

    release = PRRelease(
        guid="g1", wire="prnewswire", url="https://example.com/de",
        headline="Die technologischen Durchbrüche hinter der ESS-Plattform",
        body_html="<p>" + "Deutscher Text. " * 50 + "</p>",
        published="Thu, 16 Jul 2026 11:07:00 +0000",
        language="de",
    )
    counters = _new_counters()
    with patch.object(materiality, "_chat_json") as mock_chat:
        result = process_release(release, {"MU": {"cik": "1", "name": "x", "norm_name": "x"}},
                                 [], counters)
    assert result is None
    assert counters["non_english"] == 1
    mock_chat.assert_not_called()
