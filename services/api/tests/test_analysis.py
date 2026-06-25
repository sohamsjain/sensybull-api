"""Tests for the second-order analysis pipeline.

Covers the pure deterministic layers (number parsing, playbook math,
fundamentals extraction) and the orchestration (engine + worker) with the LLM
and SEC companyfacts mocked, plus the thesis API endpoint.
"""
from unittest.mock import patch

import pytest

from app.models.event_analysis import EventAnalysis
from app.models.filing_event import FilingEvent
from app.models.thesis_revision import ThesisRevision
from app.services.analysis import playbooks
from app.services.analysis.playbooks import parse_money, parse_count, run_playbook
from app.services.fundamentals.extract import build_snapshot


# ── number parsing ───────────────────────────────────────────────────────────
class TestParsing:
    @pytest.mark.parametrize("text,expected", [
        ("$200M", 200e6),
        ("1.5 billion", 1.5e9),
        ("$3,000,000", 3e6),
        ("$750K", 750e3),
        (250000000, 250e6),
        ("not a number", None),
        (None, None),
    ])
    def test_parse_money(self, text, expected):
        assert parse_money(text) == expected

    def test_parse_count(self):
        assert parse_count("3,000,000 shares") == 3e6
        assert parse_count("5M shares") == 5e6


# ── fundamentals extraction ──────────────────────────────────────────────────
class TestExtract:
    def _facts(self):
        return {
            "entityName": "Acme Corp",
            "facts": {"us-gaap": {
                "StockholdersEquity": {"units": {"USD": [
                    {"end": "2024-12-31", "val": 1000, "filed": "2025-02-01"},
                    {"end": "2025-03-31", "val": 1100, "filed": "2025-05-01"}]}},
                "LongTermDebtNoncurrent": {"units": {"USD": [
                    {"end": "2025-03-31", "val": 2000, "filed": "2025-05-01"}]}},
                "DebtCurrent": {"units": {"USD": [
                    {"end": "2025-03-31", "val": 500, "filed": "2025-05-01"}]}},
                "OperatingIncomeLoss": {"units": {"USD": [
                    {"start": "2024-01-01", "end": "2024-12-31", "val": 800, "filed": "2025-02-01"},
                    {"start": "2025-01-01", "end": "2025-03-31", "val": 210, "filed": "2025-05-01"}]}},
            }},
        }

    def test_latest_value_and_debt_assembly(self):
        snap = build_snapshot(self._facts())
        m = snap["metrics"]
        assert m["equity"] == 1100              # latest period wins
        assert m["total_debt"] == 2500          # long-term + current
        assert m["operating_income"] == 800     # prefers full-year over quarter
        assert snap["as_of"] == "2025-03-31"

    def test_missing_tags_reported(self):
        snap = build_snapshot(self._facts())
        assert "revenue" in snap["missing"]
        assert "cash" in snap["missing"]

    def test_empty_facts(self):
        snap = build_snapshot(None)
        assert snap["missing"] == ["all"]
        assert snap["metrics"] == {}


