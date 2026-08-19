"""Press-release events in the subscriber: persistence, cross-source dedup,
PR→8-K backfill/suppression, and the /events surface."""

import json

from app.models.filing_event import FilingEvent
from app.services.realtime.subscriber import _handle_event

from tests.test_subscriber import FakeSocketIO, _make_filing_json

# Distinct-but-close simhashes: distance("...01", "...03") == 1
_SIMHASH_A = "a3f1c2d4e5b60718"
_SIMHASH_NEAR_A = "a3f1c2d4e5b60719"     # 1 bit away from A
_SIMHASH_FAR = "5c0e3d2b1a49f8e7"


def _make_pr_json(**overrides):
    base = {
        "edgar_id": "pr:globenewswire:GNW-1",
        "signal_type": "PR",
        "source": "globenewswire",
        "issuer_name": "Apple Inc.",
        "cik": "0000320193",
        "ticker": "AAPL",
        "company_name": "Apple Inc.",
        "filing_date": "2026-07-15T08:30:00+00:00",
        "edgar_url": "https://www.globenewswire.com/news-release/example",
        "accession_number": "",
        "max_tier": 1,
        "items": [],
        "exhibits": [],
        "briefing": {
            "headline": "Apple agrees to acquire Acme for $2.1B",
            "summary": "Apple entered a definitive agreement to acquire Acme.",
            "primary_event_type": "Acquisition",
            "significance": "High",
            "sentiment": "Positive",
            "investor_takeaway": "Adds capacity.",
            "catalysts": [],
            "deal_terms": {"deal_value": "$2.1B"},
            "mode": "llm",
        },
        "event_types": ["Acquisition"],
        "content_fingerprint": "exact-fp-1",
        "headline_fingerprint": "headline-fp-1",
        "content_simhash": _SIMHASH_A,
    }
    base.update(overrides)
    return json.dumps(base)


def _make_8k_with_fps(items, exhibit_fp, **overrides):
    payload = {
        "edgar_id": "edgar-8k-1",
        "items": items,
        "source": "edgar",
        "content_fingerprint": exhibit_fp.get("exact", ""),
        "headline_fingerprint": exhibit_fp.get("headline", ""),
        "content_simhash": exhibit_fp.get("simhash", ""),
        "exhibit_fingerprints": [{"source": "EX-99.1", **exhibit_fp}],
    }
    payload.update(overrides)
    return _make_filing_json(**payload)


_WRAPPER_ITEMS = [
    {"number": "2.02", "title": "Results of Operations", "tier": 2,
     "category": "Earnings", "text": "Furnishing the press release."},
]
_SUBSTANTIVE_ITEMS = _WRAPPER_ITEMS + [
    {"number": "5.02", "title": "Departure of Directors", "tier": 2,
     "category": "Leadership", "text": "CFO resigned."},
]


class TestPrPersistence:
    def test_pr_persists_and_emits_with_new_fields(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())

        event = FilingEvent.query.filter_by(edgar_id="pr:globenewswire:GNW-1").first()
        assert event is not None
        assert event.signal_type == "PR"
        assert event.source == "globenewswire"
        assert event.issuer_name == "Apple Inc."
        assert event.content_fingerprint == "exact-fp-1"
        assert event.content_simhash == _SIMHASH_A
        assert event.related_filing_url is None

        public = [e for e in sio.emitted if e["room"] == "public"]
        assert len(public) == 1
        payload = public[0]["data"]
        assert payload["signal_type"] == "PR"
        assert payload["source"] == "globenewswire"
        assert payload["filing_url"] is None
        assert payload["important"] is True

    def test_pr_schedules_price_reactions(self, app, db_session, sample_company):
        from app.models.price_reaction import PriceReaction
        _handle_event(app, FakeSocketIO(), _make_pr_json())
        event = FilingEvent.query.filter_by(signal_type="PR").first()
        assert PriceReaction.query.filter_by(filing_event_id=event.id).count() == 6


