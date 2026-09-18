"""Fundamentals pipeline + public endpoints.

Fixtures under tests/fixtures/fmp/ are FMP /stable-shaped records for
AAPL (Sept fiscal year) built from the reported 10-K figures, so the
statement-level assertions below check against the filings.
"""

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.models.company import Company
from app.models.fundamentals import CompanyFundamentals, FundamentalsPeriod
from app.services.fundamentals import analysis, derive, mapper, rows as R
from app.services.fundamentals.edgar_docs import parse_submissions
from app.services.fundamentals.fmp_client import FMPClient, FMPError
from app.services.fundamentals.sync import rebuild_snapshot, run_sync, sync_company

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures', 'fmp')
M = 1_000_000
TODAY = date(2026, 9, 18)


def _load(name):
    with open(os.path.join(FIXTURES, name)) as fh:
        return json.load(fh)


class FakeFMP(FMPClient):
    """Serves the recorded fixtures; no network."""

    def __init__(self, missing: set[str] | None = None, fail: set[str] | None = None):
        super().__init__(api_key='test', calls_per_minute=100000)
        self.missing = missing or set()
        self.fail = fail or set()
        self.income = _load('aapl_income.json')
        self.balance = _load('aapl_balance.json')
        self.cashflow = _load('aapl_cashflow.json')
        self.prof = _load('aapl_profile.json')
        self.divs = _load('aapl_dividends.json')
        self.eod = _load('aapl_eod.json')

    def _check(self, name):
        self.calls += 1
        if name in self.fail:
            raise FMPError(f'{name} failed')
        return name not in self.missing

    def profile(self, symbol):
        return self.prof[0] if self._check('profile') else None

    def income_statements(self, symbol, period, limit=None):
        return self.income[period] if self._check('income') else []

    def balance_sheets(self, symbol, period, limit=None):
        return self.balance[period] if self._check('balance') else []

    def cash_flows(self, symbol, period, limit=None):
        return self.cashflow[period] if self._check('cashflow') else []

    def dividends(self, symbol, limit=40):
        return self.divs if self._check('dividends') else []

    def eod_light(self, symbol, years=10):
        return self.eod if self._check('eod') else []

    def earnings_calendar(self, start, end):
        return [{'symbol': 'AAPL', 'date': '2026-09-15'}] if self._check('calendar') else []


@pytest.fixture
def priced_company(db_session):
    c = Company(name='Apple Inc.', ticker='AAPL', cik='0000320193', sic='3571',
                last_price=Decimal('240.50'), shares_outstanding=14_800_000_000,
                market_cap=int(240.50 * 14_800_000_000),
                price_updated_at=datetime(2026, 9, 18, tzinfo=timezone.utc))
    db_session.session.add(c)
    db_session.session.commit()
    return c


# ── mapper ─────────────────────────────────────────────────────────────

