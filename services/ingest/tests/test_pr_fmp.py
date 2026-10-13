"""FMP press-release source: row parsing, language detection, article
extraction, and the poll loop's publish-once behaviour."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from press_release import fmp, materiality, pipeline
from press_release.article import extract_release_text
from press_release.issuer import build_ticker_index, extract_tickers
from press_release.language import is_english

@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No network in tests: article fetches fail unless a test patches them."""
    def fail(*a, **kw):
        raise OSError("offline")
    monkeypatch.setattr("press_release.feeds.fetch_pr_url", fail)
    monkeypatch.setattr("press_release.feeds._host_failures", {})
    monkeypatch.setattr("press_release.feeds._host_skip_until", {})


_EN_BODY = (
    "HOUSTON, Oct. 10, 2026 /PRNewswire/ -- To help support customers and "
    "communities impacted by Hurricane Isaias along the Northwest Florida Coast, "
    "CenterPoint Energy crews are heading out to play an important role in helping "
    "neighboring communities recover by restoring outages. Through a mutual "
    "assistance effort supporting Florida Power & Light, CenterPoint has deployed "
    "105 line workers and released approximately 150 contractor line workers and "
    "100 vegetation management personnel to assist with restoration efforts. "
) * 3


def _row(**kw):
    base = {
        "symbol": "CNP",
        "publishedDate": "2026-10-10 06:00:00",
        "publisher": "PRNewsWire",
        "title": "CenterPoint Energy Sending 355 Mutual Aid Workers",
        "text": _EN_BODY,
        "url": "https://www.prnewswire.com/news-releases/centerpoint-302905021.html",
        "site": "prnewswire.com",
        "image": "",
    }
    base.update(kw)
    return base


# ── row parsing ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("row,wire", [
    ({"site": "prnewswire.com"}, "prnewswire"),
    ({"site": "", "url": "https://www.globenewswire.com/news-release/x.html"}, "globenewswire"),
    ({"site": "", "url": "", "publisher": "Business Wire"}, "businesswire"),
    ({"site": "accessnewswire.com"}, "accesswire"),
    ({"site": "", "url": "https://www.accesswire.com/x"}, "accesswire"),
    ({"site": "newsfilecorp.com", "url": "https://newsfilecorp.com/x", "publisher": "Newsfile"}, ""),
])
def test_wire_mapping(row, wire):
    assert fmp.wire_for(row) == wire


def test_rows_fold_by_url_and_drop_other_wires():
    url = "https://www.globenewswire.com/news-release/2026/10/09/3378420/0/en/bip.html"
    rows = [
        _row(),
        _row(symbol="BIPC", url=url, site="globenewswire.com", title="Brookfield Infrastructure"),
        _row(symbol="BIP", url=url, site="globenewswire.com", title="Brookfield Infrastructure"),
        _row(symbol="XYZ", url="https://newsfilecorp.com/x", site="newsfilecorp.com", publisher="Newsfile"),
        _row(url="", title="no url"),
    ]
    releases, other = fmp.parse_fmp_rows(rows)
    assert other == 1
    assert [r.wire for r in releases] == ["prnewswire", "globenewswire"]
    assert releases[1].metadata_tickers == ["BIPC", "BIP"]
    assert releases[0].guid == releases[0].url
    assert releases[0].issuer_name == ""


def test_published_date_converted_from_eastern(monkeypatch):
    monkeypatch.delenv("PR_FMP_TIMEZONE", raising=False)
    (release,), _ = fmp.parse_fmp_rows([_row(publishedDate="2026-10-10 06:00:00")])
    assert release.published == "2026-10-10T10:00:00+00:00"   # EDT = UTC-4
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    (release,), _ = fmp.parse_fmp_rows([_row(publishedDate="2026-10-10 06:00:00")])
    assert release.published == "2026-10-10T06:00:00+00:00"


def test_parse_tolerates_garbage():
    assert fmp.parse_fmp_rows({"Error Message": "x"}) == ([], 0)
    assert fmp.parse_fmp_rows([None, "x", {}]) == ([], 0)


