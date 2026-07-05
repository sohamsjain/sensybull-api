"""Tests for thesis-aware alert delivery: formatting, channel enrichment,
tier-bypass, mute, and the deferral exclusion."""
from unittest.mock import patch

from app.models.alert_preference import AlertPreference
from app.models.company_read_state import CompanyReadState
from app.models.filing_event import FilingEvent
from app.services.alerts import thesis_format


# ── Formatting ───────────────────────────────────────────────────────────
class TestThesisFormat:
    def test_line_and_subject(self):
        a = {"impact": "breaks", "rationale": "Guidance cut."}
        assert thesis_format.subject_prefix(a) == "Thesis broken"
        assert "Thesis broken" in thesis_format.line(a)
        assert "Guidance cut." in thesis_format.line(a)

    def test_none_has_no_line(self):
        assert thesis_format.line(None) is None
        assert thesis_format.subject_prefix(None) is None

    def test_neutral_excluded_from_notify(self):
        # neutral never reaches a channel (the engine sends None for it), so
        # it isn't in the notify set even though it has a display label.
        assert "neutral" not in thesis_format.NOTIFY_IMPACTS

    def test_notify_set(self):
        assert thesis_format.NOTIFY_IMPACTS == frozenset({"supports", "threatens", "breaks"})


# ── Email template renders the thesis banner ─────────────────────────────
class TestEmailTemplate:
    def test_banner_in_html_and_text(self):
        from app.services.email.renderer import render
        ctx = {
            "app_name": "Sensybull", "frontend_url": "https://x", "support_email": "s@x",
            "user_name": "T", "ticker": "AAPL", "company_name": "Apple Inc.",
            "headline": "Something", "summary": "s", "summary_bullets": [],
            "event_types": [], "max_tier": 3, "tier_label": "Low",
            "filing_date": None, "edgar_url": "", "event_url": "https://x/e/1",
            "thesis_label": "Thesis broken", "thesis_emoji": "🔴",
            "thesis_color": "#dc2626", "thesis_rationale": "Guidance cut kills it.",
        }
        html, text = render("filing_alert", ctx)
        assert "Thesis broken" in html and "Guidance cut kills it." in html
        assert "THESIS BROKEN" in text and "Guidance cut kills it." in text

    def test_no_banner_without_thesis(self):
        from app.services.email.renderer import render
        ctx = {
            "app_name": "Sensybull", "frontend_url": "https://x", "support_email": "s@x",
            "user_name": "T", "ticker": "AAPL", "company_name": "Apple Inc.",
            "headline": "Something", "summary": "s", "summary_bullets": [],
            "event_types": [], "max_tier": 3, "tier_label": "Low",
            "filing_date": None, "edgar_url": "", "event_url": "https://x/e/1",
            "thesis_label": None, "thesis_emoji": "", "thesis_color": None,
            "thesis_rationale": None,
        }
        html, _ = render("filing_alert", ctx)
        assert "Thesis" not in html


# ── Webhook payload carries the thesis object ────────────────────────────
class TestWebhookEnrichment:
    def test_payload_includes_thesis(self, app, db_session, sample_user, sample_event):
        from app.models.channel_config import ChannelConfig
        from app.services.alerts.channels.webhook import WebhookChannel

        db_session.session.add(ChannelConfig(
            user_id=sample_user.id, channel="webhook",
            config_json={"url": "https://hook.example"},
        ))
        db_session.session.commit()

        captured = {}

        def fake_post(url, data=None, headers=None, timeout=None):
            import json
            captured["body"] = json.loads(data)
            class R:
                def raise_for_status(self): pass
            return R()

        with patch("requests.post", side_effect=fake_post):
            WebhookChannel().send(
                sample_user, sample_event, app,
                assessment={"impact": "breaks", "rationale": "boom", "thesis_status": "broken"},
            )
        assert captured["body"]["thesis"]["impact"] == "breaks"
        assert captured["body"]["thesis"]["rationale"] == "boom"


# ── Dispatch routing: tier bypass + mute ─────────────────────────────────
def _high_tier_event(db_session, company):
    ev = FilingEvent(
        edgar_id="tier3-thesis", signal_type="8-K", company_id=company.id,
        cik=company.cik, ticker="AAPL", company_name="Apple Inc.", max_tier=3,
        items_json=[], exhibits_json=[], event_types_json=[],
        briefing_json={"headline": "h"},
    )
    db_session.session.add(ev)
    db_session.session.commit()
    return ev


class TestDispatchThesisAlert:
    def test_bypasses_tier_gate(self, app, db_session, sample_user, sample_company):
        # pref only wants tier-1 alerts; event is tier-3 → regular would skip.
        db_session.session.add(AlertPreference(
            user_id=sample_user.id, enabled=True, max_tier=1,
            channels_json={"email": True}))
        event = _high_tier_event(db_session, sample_company)
        db_session.session.commit()

        from app.services.alerts.dispatcher import _dispatch_thesis
        with patch("app.services.alerts.channels.email.EmailChannel.send") as m:
            _dispatch_thesis(app, event.id, sample_user.id,
                             {"impact": "breaks", "rationale": "x"}, bypass_tier=True)
            m.assert_called_once()
            # assessment forwarded to the channel
            assert m.call_args.kwargs["assessment"]["impact"] == "breaks"

    def test_respects_mute(self, app, db_session, sample_user, sample_company):
        db_session.session.add(AlertPreference(
            user_id=sample_user.id, enabled=True, max_tier=3,
            channels_json={"email": True}))
        db_session.session.add(CompanyReadState(
            user_id=sample_user.id, company_id=sample_company.id, muted=True))
        event = _high_tier_event(db_session, sample_company)
        db_session.session.commit()

        from app.services.alerts.dispatcher import _dispatch_thesis
        with patch("app.services.alerts.channels.email.EmailChannel.send") as m:
            _dispatch_thesis(app, event.id, sample_user.id,
                             {"impact": "breaks", "rationale": "x"}, bypass_tier=True)
            m.assert_not_called()

    def test_skips_when_alerts_disabled(self, app, db_session, sample_user, sample_company):
        db_session.session.add(AlertPreference(
            user_id=sample_user.id, enabled=False, max_tier=3,
            channels_json={"email": True}))
        event = _high_tier_event(db_session, sample_company)
        db_session.session.commit()

        from app.services.alerts.dispatcher import _dispatch_thesis
        with patch("app.services.alerts.channels.email.EmailChannel.send") as m:
            _dispatch_thesis(app, event.id, sample_user.id,
                             {"impact": "breaks", "rationale": "x"}, bypass_tier=True)
            m.assert_not_called()


class TestExclusion:
    def test_trigger_alerts_excludes_deferred_users(self, app):
        from app.services.alerts.dispatcher import trigger_alerts
        with patch("app.services.alerts.dispatcher._executor") as ex:
            # all recipients deferred → nothing submitted
            trigger_alerts(app, "evt", {"u1"}, exclude_user_ids=frozenset({"u1"}))
            ex.submit.assert_not_called()
            # one remaining → submitted
            trigger_alerts(app, "evt", {"u1", "u2"}, exclude_user_ids=frozenset({"u1"}))
            ex.submit.assert_called_once()