# ── playbook math ────────────────────────────────────────────────────────────
class TestPlaybooks:
    SNAP = {"metrics": {
        "total_debt": 1000e6, "equity": 2000e6, "operating_income": 400e6,
        "interest_expense": 100e6, "cash": 500e6, "assets": 5000e6,
        "shares_outstanding": 100e6, "revenue": 3000e6, "net_income": 300e6,
        "current_assets": 1500e6, "current_liabilities": 1000e6,
    }}

    def test_debt_playbook_ratios(self):
        res = run_playbook(["Debt / Financing"], self.SNAP, {"deal_value": "$500M"})
        r = res.ratios
        assert res.playbook == "debt_financing"
        assert r["debt_to_equity"] == pytest.approx(0.5)
        assert r["new_debt_usd"] == 500e6
        assert r["new_debt_pct_of_existing_debt"] == pytest.approx(0.5)
        assert r["proforma_debt_to_equity"] == pytest.approx(0.75)
        assert r["interest_coverage"] == pytest.approx(4.0)

    def test_dilution_playbook(self):
        res = run_playbook(["Share Offering"], self.SNAP,
                           {"share_count": "10,000,000", "deal_value": "$300M"})
        assert res.playbook == "dilution"
        assert res.ratios["dilution_pct"] == pytest.approx(0.1)  # 10M / 100M

    def test_acquisition_playbook(self):
        res = run_playbook(["Acquisition"], self.SNAP, {"deal_value": "$400M"})
        assert res.playbook == "acquisition"
        assert res.ratios["cash_funded_feasible"] is True  # 400M <= 500M cash

    def test_unknown_type_falls_back_to_generic(self):
        res = run_playbook(["Leadership Change"], self.SNAP, {})
        assert res.playbook == "generic"
        assert res.ratios["debt_to_equity"] == pytest.approx(0.5)
        assert res.ratios["operating_margin"] == pytest.approx(400 / 3000)

    def test_priority_picks_specific_over_generic(self):
        # Multi-type event: debt playbook should win over the generic fallback.
        res = run_playbook(["Leadership Change", "Debt / Financing"], self.SNAP, {})
        assert res.playbook == "debt_financing"

    def test_handles_missing_metrics_without_crashing(self):
        res = run_playbook(["Debt / Financing"], {"metrics": {}}, {})
        assert res.ratios["debt_to_equity"] is None


# ── engine orchestration (LLM + fundamentals mocked) ─────────────────────────
def _make_event(db_session, company, edgar_id="an-001", event_types=None, briefing=None):
    event = FilingEvent(
        edgar_id=edgar_id, signal_type="8-K", company_id=company.id,
        cik=company.cik, ticker=company.ticker, company_name=company.name,
        max_tier=2, items_json=[], exhibits_json=[],
        briefing_json=briefing or {"headline": "New $500M notes",
                                    "deal_terms": {"deal_value": "$500M"}},
        event_types_json=event_types or ["Debt / Financing"],
    )
    db_session.session.add(event)
    db_session.session.commit()
    return event


_FAKE_LLM = {
    "insight": "The new notes modestly raise leverage but coverage stays healthy.",
    "bull_points": ["Coverage 4x"], "bear_points": ["D/E rises"],
    "confidence": "medium", "caveats": [],
    "thesis_narrative": "Acme is a moderately levered cash generator...",
    "thesis_change_summary": "Added $500M in notes, D/E to 0.75x.",
    "thesis_points": {"bull": ["Cash flow"], "bear": ["Leverage"], "uncertainties": []},
    "model": "test-model",
}

_FAKE_SNAPSHOT = {
    "entity_name": "Apple Inc.", "as_of": "2025-03-31", "currency": "USD",
    "metrics": {"total_debt": 1000e6, "equity": 2000e6, "operating_income": 400e6,
                "interest_expense": 100e6, "cash": 500e6},
    "missing": [],
}