class TestMapper:
    def test_merges_three_statements_on_period_end(self):
        fmp = FakeFMP()
        periods = mapper.merge_statements(fmp.income['annual'], fmp.balance['annual'],
                                          fmp.cashflow['annual'], 'annual')
        assert len(periods) == 6
        fy24 = next(p for p in periods if p['period_end'] == date(2024, 9, 28))
        assert fy24['fiscal_year'] == 2024
        assert fy24['fiscal_period'] == 'FY'
        assert fy24['revenue'] == 391_035 * M          # 10-K FY2024
        assert fy24['net_income'] == 93_736 * M
        assert fy24['total_assets'] == 364_980 * M
        assert fy24['cfo'] == 118_254 * M
        assert fy24['eps_diluted'] == Decimal('6.08')
        assert fy24['filing_date'] == date(2024, 11, 1)
        assert set(fy24['raw']) == {'income', 'balance', 'cashflow'}

    def test_reads_legacy_v3_field_names(self):
        legacy_income = [{
            'date': '2023-12-31', 'calendarYear': '2023', 'period': 'FY', 'fillingDate': '2024-02-20',
            'revenue': 1000, 'ebitda': 300, 'depreciationAndAmortization': 50, 'operatingIncome': 250,
            'interestExpense': 10, 'totalOtherIncomeExpensesNet': -10, 'incomeBeforeTax': 240,
            'incomeTaxExpense': 40, 'netIncome': 200, 'eps': 2.0, 'epsdiluted': 1.9,
        }]
        legacy_cf = [{'date': '2023-12-31', 'netCashUsedForInvestingActivites': -70,
                      'netCashUsedProvidedByFinancingActivities': -20, 'dividendsPaid': -15,
                      'netCashProvidedByOperatingActivities': 260, 'capitalExpenditure': -60}]
        legacy_bs = [{'date': '2023-12-31', 'totalAssets': 900, 'totalLiabilities': 400,
                      'totalStockholdersEquity': 500, 'othertotalStockholdersEquity': 0}]
        (p,) = mapper.merge_statements(legacy_income, legacy_bs, legacy_cf, 'annual')
        assert p['fiscal_year'] == 2023
        assert p['filing_date'] == date(2024, 2, 20)
        assert p['eps_diluted'] == Decimal('1.9')
        assert p['cfi'] == -70 and p['cff'] == -20 and p['dividends_paid'] == -15
        assert p['free_cash_flow'] == 200  # filled from cfo + capex
        assert p['quality_flags'] == []

    def test_missing_statement_flagged_not_dropped(self):
        fmp = FakeFMP()
        periods = mapper.merge_statements(fmp.income['annual'][:2], [], [], 'annual')
        assert len(periods) == 2
        assert mapper.FLAG_MISSING_STATEMENT in periods[0]['quality_flags']
        assert periods[0]['revenue'] is not None

    def test_pbt_reconciliation_flag(self):
        row = {'revenue': 1000, 'ebitda': 300, 'depreciation_amortization': 50,
               'interest_expense': 10, 'other_income_net': -10, 'pretax_income': 100,
               'total_assets': 10, 'total_liabilities': 5, 'total_equity': 5}
        assert mapper.FLAG_PBT_RECONCILE in mapper.reconcile(row)
        row['pretax_income'] = 240
        assert mapper.FLAG_PBT_RECONCILE not in mapper.reconcile(row)

    def test_duplicate_period_keeps_latest_filing(self):
        a = {'date': '2023-12-31', 'filingDate': '2024-02-01', 'revenue': 1}
        b = {'date': '2023-12-31', 'filingDate': '2024-06-01', 'revenue': 2}
        (p,) = mapper.merge_statements([a, b], [], [], 'annual')
        assert p['revenue'] == 2
        (p,) = mapper.merge_statements([b, a], [], [], 'annual')
        assert p['revenue'] == 2

    def test_profile_mapping_and_operating_check(self):
        prof = _load('aapl_profile.json')[0]
        mapped = mapper.map_profile(prof)
        assert mapped['industry'] == 'Consumer Electronics'
        assert mapped['employees'] == 164000
        assert mapped['ipo_date'] == date(1980, 12, 12)
        assert mapped['is_adr'] is False
        assert mapper.profile_is_operating_company(prof)
        assert not mapper.profile_is_operating_company({**prof, 'isEtf': True})
        assert not mapper.profile_is_operating_company(None)


# ── rows ───────────────────────────────────────────────────────────────

