"""Alerts on saved feed views (dispatcher.trigger_alerts / trigger_view_alerts)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.models.alert_preference import AlertPreference
from app.models.company import Company
from app.models.company_read_state import CompanyReadState
from app.models.event_type import EventType
from app.models.feed_view import FeedView
from app.models.filing_event import FilingEvent
from app.models.notification import Notification
from app.models.price_reaction import PriceReaction
from app.models.watchlist import Watchlist
from app.services.alerts import dispatcher

SEND = "app.services.alerts.channels.email.EmailChannel.send"


@pytest.fixture
def bio(db_session):
    c = Company(name="Bio Inc.", ticker="BIO", cik="111", sector="Healthcare",
                industry="Biotechnology", market_cap=800_000_000, listed=True)
    db_session.session.add(c)
    db_session.session.commit()
    return c


def _event(db_session, company, eid="e1", *, significance="High", age=timedelta(0)):
    when = datetime.now(timezone.utc) - age
    e = FilingEvent(
        edgar_id=eid, signal_type="8-K", company_id=company.id, cik=company.cik,
        ticker=company.ticker, company_name=company.name, max_tier=2,
        filing_date=when, created_at=when,
        briefing_json={"headline": "Bio wins approval", "significance": significance,
                       "sentiment": "Positive"},
    )
    e.event_types.append(EventType(type_name="Regulatory / Clinical"))
    db_session.session.add(e)
    db_session.session.commit()
    return e


def _view(db_session, user, filters, *, name="Biotech", alerts=True, position=0):
    v = FeedView(user_id=user.id, name=name, filters=filters, alerts_enabled=alerts,
                 position=position)
    db_session.session.add(v)
    db_session.session.commit()
    return v


def _publish(app, event, followers=frozenset()):
    dispatcher._with_app_context(app, dispatcher._publish_inner, event.id, frozenset(followers))


def _reaction_pass(app, event):
    dispatcher._with_app_context(app, dispatcher._views_inner, event.id, True)


class TestViewAlerts:
    def test_a_matching_view_alerts_a_reader_who_does_not_follow_the_company(
            self, app, db_session, sample_user, bio):
        view = _view(db_session, sample_user, {"scope": "all", "industry": ["biotechnology"]})
        event = _event(db_session, bio)
        with patch(SEND) as send:
            _publish(app, event)
        send.assert_called_once()
        assert send.call_args.kwargs["view"].id == view.id
        n = Notification.query.filter_by(user_id=sample_user.id).one()
        assert n.status == "sent" and n.feed_view_id == view.id

    def test_no_alert_when_the_view_does_not_match(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Energy"]})
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio))
        send.assert_not_called()

    def test_no_alert_from_a_view_with_alerts_off(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"]}, alerts=False)
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio))
        send.assert_not_called()

    def test_following_and_a_matching_view_send_one_alert(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"]})
        db_session.session.add(AlertPreference(user_id=sample_user.id, channels_json={"email": True}))
        db_session.session.commit()
        event = _event(db_session, bio)
        with patch(SEND) as send:
            _publish(app, event, {sample_user.id})
        send.assert_called_once()
        # the followed-company alert went first, so it owns the row
        assert send.call_args.kwargs["view"] is None
        assert Notification.query.filter_by(user_id=sample_user.id).count() == 1

    def test_the_settings_switch_and_mutes_still_win(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"]})
        pref = AlertPreference(user_id=sample_user.id, enabled=False, channels_json={"email": True})
        db_session.session.add(pref)
        db_session.session.commit()
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio, "e1"))
        send.assert_not_called()

        pref.enabled = True
        db_session.session.add(CompanyReadState(user_id=sample_user.id, company_id=bio.id, muted=True))
        db_session.session.commit()
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio, "e2"))
        send.assert_not_called()

    def test_uses_the_readers_channels(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"]})
        db_session.session.add(AlertPreference(user_id=sample_user.id, channels_json={"email": False}))
        db_session.session.commit()
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio))
        send.assert_not_called()

    def test_the_time_window_never_blocks_a_new_event(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"], "since": "1d"})
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio))
        send.assert_called_once()

    def test_a_mine_view_only_matches_followed_companies(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "mine", "important": True})
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio, "e1"))
        send.assert_not_called()

        wl = Watchlist(name="Mine", user_id=sample_user.id)
        wl.companies.append(bio)
        db_session.session.add(wl)
        db_session.session.commit()
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio, "e2"))
        send.assert_called_once()

    def test_first_matching_view_names_the_alert(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Energy"]}, name="Energy")
        second = _view(db_session, sample_user, {"scope": "all", "important": True},
                       name="Important", position=1)
        third = _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"]},
                      name="Health", position=2)
        assert third
        with patch(SEND) as send:
            _publish(app, _event(db_session, bio))
        send.assert_called_once()
        assert send.call_args.kwargs["view"].id == second.id


class TestMovedViews:
    def test_waits_for_the_move_then_alerts(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"], "moved": "up"})
        event = _event(db_session, bio)
        with patch(SEND) as send:
            _publish(app, event)
        send.assert_not_called()

        event.price_reactions.append(PriceReaction(
            ticker="BIO", interval="1h", measure_at=event.filing_date, status="done",
            pct_change=9.0, is_explosive=True))
        db_session.session.commit()
        with patch(SEND) as send:
            _reaction_pass(app, event)
        send.assert_called_once()

        # a later interval measuring the same move doesn't alert again
        with patch(SEND) as send:
            _reaction_pass(app, event)
        send.assert_not_called()

    def test_reaction_pass_ignores_views_without_a_move_filter(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "sector": ["Healthcare"]})
        event = _event(db_session, bio)
        event.price_reactions.append(PriceReaction(
            ticker="BIO", interval="1h", measure_at=event.filing_date, status="done",
            pct_change=9.0, is_explosive=True))
        db_session.session.commit()
        with patch(SEND) as send:
            _reaction_pass(app, event)
        send.assert_not_called()

    def test_an_old_event_moving_is_not_news(self, app, db_session, sample_user, bio):
        _view(db_session, sample_user, {"scope": "all", "moved": "any"})
        event = _event(db_session, bio, age=timedelta(days=5))
        event.price_reactions.append(PriceReaction(
            ticker="BIO", interval="1w", measure_at=event.filing_date, status="done",
            pct_change=-15.0, is_explosive=True))
        db_session.session.commit()
        with patch(SEND) as send:
            _reaction_pass(app, event)
        send.assert_not_called()