def test_share_class_symbols_pass_shape_check():
    assert extract_tickers("", "", ["BRK-B"]) == ["BRK-B"]
    assert extract_tickers("", "", ["9973.HK", "GTII.CN", "US1234567890"]) == []


# ── language ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    # From the live PR Newswire / GlobeNewswire feeds, Oct 10 2026
    "Übernahmeangebot für Nagarro erfolgreich abgeschlossen Bei Ablauf der zusätzlichen "
    "Annahmefrist waren rund 94,04 % des Grundkapitals von Nagarro gesichert, womit die "
    "Mindestannahmeschwelle von 50 % zuzüglich einer Aktie deutlich überschritten wurde "
    "Nachdem Persistent mehr als 90 % des",
    "SINGAPOUR, 10 octobre 2026 /PRNewswire/ -- ONLYOFFICE présente sa mise à jour 10.0 "
    "majeure d'automne, qui inclut des outils d'IA contextuels directement intégrés aux "
    "éditeurs, de nouvelles applications modulaires et une importante mise à niveau de la sécurité.",
    "SINGAPUR, 10 de octubre de 2026 /PRNewswire/ -- ONLYOFFICE 10.0 se ha lanzado "
    "oficialmente. La gran actualización de otoño incorpora herramientas de IA sensible al "
    "contexto directamente en los editores, nuevas aplicaciones modulares y una importante mejora",
    "VU HỒ, Trung Quốc, Oct. 10, 2026 (GLOBE NEWSWIRE) -- Dòng xe iCAUR V sắp chào đón một "
    "thành viên mới. iCAUR V25, mẫu SUV đa năng (All-Way SUV), sẽ chính thức ra mắt vào ngày "
    "19 tháng 10 tại Hội nghị Thượng đỉnh Người dùng Quốc tế iCAUR 2026 diễn ra ở Vũ Hồ",
    "华为今日宣布推出新一代智能手机，搭载自研芯片，并将于下月在全球市场上市销售。公司表示，新产品将进一步巩固其市场地位。",
])
def test_non_english_detected(text):
    assert not is_english(text)


@pytest.mark.parametrize("text", [
    _EN_BODY,
    "Integra LifeSciences Holdings Corporation (NASDAQ:IART) (the “Company”) today "
    "announced the pricing of $450,000,000 aggregate principal amount of 9.500% senior "
    "secured notes due 2033 (the “Notes”). The Notes will be general senior secured "
    "obligations of the Company and will be guaranteed by the Company’s subsidiaries.",
    "Short teaser",            # too little text to judge → benefit of the doubt
])
def test_english_passes(text):
    assert is_english(text)


# ── article extraction ────────────────────────────────────────────────────

def test_extract_release_text_skips_page_chrome():
    page = (
        "<html><body><nav>News Releases | Products | Contact Us | Login</nav>"
        "<h1>CenterPoint Energy Sending 355 Mutual Aid Workers</h1>"
        "<div>NEWS PROVIDED BY <a href='/news/centerpoint-energy/'>CenterPoint Energy</a></div>"
        "<p>HOUSTON, Oct. 10, 2026 /PRNewswire/ -- To help support customers (NYSE: CNP).</p>"
        "<p>More release text.</p>"
        "<p>SOURCE CenterPoint Energy</p>"
        "<div>Related news: something else entirely</div></body></html>"
    )
    text = extract_release_text(page)
    assert text.startswith("HOUSTON, Oct. 10, 2026 /PRNewswire/")
    assert "More release text." in text
    assert "SOURCE" not in text and "Related news" not in text and "Login" not in text


def test_extract_release_text_without_dateline_is_empty():
    assert extract_release_text("<p>Just a page</p>") == ""


# ── process_release ───────────────────────────────────────────────────────

_INDEX = build_ticker_index({"0001130310": {"ticker": "CNP", "name": "CENTERPOINT ENERGY INC"}})


def _llm(**kw):
    base = {
        "issued_by_company": True,
        "summary": "CenterPoint sent crews to Florida.",
        "primary_event_type": "Other", "event_types": ["Other"],
        "significance": "Low", "sentiment": "Neutral",
        "investor_takeaway": "", "catalysts": [],
    }
    base.update(kw)
    return base