class TestRows:
    def _fy24(self):
        fmp = FakeFMP()
        periods = mapper.merge_statements(fmp.income['annual'], fmp.balance['annual'],
                                          fmp.cashflow['annual'], 'annual')
        return next(p for p in periods if p['period_end'] == date(2024, 9, 28))

    def test_income_rows_identity(self):
        p = self._fy24()
        r = R.income_rows(p)
        assert r['sales'] == 391_035 * M
        assert r['operating_profit'] == (123_216 + 11_445) * M          # EBITDA
        assert r['expenses'] == r['sales'] - r['operating_profit']
        assert r['opm_pct'] == pytest.approx(34.44, abs=0.01)
        assert r['interest'] == 0
        assert r['depreciation'] == 11_445 * M
        assert r['profit_before_tax'] == 123_485 * M
        assert r['tax_pct'] == pytest.approx(24.09, abs=0.01)
        assert r['net_profit'] == 93_736 * M
        assert r['eps'] == 6.08
        assert r['dividend_payout_pct'] == pytest.approx(16.25, abs=0.01)
        # Sales − Expenses − Dep − Interest + Other = PBT
        implied = r['sales'] - r['expenses'] - r['depreciation'] - r['interest'] + r['other_income']
        assert implied == r['profit_before_tax']

    def test_balance_rows_assets_first_and_sum(self):
        p = self._fy24()
        r = R.balance_rows(p)
        assert list(r)[:4] == ['fixed_assets', 'investments', 'other_assets', 'total_assets']
        assert r['fixed_assets'] == 45_680 * M
        assert r['investments'] == (91_479 + 35_228) * M
        assert r['fixed_assets'] + r['investments'] + r['other_assets'] == r['total_assets']
        assert r['borrowings'] == (20_879 + 85_750) * M
        assert r['borrowings'] + r['other_liabilities'] == r['total_liabilities']
        assert r['equity_capital'] + r['reserves'] == r['total_equity']
        assert r['total_liabilities'] + r['total_equity'] == r['total_assets']

    def test_cashflow_and_ratio_rows(self):
        p = self._fy24()
        c = R.cashflow_rows(p)
        assert c['cash_from_operating'] == 118_254 * M
        assert c['net_cash_flow'] == -794 * M
        assert c['free_cash_flow'] == (118_254 - 9_447) * M
        ratios = R.ratio_rows(p)
        assert ratios['debtor_days'] == pytest.approx(61.8, abs=0.1)
        assert ratios['inventory_days'] == pytest.approx(12.6, abs=0.1)
        assert ratios['days_payable'] == pytest.approx(119.7, abs=0.1)
        assert ratios['cash_conversion_cycle'] == pytest.approx(61.8 + 12.6 - 119.7, abs=0.2)
        # ROCE = (PBT + interest) / (assets − current liabilities)
        assert ratios['roce_pct'] == pytest.approx(100 * 123_485 / (364_980 - 176_392), abs=0.05)

    def test_null_safety(self):
        empty = {'period_end': date(2020, 1, 1)}
        assert all(v is None for v in R.income_rows(empty).values())
        assert all(v is None for v in R.balance_rows(empty).values())
        assert all(v is None for v in R.ratio_rows(empty).values())


# ── derive ─────────────────────────────────────────────────────────────