class TestCrossWireDedup:
    def test_same_release_on_second_wire_skipped(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        # Different wire + guid, same headline fingerprint
        _handle_event(app, sio, _make_pr_json(
            edgar_id="pr:prnewswire:PRN-9",
            source="prnewswire",
            content_fingerprint="exact-fp-other",
            content_simhash=_SIMHASH_FAR,
        ))
        assert FilingEvent.query.filter(FilingEvent.signal_type == "PR").count() == 1

    def test_near_simhash_counts_as_dup(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        _handle_event(app, sio, _make_pr_json(
            edgar_id="pr:prnewswire:PRN-9",
            content_fingerprint="exact-fp-other",
            headline_fingerprint="headline-fp-other",
            content_simhash=_SIMHASH_NEAR_A,
        ))
        assert FilingEvent.query.filter(FilingEvent.signal_type == "PR").count() == 1

    def test_different_release_same_company_publishes(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        _handle_event(app, sio, _make_pr_json(
            edgar_id="pr:globenewswire:GNW-2",
            content_fingerprint="exact-fp-2",
            headline_fingerprint="headline-fp-2",
            content_simhash=_SIMHASH_FAR,
        ))
        assert FilingEvent.query.filter(FilingEvent.signal_type == "PR").count() == 2


class TestPrAfter8K:
    def test_pr_after_matching_8k_dropped(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_8k_with_fps(
            _WRAPPER_ITEMS,
            {"exact": "exact-fp-1", "headline": "hf", "simhash": _SIMHASH_FAR},
        ))
        _handle_event(app, sio, _make_pr_json())
        assert FilingEvent.query.filter(FilingEvent.signal_type == "PR").count() == 0
        assert FilingEvent.query.filter_by(edgar_id="edgar-8k-1").count() == 1


class Test8KAfterPr:
    def test_wrapper_8k_backfills_and_suppresses(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        sio.emitted.clear()

        _handle_event(app, sio, _make_8k_with_fps(
            _WRAPPER_ITEMS,
            {"exact": "no-match", "headline": "no-match", "simhash": _SIMHASH_NEAR_A},
        ))

        # No new row for the 8-K
        assert FilingEvent.query.filter_by(edgar_id="edgar-8k-1").count() == 0
        # PR event gained the filing metadata
        pr = FilingEvent.query.filter_by(signal_type="PR").first()
        assert pr.related_edgar_id == "edgar-8k-1"
        assert pr.related_filing_url == "https://sec.gov/test"
        assert pr.related_accession_number == "0000320193-24-000001"
        # An update (not a fresh event) was emitted
        updates = [e for e in sio.emitted if e["event"] == "filing_event_update"]
        assert updates and updates[-1]["data"]["filing_url"] == "https://sec.gov/test"
        assert not [e for e in sio.emitted if e["event"] == "filing_event"]

    def test_substantive_8k_backfills_and_publishes(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        sio.emitted.clear()

        _handle_event(app, sio, _make_8k_with_fps(
            _SUBSTANTIVE_ITEMS,
            {"exact": "exact-fp-1", "headline": "x", "simhash": _SIMHASH_FAR},
        ))

        # Backfill happened...
        pr = FilingEvent.query.filter_by(signal_type="PR").first()
        assert pr.related_edgar_id == "edgar-8k-1"
        # ...AND the filing published as its own event (5.02 is substance)
        assert FilingEvent.query.filter_by(edgar_id="edgar-8k-1").count() == 1
        assert [e for e in sio.emitted if e["event"] == "filing_event"]

    def test_redelivered_suppressed_8k_is_noop(self, app, db_session, sample_company):
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        wrapper = _make_8k_with_fps(
            _WRAPPER_ITEMS,
            {"exact": "exact-fp-1", "headline": "x", "simhash": _SIMHASH_FAR},
        )
        _handle_event(app, sio, wrapper)
        sio.emitted.clear()
        _handle_event(app, sio, wrapper)   # Redis redelivery

        assert FilingEvent.query.filter_by(edgar_id="edgar-8k-1").count() == 0
        assert sio.emitted == []           # no duplicate update emit either

    def test_no_fingerprints_means_no_matching(self, app, db_session, sample_company):
        """8-Ks without exhibit fingerprints (pre-rollout ingest) publish normally."""
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        _handle_event(app, sio, _make_filing_json(items=_WRAPPER_ITEMS))

        assert FilingEvent.query.filter_by(edgar_id="test-sub-001").count() == 1
        pr = FilingEvent.query.filter_by(signal_type="PR").first()
        assert pr.related_edgar_id is None

    def test_suppression_flag_off_publishes_both(self, app, db_session,
                                                  sample_company, monkeypatch):
        monkeypatch.setenv("PR_SUPPRESS_8K", "0")
        sio = FakeSocketIO()
        _handle_event(app, sio, _make_pr_json())
        _handle_event(app, sio, _make_8k_with_fps(
            _WRAPPER_ITEMS,
            {"exact": "exact-fp-1", "headline": "x", "simhash": _SIMHASH_FAR},
        ))

        # Backfill still happens, but the 8-K also publishes
        pr = FilingEvent.query.filter_by(signal_type="PR").first()
        assert pr.related_edgar_id == "edgar-8k-1"
        assert FilingEvent.query.filter_by(edgar_id="edgar-8k-1").count() == 1


class TestEventsSurface:
    def test_types_endpoint_covers_the_pr_only_categories(self, client):
        """FDA decisions and trial data reach the wire before any filing;
        they classify under Operations & Strategy."""
        resp = client.get("/api/v1/events/types")
        assert resp.status_code == 200
        types = resp.get_json()["event_types"]
        assert "Operations & Strategy" in types
        assert types[-1] == "Other"

    def test_signal_type_pr_filter(self, app, client, db_session, sample_company):
        _handle_event(app, FakeSocketIO(), _make_pr_json())
        _handle_event(app, FakeSocketIO(), _make_filing_json())

        resp = client.get("/api/v1/events/all?signal_type=PR")
        assert resp.status_code == 200
        events = resp.get_json()["events"]
        assert len(events) == 1
        assert events[0]["signal_type"] == "PR"
        assert events[0]["source"] == "globenewswire"