def _release(**kw):
    (release,), _ = fmp.parse_fmp_rows([_row(**kw)])
    return release


def test_wire_headline_kept_and_routine_release_publishes():
    counters = pipeline._new_counters()
    with patch.object(materiality, "_chat_json", return_value=_llm()):
        event = pipeline.process_release(_release(), _INDEX, [], counters)
    assert event is not None
    assert event.briefing.headline == "CenterPoint Energy Sending 355 Mutual Aid Workers"
    assert event.ticker == "CNP"
    assert event.source == "prnewswire"
    assert event.signal_type == "PR"
    assert event.max_tier == 3          # Low significance → routine


def test_untagged_translation_dropped_before_llm():
    german = ("Übernahmeangebot für Nagarro erfolgreich abgeschlossen Bei Ablauf der "
              "zusätzlichen Annahmefrist waren rund 94 % des Grundkapitals von Nagarro "
              "gesichert, womit die Mindestannahmeschwelle deutlich überschritten wurde. ") * 5
    counters = pipeline._new_counters()
    with patch.object(materiality, "_chat_json") as chat:
        assert pipeline.process_release(
            _release(text=german, title="Persistent sichert sich eine starke Mehrheitsbeteiligung"),
            _INDEX, [], counters) is None
    assert counters["non_english"] == 1
    chat.assert_not_called()


def test_law_firm_release_tagged_with_target_ticker_dropped():
    body = ("NEW YORK, Oct. 10, 2026 /PRNewswire/ -- Why: Rosen Law Firm, a global investor "
            "rights law firm, reminds purchasers of securities of CenterPoint Energy (NYSE: CNP) "
            "of the important lead plaintiff deadline. ") * 4
    counters = pipeline._new_counters()
    with patch.object(materiality, "_chat_json") as chat:
        assert pipeline.process_release(
            _release(text=body, title="CNP Investors Have Opportunity to Lead Securities Fraud Lawsuit"),
            _INDEX, [], counters) is None
    assert counters["blocklisted"] == 1
    chat.assert_not_called()


def test_symbol_not_a_us_registrant_dropped():
    counters = pipeline._new_counters()
    with patch.object(materiality, "_chat_json") as chat:
        assert pipeline.process_release(_release(symbol="ZZZZ"), _INDEX, [], counters) is None
    assert counters["issuer_fail"] == 1
    chat.assert_not_called()


# ── poll loop ─────────────────────────────────────────────────────────────