class TestDerive:
    def _periods(self):
        fmp = FakeFMP()
        annual = mapper.merge_statements(fmp.income['annual'], fmp.balance['annual'],
                                         fmp.cashflow['annual'], 'annual')
        quarters = mapper.merge_statements(fmp.income['quarter'], fmp.balance['quarter'],
                                           fmp.cashflow['quarter'], 'quarter')
        return (sorted(annual, key=lambda p: p['period_end'], reverse=True),
                sorted(quarters, key=lambda p: p['period_end'], reverse=True))

    def test_ttm_sums_four_quarters(self):
        _, quarters = self._periods()
        ttm = derive.ttm_period(quarters)
        assert ttm['revenue'] == (102_466 + 94_036 + 95_359 + 124_300) * M
        assert ttm['eps_diluted'] == pytest.approx(1.85 + 1.57 + 1.65 + 2.40)
        assert ttm['total_assets'] == 359_000 * M  # balance sheet from latest quarter
        assert derive.ttm_period(quarters[:3]) is None
        assert derive.ttm_period(quarters, offset=4)['revenue'] == (94_930 + 85_777 + 90_753 + 119_575) * M

    def test_ttm_rejects_gappy_quarters(self):
        _, quarters = self._periods()
        gappy = [quarters[0], quarters[2], quarters[4], quarters[6]]
        assert derive.ttm_period(gappy) is None

    def test_snapshot_ratios(self):
        annual, quarters = self._periods()
        snap = derive.build_snapshot(
            annual, quarters, price=240.5, shares_outstanding=14_800 * M, market_cap=None,
            price_refs={'1y': 200.0, '3y': 150.0, '5y': 100.0, '10y': 30.0},
            dividends_ttm_ps=1.02, high_52w=260.1, low_52w=169.21)
        r = snap['ratios']
        eps_ttm = 1.85 + 1.57 + 1.65 + 2.40
        assert r['eps_ttm'] == pytest.approx(eps_ttm)
        assert r['pe_ttm'] == pytest.approx(240.5 / eps_ttm, abs=0.01)
        assert r['market_cap'] == int(240.5 * 14_800 * M)
        assert r['book_value_ps'] == pytest.approx(69_500 * M / (14_800 * M), abs=0.01)
        assert r['dividend_yield'] == pytest.approx(100 * 1.02 / 240.5, abs=0.01)
        assert r['roce'] == pytest.approx(100 * 133_383 / (359_000 - 157_500), abs=0.05)
        assert r['roe'] == pytest.approx(100 * 111_483 / ((69_500 + 56_950) / 2), abs=0.05)
        assert r['debt_to_equity'] == pytest.approx((12_000 + 82_000) / 69_500, abs=0.01)
        assert r['high_52w'] == 260.1
        g = snap['growth']
        assert g['sales']['5y'] == pytest.approx(100 * ((416_161 / 274_515) ** 0.2 - 1), abs=0.05)
        assert g['sales']['10y'] is None                     # only 6 years of history
        assert g['sales']['ttm'] == pytest.approx(100 * (416_161 / 391_035 - 1), abs=0.05)
        assert g['price']['5y'] == pytest.approx(100 * ((240.5 / 100) ** 0.2 - 1), abs=0.05)
        assert g['price']['10y'] == pytest.approx(100 * ((240.5 / 30) ** 0.1 - 1), abs=0.05)
        assert g['roe']['last'] == r['roe']
        assert g['roe']['3y'] is not None

    def test_pe_is_null_for_losses(self):
        annual, quarters = self._periods()
        for q in quarters[:4]:
            q['eps_diluted'] = Decimal('-1')
        snap = derive.build_snapshot(annual, quarters, price=10, shares_outstanding=1000, market_cap=None)
        assert snap['ratios']['pe_ttm'] is None

    def test_price_references(self):
        refs = derive.price_references(_load('aapl_eod.json'), TODAY)
        assert refs['ref_date'] == date(2026, 9, 17)
        assert refs['1y'] and refs['10y']
        assert refs['1y'] > refs['3y'] > refs['5y'] > refs['10y']
        assert refs['high_52w'] >= refs['1y']
        assert derive.price_references([], TODAY) == {}
        short = [{'date': '2026-06-01', 'price': 1}, {'date': '2026-09-01', 'price': 2}]
        assert derive.price_references(short, TODAY)['1y'] is None

    def test_dividends_ttm(self):
        assert derive.dividends_ttm_per_share(_load('aapl_dividends.json'), TODAY) == pytest.approx(1.02)
        assert derive.dividends_ttm_per_share([], TODAY) is None


# ── analysis ───────────────────────────────────────────────────────────

