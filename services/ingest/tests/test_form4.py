"""Form 4 XML parsing, buy qualification, cluster state."""

import os

import form4_state
from form4 import (
    build_form4_briefing,
    owner_role,
    parse_form4_xml,
    qualifying_buy_value,
)
from form4_state import load_buys, record_buy, save_buys


class TestParseForm4:
    def test_parses_officer_buy(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_buy.xml"))
        assert f4.issuer_cik == "0000012345"
        assert f4.issuer_ticker == "SMCP"
        assert f4.owner_name == "Doe Jane"
        assert f4.is_officer and not f4.is_director
        assert f4.officer_title == "Chief Financial Officer"
        assert f4.aff_10b5_1 is False
        assert f4.shares_after == 310000
        assert len(f4.transactions) == 1
        txn = f4.transactions[0]
        assert (txn.code, txn.shares, txn.price, txn.acquired) == ("P", 50000, 3.21, True)

    def test_none_ticker_normalized(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_multi_txn.xml"))
        assert f4.issuer_ticker == ""

    def test_invalid_xml(self):
        assert parse_form4_xml("") is None
        assert parse_form4_xml("<not-xml") is None
        assert parse_form4_xml("<wrongRoot/>") is None


class TestQualification:
    def test_officer_open_market_buy_qualifies(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_buy.xml"))
        assert qualifying_buy_value(f4, 100_000) == 50000 * 3.21  # $160.5k

    def test_sale_never_qualifies(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_sale.xml"))
        assert qualifying_buy_value(f4, 100_000) == 0.0

    def test_10b5_1_plan_buy_is_noise(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_10b51.xml"))
        assert qualifying_buy_value(f4, 100_000) == 0.0

    def test_below_threshold_is_noise(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_buy.xml"))
        assert qualifying_buy_value(f4, 1_000_000) == 0.0

    def test_multi_txn_sums_only_p_codes(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_multi_txn.xml"))
        # 30000*3.00 + 20000*3.10 = 152000; the code-A grant is ignored
        assert qualifying_buy_value(f4, 100_000) == 152_000.0

    def test_non_insider_is_noise(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_buy.xml"))
        f4.is_officer = f4.is_director = f4.is_ten_pct = False
        assert qualifying_buy_value(f4, 100_000) == 0.0


class TestOwnerRole:
    def test_roles(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_buy.xml"))
        assert owner_role(f4) == "Chief Financial Officer"
        f4.is_director = True
        assert owner_role(f4) == "Chief Financial Officer & Director"


class TestClusterState:
    def _use_tmp_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(form4_state, "BUYS_FILE",
                            str(tmp_path / "form4_buys.json"))

    def test_record_and_dedup_by_accession(self, tmp_path, monkeypatch):
        self._use_tmp_file(tmp_path, monkeypatch)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        state = {}
        buy = {"owner_cik": "1", "owner_name": "A", "value": 150000.0,
               "date": now, "accession": "acc-1"}
        window = record_buy(state, "0000012345", buy)
        assert len(window) == 1
        window = record_buy(state, "0000012345", dict(buy))  # same accession
        assert len(window) == 1

    def test_distinct_owner_cluster_detection(self, tmp_path, monkeypatch):
        self._use_tmp_file(tmp_path, monkeypatch)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        state = {}
        record_buy(state, "0000012345", {
            "owner_cik": "1", "owner_name": "A", "value": 150000.0,
            "date": now, "accession": "acc-1"})
        window = record_buy(state, "0000012345", {
            "owner_cik": "2", "owner_name": "B", "value": 200000.0,
            "date": now, "accession": "acc-2"})
        assert len({b["owner_cik"] for b in window}) == 2

    def test_window_prunes_old_buys(self, tmp_path, monkeypatch):
        self._use_tmp_file(tmp_path, monkeypatch)
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=10)).isoformat()

        state = {"0000012345": [
            {"owner_cik": "1", "owner_name": "A", "value": 150000.0,
             "date": old, "accession": "acc-old"},
        ]}
        window = record_buy(state, "0000012345", {
            "owner_cik": "2", "owner_name": "B", "value": 200000.0,
            "date": now.isoformat(), "accession": "acc-new"})
        assert [b["accession"] for b in window] == ["acc-new"]

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        self._use_tmp_file(tmp_path, monkeypatch)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        state = {}
        record_buy(state, "0000012345", {
            "owner_cik": "1", "owner_name": "A", "value": 150000.0,
            "date": now, "accession": "acc-1"})
        save_buys(state)
        assert os.path.exists(form4_state.BUYS_FILE)
        loaded = load_buys()
        assert loaded["0000012345"][0]["accession"] == "acc-1"


class TestForm4Briefing:
    def test_single_buy_briefing(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_buy.xml"))
        value = qualifying_buy_value(f4, 100_000)
        window = [{"owner_cik": f4.owner_cik, "owner_name": f4.owner_name,
                   "value": value, "date": "2026-07-01", "accession": "a1"}]
        briefing = build_form4_briefing(f4, value, window)
        assert "Doe Jane" in briefing.headline
        assert "$3.21" in briefing.headline
        assert briefing.significance == "Medium"
        assert briefing.sentiment == "Positive"
        assert briefing.event_types == ["Insider Buying"]
        assert briefing.deal_terms["share_count"] == "50,000"

    def test_cluster_briefing(self, fixture_text):
        f4 = parse_form4_xml(fixture_text("form4_buy.xml"))
        value = qualifying_buy_value(f4, 100_000)
        window = [
            {"owner_cik": "0000111222", "owner_name": "Smith John",
             "value": 152000.0, "date": "2026-06-30", "accession": "a0"},
            {"owner_cik": f4.owner_cik, "owner_name": f4.owner_name,
             "value": value, "date": "2026-07-01", "accession": "a1"},
        ]
        briefing = build_form4_briefing(f4, value, window)
        assert briefing.headline.startswith("Insider cluster: 2 insiders")
        assert briefing.significance == "High"
        assert "1 other insider also bought" in briefing.summary
