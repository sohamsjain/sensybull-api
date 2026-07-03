"""Tests for the event-driven movers endpoint."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.models.filing_event import FilingEvent
from app.routes import movers as movers_module


def _make_event(db_session, company, edgar_id, days_ago=1, max_tier=1, headline="Deal"):
    event = FilingEvent(
        edgar_id=edgar_id,
        company_id=company.id,
        cik=company.cik,
        ticker=company.ticker,
        company_name=company.name,
        filing_date=datetime.now(timezone.utc) - timedelta(days=days_ago),
        max_tier=max_tier,
        briefing_json={"headline": headline, "significance": "High",
                       "primary_event_type": "Acquisition"},
    )
    db_session.session.add(event)
    db_session.session.commit()
    return event


class TestMovers:
    def test_empty_without_recent_events(self, client):
        resp = client.get("/api/v1/movers")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["gainers"] == []
        assert data["losers"] == []

    def test_ranks_and_joins_events(self, client, db_session,
                                     sample_company, sample_company_2):
        _make_event(db_session, sample_company, "mv-aapl", headline="AAPL pops")
        _make_event(db_session, sample_company_2, "mv-tsla", headline="TSLA drops")

        snapshots = {
            "AAPL": {"latestTrade": {"p": 110.0}, "prevDailyBar": {"c": 100.0}},
            "TSLA": {"latestTrade": {"p": 90.0}, "prevDailyBar": {"c": 100.0}},
        }
        with patch.object(movers_module.alpaca, "get_snapshots",
                          return_value=snapshots):
            resp = client.get("/api/v1/movers")

        data = resp.get_json()
        assert len(data["gainers"]) == 1
        gainer = data["gainers"][0]
        assert gainer["ticker"] == "AAPL"
        assert gainer["change_pct"] == 10.0
        assert gainer["event"]["headline"] == "AAPL pops"
        assert gainer["company_id"] == sample_company.id

        loser = data["losers"][0]
        assert loser["ticker"] == "TSLA"
        assert loser["change_pct"] == -10.0
        assert loser["event"]["headline"] == "TSLA drops"

    def test_prefers_material_event_over_newer_routine(self, client, db_session,
                                                        sample_company):
        _make_event(db_session, sample_company, "mv-old-t1",
                    days_ago=3, max_tier=1, headline="Material")
        _make_event(db_session, sample_company, "mv-new-t3",
                    days_ago=1, max_tier=3, headline="Routine")

        snapshots = {"AAPL": {"latestTrade": {"p": 105.0}, "prevDailyBar": {"c": 100.0}}}
        with patch.object(movers_module.alpaca, "get_snapshots",
                          return_value=snapshots):
            resp = client.get("/api/v1/movers")

        assert resp.get_json()["gainers"][0]["event"]["headline"] == "Material"

    def test_cache_hit_skips_alpaca(self, client):
        cached = {"as_of": "now", "gainers": [], "losers": []}
        with patch.object(movers_module, "cache_get", return_value=cached), \
             patch.object(movers_module.alpaca, "get_snapshots") as snapshots:
            resp = client.get("/api/v1/movers")
        assert resp.get_json() == cached
        snapshots.assert_not_called()

    def test_alpaca_down_without_stale_cache_is_503(self, client, db_session,
                                                    sample_company):
        _make_event(db_session, sample_company, "mv-503")
        with patch.object(movers_module.alpaca, "get_snapshots",
                          side_effect=movers_module.alpaca.AlpacaError("down")):
            resp = client.get("/api/v1/movers")
        assert resp.status_code == 503