class TestAnalysis:
    def test_rules_cite_numbers(self):
        fmp = FakeFMP()
        annual = sorted(mapper.merge_statements(fmp.income['annual'], fmp.balance['annual'],
                                                fmp.cashflow['annual'], 'annual'),
                        key=lambda p: p['period_end'], reverse=True)
        snap = derive.build_snapshot(annual, [], price=240.5, shares_outstanding=14_800 * M, market_cap=None)
        out = analysis.build_analysis(annual, snap)
        pros, cons = out['pros'], out['cons']
        assert any('reduced debt' in p for p in pros)
        assert any('return on equity' in p for p in pros)
        assert any('free cash flow' in p for p in pros)
        # P/B is far above 5, but ROE is high too — that is not a con
        assert not any('book value' in c for c in cons)
        assert not any('net loss' in c for c in cons)
        assert not any('dividends' in c for c in cons)  # cash flow shows dividends paid
        assert out['key_points']

    def test_expensive_low_roe_and_losses(self):
        annual = [
            {'period_end': date(2025, 12, 31), 'net_income': -50, 'total_equity': 1000, 'total_debt': 10,
             'dividends_paid': 0, 'revenue': 1000, 'receivables': 100},
            {'period_end': date(2024, 12, 31), 'net_income': -20, 'total_equity': 1050, 'total_debt': 10,
             'dividends_paid': 0, 'revenue': 1000, 'receivables': 100},
            {'period_end': date(2023, 12, 31), 'net_income': 10, 'total_equity': 1070, 'total_debt': 10,
             'dividends_paid': 0, 'revenue': 1000, 'receivables': 100},
        ]
        derived = {'ratios': {'pb': 8.0, 'roe': -5.0, 'book_value_ps': 1.0, 'interest_coverage': 1.2},
                   'growth': {'roe': {'3y': -2.0}, 'sales': {'5y': 1.0}, 'profit': {}}}
        out = analysis.build_analysis(annual, derived)
        cons = out['cons']
        assert any('book value' in c for c in cons)
        assert any('net loss' in c for c in cons)
        assert any('Interest coverage' in c for c in cons)
        assert any('poor sales growth' in c for c in cons)
        assert any('low return on equity' in c for c in cons)
        assert out['pros'] == ['Company is almost debt free.']

    def test_empty_history(self):
        out = analysis.build_analysis([], {'ratios': {}, 'growth': {}})
        assert out == {'pros': [], 'cons': [], 'key_points': []}


# ── sync ───────────────────────────────────────────────────────────────

class TestSync:
    def test_sync_company_stores_periods_and_snapshot(self, db_session, priced_company):
        result = sync_company(priced_company, FakeFMP(), today=TODAY)
        assert result == {'periods': 14, 'has_fundamentals': True, 'error': None}
        periods = FundamentalsPeriod.query.filter_by(company_id=priced_company.id).all()
        assert len(periods) == 14
        assert sum(1 for p in periods if p.period_type == 'annual') == 6
        snap = CompanyFundamentals.query.filter_by(company_id=priced_company.id).one()
        assert snap.has_fundamentals
        assert snap.industry == 'Consumer Electronics'
        assert snap.latest_annual_end == date(2025, 9, 27)
        assert snap.latest_quarter_end == date(2025, 9, 27)
        assert snap.coverage_from == 2020
        assert snap.fiscal_year_end_month == 9
        assert snap.derived['ratios']['pe_ttm'] == pytest.approx(240.5 / (1.85 + 1.57 + 1.65 + 2.40), abs=0.01)
        assert snap.pe_ttm == snap.derived['ratios']['pe_ttm']
        assert snap.derived['analysis']['pros']
        assert float(snap.dividends_ttm_ps) == pytest.approx(1.02)

    def test_sync_is_idempotent(self, db_session, priced_company):
        sync_company(priced_company, FakeFMP(), today=TODAY)
        sync_company(priced_company, FakeFMP(), today=TODAY)
        assert FundamentalsPeriod.query.filter_by(company_id=priced_company.id).count() == 14

    def test_sync_records_fmp_error(self, db_session, priced_company):
        result = sync_company(priced_company, FakeFMP(fail={'income'}), today=TODAY)
        assert result['error']
        snap = CompanyFundamentals.query.filter_by(company_id=priced_company.id).one()
        assert snap.sync_error and snap.last_synced_at
        assert not snap.has_fundamentals

    def test_optional_calls_may_fail(self, db_session, priced_company):
        result = sync_company(priced_company, FakeFMP(fail={'dividends', 'eod'}), today=TODAY)
        assert result['error'] is None
        snap = CompanyFundamentals.query.filter_by(company_id=priced_company.id).one()
        assert snap.has_fundamentals and snap.dividends_ttm_ps is None

    def test_etf_marked_without_fundamentals(self, db_session, priced_company):
        fmp = FakeFMP()
        fmp.prof[0]['isEtf'] = True
        result = sync_company(priced_company, fmp, today=TODAY)
        assert result['has_fundamentals'] is False
        assert fmp.calls == 1  # profile only; no statement calls spent

    def test_rebuild_snapshot_uses_new_price(self, db_session, priced_company):
        sync_company(priced_company, FakeFMP(), today=TODAY)
        priced_company.last_price = Decimal('120.25')
        db_session.session.commit()
        snap = rebuild_snapshot(priced_company)
        assert snap.derived['ratios']['pe_ttm'] == pytest.approx(120.25 / (1.85 + 1.57 + 1.65 + 2.40), abs=0.01)

    def test_run_sync_queues_unsynced_then_recent_filers(self, db_session, priced_company, sample_company_2):
        fmp = FakeFMP()
        result = run_sync(client=fmp, today=TODAY)
        assert result['queued'] == 2 and result['synced'] == 2
        # Second run: nothing unsynced. AAPL reported on 2026-09-15 per the
        # calendar and its stored quarters are a year old → re-fetched.
        result = run_sync(client=FakeFMP(), today=TODAY)
        assert result['queued'] == 1
        # No calendar entries and nothing stale → nothing to do
        result = run_sync(client=FakeFMP(missing={'calendar'}), today=TODAY)
        assert result['queued'] == 0

    def test_run_sync_without_key_is_noop(self, db_session, priced_company):
        with patch.dict(os.environ, {'FMP_API_KEY': ''}):
            result = run_sync(client=FMPClient(api_key=''), today=TODAY)
        assert result['skipped'] == 'not_configured'


