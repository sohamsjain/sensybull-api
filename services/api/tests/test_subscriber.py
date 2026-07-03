"""Tests for the Redis subscriber's event handling logic."""

import json
from app.models.filing_event import FilingEvent
from app.models.company import Company
from app.services.realtime.subscriber import _handle_event


def _make_filing_json(**overrides):
    """Build a minimal valid filing event JSON payload."""
    base = {
        "edgar_id": "test-sub-001",
        "signal_type": "8-K",
        "cik": "0000320193",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "filing_date": "2024-03-15T16:30:00Z",
        "edgar_url": "https://sec.gov/test",
        "accession_number": "0000320193-24-000001",
        "max_tier": 2,
        "items": [
            {"number": "1.01", "title": "Material Agreement", "tier": 2,
             "category": "Contracts", "text": "Entered into agreement."}
        ],
        "exhibits": [],
        "briefing": {
            "headline": "Apple Signs Major Deal",
            "summary": "Apple entered into a material agreement.",
            "primary_event_type": "Material Agreement",
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "Watch for details.",
            "catalysts": [
                {"date": "2024-06-01", "event": "Agreement effective date"}
            ],
            "deal_terms": {"counterparty": "Acme Corp"},
        },
        "event_types": ["Material Agreement", "Acquisition"],
    }
    base.update(overrides)
    return json.dumps(base)


class FakeSocketIO:
    """Captures emit calls for assertion."""

    def __init__(self):
        self.emitted = []

    def emit(self, event, data, room=None, namespace=None):
        self.emitted.append({
            "event": event, "data": data,
            "room": room, "namespace": namespace,
        })


class TestHandleEvent:
    def test_persists_event(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_filing_json())

        event = FilingEvent.query.filter_by(edgar_id="test-sub-001").first()
        assert event is not None
        assert event.ticker == "AAPL"
        assert event.max_tier == 2
        assert event.company_id == sample_company.id

    def test_creates_event_types(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_filing_json())

        event = FilingEvent.query.filter_by(edgar_id="test-sub-001").first()
        type_names = {et.type_name for et in event.event_types}
        assert type_names == {"Material Agreement", "Acquisition"}

    def test_creates_catalysts(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_filing_json())

        event = FilingEvent.query.filter_by(edgar_id="test-sub-001").first()
        assert len(event.catalysts) == 1
        assert event.catalysts[0].event_description == "Agreement effective date"

    def test_idempotency_skips_duplicate(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_filing_json())
        _handle_event(app, sio, _make_filing_json())  # second call

        count = FilingEvent.query.filter_by(edgar_id="test-sub-001").count()
        assert count == 1

    def test_auto_creates_unknown_company(self, app, db_session):
        """If ticker doesn't match any existing company, auto-create one."""
        sio = FakeSocketIO()
        payload = _make_filing_json(
            edgar_id="new-company-001",
            ticker="NEWCO",
            cik="9999999999",
            company_name="NewCo Inc.",
        )
        _handle_event(app, sio, payload)

        company = Company.query.filter_by(ticker="NEWCO").first()
        assert company is not None
        assert company.name == "NewCo Inc."

        event = FilingEvent.query.filter_by(edgar_id="new-company-001").first()
        assert event.company_id == company.id

    def test_emits_to_public_room(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_filing_json())

        public_emits = [e for e in sio.emitted if e["room"] == "public"]
        assert len(public_emits) == 1
        assert public_emits[0]["event"] == "filing_event"

    def test_emits_to_watchlist_users(
        self, app, db_session, sample_watchlist, sample_company
    ):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_filing_json())

        user_emits = [e for e in sio.emitted if e["room"] and e["room"].startswith("user:")]
        assert len(user_emits) == 1
        assert user_emits[0]["room"] == f"user:{sample_watchlist.user_id}"

    def test_handles_invalid_json(self, app, db_session):
        sio = FakeSocketIO()
        _handle_event(app, sio, "not valid json {{{")
        # Should not crash, no events stored
        assert FilingEvent.query.count() == 0

    def test_handles_missing_briefing(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        payload = _make_filing_json(edgar_id="no-briefing-001", briefing=None)
        _handle_event(app, sio, payload)

        event = FilingEvent.query.filter_by(edgar_id="no-briefing-001").first()
        assert event is not None
        assert event.briefing_json is None
        assert len(event.catalysts) == 0


class TestNonEightKForms:
    """Multi-form ingest: events like SC 13D arrive with no items and a
    form-level max_tier that must pass through without recomputation."""

    def test_sc13d_persists_with_form_tier(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        payload = _make_filing_json(
            edgar_id="test-sub-13d",
            signal_type="SC 13D",
            max_tier=1,
            items=[],
            event_types=["Activist Initial"],
            briefing={
                "headline": "Activist takes 8.2% stake",
                "summary": "An activist crossed 5%.",
                "primary_event_type": "Activist Initial",
                "significance": "High",
                "sentiment": "Positive",
                "investor_takeaway": "Watch for a 13D/A.",
                "catalysts": [],
                "deal_terms": {},
            },
        )
        _handle_event(app, sio, payload)

        event = FilingEvent.query.filter_by(edgar_id="test-sub-13d").first()
        assert event is not None
        assert event.signal_type == "SC 13D"
        # Empty items must NOT downgrade the form-level tier to 3
        assert event.max_tier == 1
        assert event.items_json == []
        assert event.company_id == sample_company.id

    def test_sc13d_schedules_price_reactions(self, app, db_session, sample_company):
        from app.models.price_reaction import PriceReaction

        _handle_event(app, FakeSocketIO(), _make_filing_json(
            edgar_id="test-sub-13d-pr",
            signal_type="SC 13D",
            max_tier=1,
            items=[],
        ))
        event = FilingEvent.query.filter_by(edgar_id="test-sub-13d-pr").first()
        rows = PriceReaction.query.filter_by(filing_event_id=event.id).all()
        assert len(rows) == 6

    def test_form4_insider_buy_event(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_filing_json(
            edgar_id="test-sub-form4",
            signal_type="4",
            max_tier=2,
            items=[],
            event_types=["Insider Buying"],
            briefing={
                "headline": "CFO Doe Jane buys $160k at $3.21",
                "summary": "Open-market purchase.",
                "primary_event_type": "Insider Buying",
                "significance": "Medium",
                "sentiment": "Positive",
                "investor_takeaway": "Insiders buy for one reason.",
                "catalysts": [],
                "deal_terms": {"deal_value": "$160k"},
            },
        ))
        event = FilingEvent.query.filter_by(edgar_id="test-sub-form4").first()
        assert event.signal_type == "4"
        assert event.max_tier == 2
        emitted = [e for e in sio.emitted if e["event"] == "filing_event"]
        assert emitted, "filing_event should be emitted for Form 4 events"