class TestEngine:
    def test_analyze_event_persists_analysis_and_thesis(self, app, db_session, sample_company):
        from app.services.analysis.engine import analyze_event

        event = _make_event(db_session, sample_company)
        with patch("app.services.analysis.engine.get_company_snapshot", return_value=_FAKE_SNAPSHOT), \
             patch("app.services.analysis.engine.generate_analysis", return_value=_FAKE_LLM):
            analyze_event(event.id)

        db_session.session.expire_all()
        ev = db_session.session.get(FilingEvent, event.id)
        assert ev.analysis_status == "done"
        analysis = EventAnalysis.query.filter_by(filing_event_id=event.id).first()
        assert analysis is not None
        assert analysis.metrics_json["playbook"] == "debt_financing"
        assert analysis.insight_json["confidence"] == "medium"

        rev = ThesisRevision.query.filter_by(company_id=sample_company.id).first()
        assert rev is not None and rev.version == 1
        assert sample_company.current_thesis_revision_id == rev.id

    def test_thesis_builds_on_prior_revision(self, app, db_session, sample_company):
        from app.services.analysis.engine import analyze_event

        e1 = _make_event(db_session, sample_company, edgar_id="an-101")
        with patch("app.services.analysis.engine.get_company_snapshot", return_value=_FAKE_SNAPSHOT), \
             patch("app.services.analysis.engine.generate_analysis", return_value=_FAKE_LLM):
            analyze_event(e1.id)

        e2 = _make_event(db_session, sample_company, edgar_id="an-102")
        captured = {}

        def _capture(ctx):
            captured.update(ctx)
            return _FAKE_LLM

        with patch("app.services.analysis.engine.get_company_snapshot", return_value=_FAKE_SNAPSHOT), \
             patch("app.services.analysis.engine.generate_analysis", side_effect=_capture):
            analyze_event(e2.id)

        # The second call must receive the first thesis as prior context.
        assert captured["prior_thesis"] == _FAKE_LLM["thesis_narrative"]
        revs = (ThesisRevision.query.filter_by(company_id=sample_company.id)
                .order_by(ThesisRevision.version).all())
        assert [r.version for r in revs] == [1, 2]

    def test_analyze_proceeds_without_fundamentals(self, app, db_session, sample_company):
        from app.services.analysis.engine import analyze_event

        event = _make_event(db_session, sample_company, edgar_id="an-201")
        with patch("app.services.analysis.engine.get_company_snapshot", return_value=None), \
             patch("app.services.analysis.engine.generate_analysis", return_value=_FAKE_LLM):
            analyze_event(event.id)

        db_session.session.expire_all()
        ev = db_session.session.get(FilingEvent, event.id)
        assert ev.analysis_status == "done"


# ── worker fallback ──────────────────────────────────────────────────────────
class _FakeSocketIO:
    def __init__(self):
        self.emitted = []

    def emit(self, event, data, room=None, namespace=None):
        self.emitted.append({"event": event, "room": room})


class TestWorkerFallback:
    def test_failed_analysis_still_fans_out(self, app, db_session, sample_company):
        from app.services.analysis import worker

        event = _make_event(db_session, sample_company, edgar_id="an-301")
        sio = _FakeSocketIO()

        with patch("app.services.analysis.engine.analyze_event", side_effect=RuntimeError("LLM down")):
            worker._process(app, sio, event.id)

        db_session.session.expire_all()
        ev = db_session.session.get(FilingEvent, event.id)
        assert ev.analysis_status == "failed"
        # The instant briefing was still delivered (public room emit).
        assert any(e["room"] == "public" for e in sio.emitted)


# ── thesis API endpoint ──────────────────────────────────────────────────────
class TestThesisEndpoint:
    def test_requires_auth(self, client, sample_company):
        resp = client.get(f"/api/v1/companies/{sample_company.id}/thesis")
        assert resp.status_code == 401

    def test_returns_current_and_revisions(self, client, db_session, auth_headers, sample_company):
        r1 = ThesisRevision(company_id=sample_company.id, version=1,
                            narrative="v1 story", change_summary="seed")
        r2 = ThesisRevision(company_id=sample_company.id, version=2,
                            narrative="v2 story", change_summary="update")
        db_session.session.add_all([r1, r2])
        db_session.session.flush()
        sample_company.current_thesis_revision_id = r2.id
        db_session.session.commit()

        resp = client.get(f"/api/v1/companies/{sample_company.id}/thesis", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["current"]["narrative"] == "v2 story"
        assert [rev["version"] for rev in data["revisions"]] == [2, 1]

    def test_no_thesis_yet(self, client, auth_headers, sample_company):
        resp = client.get(f"/api/v1/companies/{sample_company.id}/thesis", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()["current"] is None


# expose the playbooks module for ad-hoc debugging
assert hasattr(playbooks, "run_playbook")