# ── routes ─────────────────────────────────────────────────────────────

class TestRoutes:
    def test_page_payload(self, client, db_session, priced_company):
        sync_company(priced_company, FakeFMP(), today=TODAY)
        resp = client.get('/api/v1/fundamentals/aapl')
        assert resp.status_code == 200
        assert 'public' in resp.headers['Cache-Control']
        body = resp.get_json()
        assert body['status'] == 'ready'
        assert body['company']['ticker'] == 'AAPL'
        assert body['company']['industry'] == 'Consumer Electronics'
        assert body['ratios']['price'] == 240.5
        assert body['ratios']['pe_ttm'] == pytest.approx(240.5 / (1.85 + 1.57 + 1.65 + 2.40), abs=0.01)
        # quarterly: 8 quarters newest last, no payout row
        q = body['quarterly']
        assert [p['label'] for p in q['periods']][-1] == 'Sep 2025'
        assert len(q['rows']['sales']) == 8 and q['rows']['sales'][-1] == 102_466 * M
        assert 'dividend_payout_pct' not in q['rows']
        assert q['breakdown']['cost_of_revenue_pct'][-1] == pytest.approx(53.31, abs=0.01)
        # annual: 6 years + TTM column
        inc = body['annual']['income']
        assert [p['label'] for p in inc['periods']] == ['Sep 2020', 'Sep 2021', 'Sep 2022',
                                                          'Sep 2023', 'Sep 2024', 'Sep 2025', 'TTM']
        assert inc['rows']['sales'][-2] == 416_161 * M
        assert inc['rows']['sales'][-1] == 416_161 * M  # TTM == FY2025 (Q4 is the FY end)
        assert inc['rows']['dividend_payout_pct'][-2] == pytest.approx(100 * 15_400 / 111_483, abs=0.01)
        bal = body['annual']['balance']
        assert set(bal['rows']) == set(R.BALANCE_ROWS)  # jsonify sorts keys; order lives in rows.ts
        assert bal['breakdown']['borrowings']['long_term_debt'][-1] == 82_000 * M
        cf = body['annual']['cashflow']
        assert cf['rows']['cash_from_operating'][-1] == 127_200 * M
        assert body['annual']['ratios']['rows']['roce_pct'][-1] is not None
        assert body['growth']['sales']['5y'] is not None
        assert body['analysis']['pros']
        assert body['as_of']['latest_quarter_end'] == '2025-09-27'

    def test_invalid_and_unknown_symbols(self, client, db_session):
        assert client.get('/api/v1/fundamentals/not_a_ticker!').status_code == 400
        assert client.get('/api/v1/fundamentals/ZZZZ').status_code == 404

    def test_unsynced_company_returns_building_and_starts_backfill(self, client, db_session, priced_company):
        started = {}
        with patch('app.routes.fundamentals._start_backfill', side_effect=lambda c: started.setdefault('id', c.id) or True):
            resp = client.get('/api/v1/fundamentals/AAPL')
        assert resp.status_code == 202
        assert resp.get_json()['status'] == 'building'
        assert resp.headers['Cache-Control'] == 'no-store'
        assert started['id'] == priced_company.id

    def test_unsynced_without_key_is_unavailable(self, client, db_session, priced_company):
        with patch.dict(os.environ, {'FMP_API_KEY': ''}):
            resp = client.get('/api/v1/fundamentals/AAPL')
        assert resp.status_code == 200
        assert resp.get_json()['status'] == 'unavailable'

    def test_company_without_statements_is_empty(self, client, db_session, priced_company):
        fmp = FakeFMP()
        fmp.prof[0]['isFund'] = True
        sync_company(priced_company, fmp, today=TODAY)
        body = client.get('/api/v1/fundamentals/AAPL').get_json()
        assert body['status'] == 'empty'
        assert body['quarterly']['periods'] == []

    def test_documents(self, client, db_session, priced_company):
        submissions = {'filings': {'recent': {
            'form': ['10-K', '8-K', '10-Q', 'DEF 14A', '4'],
            'filingDate': ['2025-10-31', '2025-10-30', '2025-08-01', '2025-01-10', '2025-01-05'],
            'accessionNumber': ['0000320193-25-000079', '0000320193-25-000078', '0000320193-25-000057',
                                '0001308179-25-000010', '0000320193-25-000003'],
            'primaryDocument': ['aapl-20250927.htm', 'aapl-20251030.htm', 'aapl-20250628.htm',
                                'proxy.htm', 'form4.xml'],
            'primaryDocDescription': ['10-K', '8-K', '10-Q', 'DEF 14A', ''],
            'reportDate': ['2025-09-27', '2025-10-30', '2025-06-28', '', ''],
        }}}
        parsed = parse_submissions(submissions, '0000320193')
        assert parsed['annual'][0]['url'] == 'https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm'
        assert parsed['quarterly'][0]['form'] == '10-Q'
        assert parsed['proxy'][0]['form'] == 'DEF 14A'
        assert parsed['recent_8k_count'] == 1

        with patch('app.routes.fundamentals.fetch_documents', return_value=parsed):
            resp = client.get('/api/v1/fundamentals/AAPL/documents')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['filings']['annual'][0]['period'] == '2025-09-27'
        assert body['updates'] == []
        assert body['edgar_url'].endswith('CIK=0000320193&type=&dateb=&owner=include&count=40')

    def test_documents_survive_edgar_outage(self, client, db_session, priced_company):
        from app.services.fundamentals.edgar_docs import EdgarDocsError
        with patch('app.routes.fundamentals.fetch_documents', side_effect=EdgarDocsError('down')):
            resp = client.get('/api/v1/fundamentals/AAPL/documents')
        assert resp.status_code == 200
        assert resp.get_json()['filings_error'] == 'edgar_unavailable'

    def test_search_is_public_and_flags_fundamentals(self, client, db_session, priced_company, sample_company_2):
        sync_company(priced_company, FakeFMP(), today=TODAY)
        resp = client.get('/api/v1/companies/search?q=a')
        assert resp.status_code == 200
        results = resp.get_json()['results']
        assert results[0]['ticker'] == 'AAPL'   # market cap ranks it above TSLA (no cap)
        assert results[0]['has_fundamentals'] is True
        assert results[0]['industry'] == 'Consumer Electronics'
        assert next(r for r in results if r['ticker'] == 'TSLA')['has_fundamentals'] is False
