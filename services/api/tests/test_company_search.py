"""Tests for company search and typeahead."""

from app.models.company import Company


class TestCompanySearch:
    """Tests for GET /api/v1/companies/?q="""

    def test_search_by_ticker(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/?q=AAPL', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 1
        assert any(c['ticker'] == 'AAPL' for c in data['companies'])

    def test_search_by_name(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/?q=Apple', headers=auth_headers)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['total'] >= 1
        assert any('Apple' in c['name'] for c in data['companies'])

    def test_search_case_insensitive(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/?q=apple', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['total'] >= 1

    def test_search_partial_ticker(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/?q=AA', headers=auth_headers)
        assert resp.status_code == 200
        assert any(c['ticker'] == 'AAPL' for c in resp.get_json()['companies'])

    def test_search_partial_name(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/?q=App', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['total'] >= 1

    def test_search_no_results(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/?q=ZZZZZ', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['total'] == 0

    def test_search_exact_ticker_ranked_first(self, client, auth_headers, db_session):
        """Exact ticker match should appear before partial name matches."""
        db_session.session.add(Company(name='Agilent Technologies', ticker='A'))
        db_session.session.add(Company(name='Alphabet Inc.', ticker='GOOGL'))
        db_session.session.commit()

        resp = client.get('/api/v1/companies/?q=A', headers=auth_headers)
        companies = resp.get_json()['companies']
        # Exact ticker match 'A' should be first
        assert companies[0]['ticker'] == 'A'

    def test_ticker_param_backward_compat(self, client, auth_headers, sample_company):
        """Old ?ticker= param should still work."""
        resp = client.get('/api/v1/companies/?ticker=AAPL', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['total'] >= 1

    def test_no_query_returns_all(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/', headers=auth_headers)
        assert resp.status_code == 200
        assert resp.get_json()['total'] >= 1


class TestCompanyTypeahead:
    """Tests for GET /api/v1/companies/search?q="""

    def test_typeahead_basic(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/search?q=App', headers=auth_headers)
        assert resp.status_code == 200
        results = resp.get_json()['results']
        assert len(results) >= 1
        # Compact format — only id, name, ticker
        assert set(results[0].keys()) == {'id', 'name', 'ticker'}

    def test_typeahead_requires_q(self, client, auth_headers):
        resp = client.get('/api/v1/companies/search', headers=auth_headers)
        assert resp.status_code == 400

    def test_typeahead_limit(self, client, auth_headers, db_session):
        for i in range(15):
            db_session.session.add(Company(name=f'TestCorp {i}', ticker=f'TC{i}'))
        db_session.session.commit()

        resp = client.get('/api/v1/companies/search?q=TestCorp&limit=5', headers=auth_headers)
        assert len(resp.get_json()['results']) == 5

    def test_typeahead_default_limit(self, client, auth_headers, db_session):
        for i in range(15):
            db_session.session.add(Company(name=f'SearchMe {i}', ticker=f'SM{i}'))
        db_session.session.commit()

        resp = client.get('/api/v1/companies/search?q=SearchMe', headers=auth_headers)
        assert len(resp.get_json()['results']) == 10

    def test_typeahead_ticker_search(self, client, auth_headers, sample_company):
        resp = client.get('/api/v1/companies/search?q=AAPL', headers=auth_headers)
        results = resp.get_json()['results']
        assert any(r['ticker'] == 'AAPL' for r in results)