def _run_loop(monkeypatch, tmp_path, rows, process, seconds=0.6):
    monkeypatch.setattr(pipeline, "PR_SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(pipeline, "FINGERPRINT_FILE", str(tmp_path / "fp.json"))
    monkeypatch.setenv("PR_FMP_POLL_SECONDS", "0.25")
    published = []
    monkeypatch.setattr(pipeline.fmp, "fetch_latest", rows if callable(rows) else lambda: rows)
    monkeypatch.setattr(pipeline, "process_release", process)
    monkeypatch.setattr(pipeline, "publish_filing", published.append)

    async def go():
        try:
            await asyncio.wait_for(pipeline.fmp_poll_loop(lambda: _INDEX), seconds)
        except asyncio.TimeoutError:
            pass
    asyncio.run(go())
    return published


def _fresh(minutes_ago=1):
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def test_loop_publishes_each_release_once_across_polls(monkeypatch, tmp_path):
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    calls = []

    def process(release, index, fp, counters):
        calls.append(release.url)
        from events import FilingEvent, FilingEventBriefing
        return FilingEvent(
            edgar_id=f"pr:{release.wire}:{release.guid}", signal_type="PR", cik="1",
            ticker="CNP", company_name="CenterPoint", filing_date=release.published,
            edgar_url=release.url, accession_number="", max_tier=2, source=release.wire,
            briefing=FilingEventBriefing(release.headline, "", "Other", "Low", "Neutral", ""),
        )

    rows = [_row(publishedDate=_fresh()), _row(url="https://www.prnewswire.com/b.html", publishedDate=_fresh())]
    published = _run_loop(monkeypatch, tmp_path, rows, process)
    assert len(calls) == 2              # several polls, each release processed once
    assert len(published) == 2


def test_loop_skips_stale_releases(monkeypatch, tmp_path):
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    calls = []
    published = _run_loop(monkeypatch, tmp_path, [_row(publishedDate=_fresh(minutes_ago=600))],
                          lambda r, *a: calls.append(r) or None)
    assert calls == [] and published == []


def _event_for(release):
    from events import FilingEvent, FilingEventBriefing
    return FilingEvent(
        edgar_id=f"pr:{release.wire}:{release.guid}", signal_type="PR", cik="1",
        ticker="CNP", company_name="C", filing_date=release.published,
        edgar_url=release.url, accession_number="", max_tier=2, source=release.wire,
        briefing=FilingEventBriefing(release.headline, "", "Other", "Low", "Neutral", ""),
    )


def test_loop_publishes_a_release_fmp_lists_late(monkeypatch, tmp_path):
    """After the first poll, a row new to FMP's list is news even when its
    publishedDate is past PR_MAX_AGE_MINUTES — FMP lists some releases
    long after the wire time."""
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    first = _row(publishedDate=_fresh())
    late = _row(url="https://www.prnewswire.com/late.html", publishedDate=_fresh(minutes_ago=45))
    polls = {"n": 0}

    def rows():
        polls["n"] += 1
        return [first] if polls["n"] == 1 else [first, late]

    calls = []
    published = _run_loop(monkeypatch, tmp_path, rows,
                          lambda r, *a: calls.append(r.url) or _event_for(r))
    assert calls == [first["url"], late["url"]]
    assert len(published) == 2


def test_loop_baseline_still_skips_backlog_on_start(monkeypatch, tmp_path):
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    calls = []
    _run_loop(monkeypatch, tmp_path, [_row(publishedDate=_fresh(minutes_ago=45))],
              lambda r, *a: calls.append(r) or None)
    assert calls == []


class _RateLimited(Exception):
    status_code = 429


def test_loop_rate_limit_is_retried_not_given_up(monkeypatch, tmp_path):
    """A 429 waits its turn; it never counts towards the attempt limit."""
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    monkeypatch.setattr(pipeline, "_RATE_LIMIT_PAUSE_SECONDS", 0.01)
    calls = []

    def process(release, *a):
        calls.append(release.url)
        if len(calls) <= pipeline._MAX_LLM_ATTEMPTS + 1:
            raise _RateLimited("429")
        return _event_for(release)

    published = _run_loop(monkeypatch, tmp_path, [_row(publishedDate=_fresh())], process,
                          seconds=2.0)
    assert len(calls) == pipeline._MAX_LLM_ATTEMPTS + 2
    assert len(published) == 1


def test_loop_other_errors_still_give_up(monkeypatch, tmp_path):
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    monkeypatch.setattr(pipeline, "_MAX_LLM_ATTEMPTS", 1)
    calls = []

    def process(release, *a):
        calls.append(release.url)
        raise RuntimeError("boom")

    assert _run_loop(monkeypatch, tmp_path, [_row(publishedDate=_fresh())], process) == []
    assert len(calls) == 1


def test_loop_logs_every_row_it_fetches(monkeypatch, tmp_path, caplog):
    """Law-firm spam and other wires included: each row FMP lists gets a
    "PR fetched" line and an outcome line."""
    import logging
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    law = _row(url="https://www.prnewswire.com/law.html", publishedDate=_fresh(),
               title="CNP Investors Have Opportunity to Lead Securities Fraud Lawsuit",
               text="Rosen Law Firm reminds purchasers of CenterPoint Energy (NYSE: CNP) "
                    "of the lead plaintiff deadline.")
    newsfile = _row(url="https://www.newsfilecorp.com/x", publishedDate=_fresh(),
                    publisher="Newsfile Corp", site="newsfilecorp.com",
                    title="Kaplan Fox Reminds Innventure Investors")
    real = pipeline.process_release
    with caplog.at_level(logging.INFO, logger="press_release.pipeline"):
        _run_loop(monkeypatch, tmp_path, [newsfile, law], real)
    lines = [r.getMessage() for r in caplog.records]
    fetched = [m for m in lines if m.startswith("PR fetched")]
    assert len(fetched) == 2                     # once each, across several polls
    assert any("Kaplan Fox" in m and "isn't an ingested wire" in m for m in fetched)
    assert any(m.startswith("PR drop") and "Securities Fraud" in m and "blocklist" in m
               for m in lines)


def test_cut_off_teaser_is_retried_not_dropped_when_the_article_fails():
    """Oct 13 2026: ISG's Business Wire release, tagged III, whose teaser
    ends before naming the company and whose article page didn't load."""
    index = build_ticker_index({"0001371489": {"ticker": "III",
                                              "name": "Information Services Group Inc."}})
    release = _release(
        symbol="III", publisher="Business Wire", site="businesswire.com",
        url="https://www.businesswire.com/news/home/20261013/en/isg",
        title="European Automotive, Mobility Firms Expand AI in Operations",
        text="LONDON--(BUSINESS WIRE)--Automotive and mobility enterprises across Europe "
             "are moving AI from experimental use cases into operations while modernizing "
             "engineering and manufacturing, according to a new research report.")
    counters = pipeline._new_counters()
    with patch.object(materiality, "_chat_json") as chat, \
            pytest.raises(pipeline.ArticleUnavailable, match="offline"):
        pipeline.process_release(release, index, [], counters)
    chat.assert_not_called()
    assert counters["issuer_fail"] == 0


def test_cut_off_teaser_passes_once_the_article_loads():
    index = build_ticker_index({"0001371489": {"ticker": "III",
                                              "name": "Information Services Group Inc."}})
    page = ("<html><body><p>LONDON--(BUSINESS WIRE)--Automotive and mobility enterprises "
            "across Europe are moving AI from experimental use cases into operations, "
            "according to a new research report published today by Information Services "
            "Group (ISG) (Nasdaq: III), a global AI-centered technology research and "
            "advisory firm. The report finds enterprises are integrating digital "
            "capabilities across the vehicle lifecycle.</p></body></html>").encode()
    release = _release(
        symbol="III", publisher="Business Wire", site="businesswire.com",
        url="https://www.businesswire.com/news/home/20261013/en/isg",
        title="European Automotive, Mobility Firms Expand AI in Operations",
        text="LONDON--(BUSINESS WIRE)--Automotive and mobility enterprises across Europe.")
    counters = pipeline._new_counters()
    with patch("press_release.feeds.fetch_pr_url", return_value=page) as fetch, \
            patch.object(materiality, "_chat_json", return_value=_llm()):
        event = pipeline.process_release(release, index, [], counters)
    assert event is not None and event.ticker == "III"
    assert fetch.call_args.kwargs["html"] is True


def test_release_without_a_wire_dateline_is_found_by_its_headline():
    """Oct 13 2026: Tower Semiconductor's GlobeNewswire page prints no
    "(GLOBE NEWSWIRE)" marker; the article text was discarded."""
    headline = "Tower Semiconductor Announces Third Quarter 2026 Financial Results and Conference Call"
    body = ("MIGDAL HAEMEK, Israel – October 13, 2026 – Tower Semiconductor (NASDAQ/TASE: TSEM), "
            "the leading foundry of high-value analog semiconductor solutions, will issue its "
            "third quarter 2026 earnings release on Monday, November 2, 2026. ") * 3
    page = (f"<html><head><title>{headline}</title></head><body><nav>Newsroom</nav>"
            f"<div>Share {headline}</div><h1>{headline}</h1><p>{body}</p>"
            "<p>Tags: semiconductors</p><footer>Related news</footer></body></html>")
    text = extract_release_text(page, headline)
    assert text.startswith("MIGDAL HAEMEK")
    assert "Related news" not in text
    assert extract_release_text(page) == ""          # no headline, no anchor
    assert extract_tickers(headline, text) == ["TSEM"]


def test_lag_not_reported_for_rows_listed_before_start(monkeypatch, tmp_path, caplog):
    import logging
    monkeypatch.setenv("PR_FMP_TIMEZONE", "UTC")
    with caplog.at_level(logging.INFO, logger="press_release.pipeline"):
        _run_loop(monkeypatch, tmp_path, [_row(publishedDate=_fresh(minutes_ago=20))],
                  lambda r, *a: _event_for(r))
    published = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Published PR")]
    assert len(published) == 1
    assert "wire→fmp=n/a(listed before start)" in published[0]


def test_wells_fargo_keeps_its_common_ticker(monkeypatch):
    """Oct 13 2026: SEC lists WFC first, then its preferreds; keeping the
    last entry labelled Wells Fargo WFC-PA and lost WFC from the index."""
    import json
    import fetcher
    raw = {"0": {"cik_str": 72971, "ticker": "WFC", "title": "WELLS FARGO & COMPANY/MN"},
           "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
           "2": {"cik_str": 72971, "ticker": "WFC-PY", "title": "WELLS FARGO & COMPANY/MN"},
           "3": {"cik_str": 72971, "ticker": "WFC-PA", "title": "WELLS FARGO & COMPANY/MN"}}
    monkeypatch.setattr(fetcher, "fetch_url", lambda url: json.dumps(raw).encode())
    cik_map = fetcher.load_ticker_map()
    assert cik_map["0000072971"]["ticker"] == "WFC"
    index = build_ticker_index(cik_map)
    assert index["WFC"]["cik"] == index["WFC-PA"]["cik"] == "0000072971"
    assert index["AAPL"]["cik"] == "0000320193"


def test_failing_wire_host_is_skipped_after_repeated_failures(monkeypatch):
    from press_release import feeds
    calls = []

    def timeout(*a, **kw):
        calls.append(a[0])
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(feeds, "fetch_pr_url", timeout)
    for i in range(feeds._HOST_FAILURE_LIMIT + 2):
        r = _release(url=f"https://www.businesswire.com/news/home/{i}/en/x",
                     publisher="Business Wire", site="businesswire.com")
        feeds.fetch_release_body(r)
    assert len(calls) == feeds._HOST_FAILURE_LIMIT     # then skipped
    assert "skipped" in r.fetch_error


def test_telegraphic_headline_and_teaser_are_english():
    """Oct 13 2026: BIO-key's headline + one-line teaser scored as foreign."""
    assert is_english(
        "BIO-key Adds New Middle East Government Security Agency Customer, Expanding "
        "Regional Footprint\nBIO-key (BKYI) Adds New Middle East Government Security "
        "Agency, Expands Regional Footprint, Supports National Digital Transformation "
        "& Cybersecurity goals.")


def test_short_translated_teaser_still_not_english():
    assert not is_english(
        "Persistent sichert sich eine starke Mehrheitsbeteiligung\nBei Ablauf der "
        "zusätzlichen Annahmefrist waren rund 94 % des Grundkapitals von Nagarro "
        "gesichert, womit die Mindestannahmeschwelle deutlich überschritten wurde.")
    assert not is_english(
        "Nagarro: l'offre publique d'achat est un succès\nÀ l'expiration de la "
        "période d'acceptation, environ 94 % du capital de Nagarro sont sécurisés "
        "pour Persistent, qui dépasse le seuil minimal.")


def test_short_teaser_reaches_the_llm_when_the_article_fetch_fails():
    """Business Wire / GlobeNewswire teasers are often one sentence; with
    the headline that is still a briefing, not a body_too_short drop."""
    counters = pipeline._new_counters()
    release = _release(
        symbol="CNP",
        title="CenterPoint Energy Reports Q3 2026 Deliveries of 1.3 Million Units, Down 1%",
        text="Q3 2026 consolidated deliveries 1.3 million units, down 1% y-o-y. "
             "Growth in Texas was offset by lower volumes in Ohio following a planned shutdown.",
    )
    with patch.object(materiality, "_chat_json", return_value=_llm()) as chat:
        assert pipeline.process_release(release, _INDEX, [], counters) is not None
    chat.assert_called_once()
    assert counters["teaser_only"] == 1


# ── the live FMP sample (Oct 10 2026) ─────────────────────────────────────

_SAMPLE_INDEX = build_ticker_index({
    f"{i:010d}": {"ticker": t, "name": n} for i, (t, n) in enumerate([
        ("QFIN", "Qfin Holdings, Inc."), ("FRVO", "Fervo Energy Co"),
        ("EV", "EATON VANCE CORP"),             # what FMP's "EV" tag resolves to
        ("GOOG", "Alphabet Inc."), ("ARDX", "ARDELYX, INC."),
        ("DNB", "Dun & Bradstreet Holdings, Inc."), ("CELH", "Celsius Holdings, Inc."),
        ("AVEX", "AEVEX Corp"), ("UWMC", "UWM Holdings Corp"),
        ("XTIA", "XTI Aerospace, Inc."), ("GDDY", "GoDaddy Inc."),
        ("PZZA", "PAPA JOHNS INTERNATIONAL INC"), ("HDB", "HDFC BANK LTD"),
        ("FTK", "FLOTEK INDUSTRIES INC/CN/"), ("CNP", "CENTERPOINT ENERGY INC"),
        ("MRP", "Millrose Properties, Inc."), ("LINC", "LINCOLN EDUCATIONAL SERVICES CORP"),
    ], start=1)
})


def _sample_releases(fixture_text):
    import json
    return fmp.parse_fmp_rows(json.loads(fixture_text("pr/fmp_latest.json")))


def test_sample_drops_newsfile_rows(fixture_text):
    releases, other = _sample_releases(fixture_text)
    assert other == 10                                   # Newsfile Corp rows
    assert {r.wire for r in releases} == {"prnewswire", "businesswire"}
    assert all(r.body_is_teaser for r in releases)


def test_sample_only_the_company_release_reaches_the_llm(fixture_text):
    releases, _ = _sample_releases(fixture_text)
    counters = pipeline._new_counters()
    reached = []

    def fake_llm(messages):
        reached.append(messages[1]["content"].split("\n", 2)[1])   # "Ticker: X"
        return _llm()

    retried = []
    with patch.object(materiality, "_chat_json", side_effect=fake_llm):
        for r in releases:
            try:
                pipeline.process_release(r, _SAMPLE_INDEX, [], counters)
            except pipeline.ArticleUnavailable:
                retried.append(r.metadata_tickers)

    assert reached == ["Ticker: CNP"]
    assert counters["blocklisted"] == 8                  # Rosen ×7 + Robbins LLP
    # AHF's clinic tagged "EV": its teaser can't verify the tag and the
    # article is offline here, so it waits for the article instead of the LLM
    assert retried == [["EV"]]


def test_article_fetched_for_teasers_and_prn_issuer_credit_used():
    page = (
        "<html><nav>Menu</nav><div>News provided by</div>"
        "<div><a href='/news/the-rosen-law-firm/'>THE ROSEN LAW FIRM, P. A. →</a></div>"
        "<p>HOUSTON, Oct. 10, 2026 /PRNewswire/ -- " + _EN_BODY + "</p>"
        "<p>SOURCE The Rosen Law Firm</p></html>"
    ).encode()
    release = _release(text=_EN_BODY[:300])
    counters = pipeline._new_counters()
    with patch("press_release.feeds.fetch_pr_url", return_value=page) as fetch, \
         patch.object(materiality, "_chat_json") as chat:
        assert pipeline.process_release(release, _INDEX, [], counters) is None
    fetch.assert_called_once()
    assert release.issuer_name == "THE ROSEN LAW FIRM, P. A."
    assert counters["issuer_fail"] == 1                  # credit ≠ CenterPoint
    chat.assert_not_called()


def test_prn_issuer_credit_matching_the_company_verifies_it():
    page = (
        "<div>News provided by</div><div><a>CenterPoint Energy →</a></div>"
        "<p>HOUSTON, Oct. 10, 2026 /PRNewswire/ -- " + _EN_BODY + "</p>"
    ).encode()
    counters = pipeline._new_counters()
    with patch("press_release.feeds.fetch_pr_url", return_value=page), \
         patch.object(materiality, "_chat_json", return_value=_llm()):
        event = pipeline.process_release(_release(text=_EN_BODY[:300]), _INDEX, [], counters)
    assert event is not None and event.issuer_name == "CenterPoint Energy"
    assert counters["issuer_unverified"] == 0            # verified by the credit
