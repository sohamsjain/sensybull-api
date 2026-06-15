"""Tests for the Benzinga logo sync service."""

from unittest.mock import Mock, patch

from app.services.logo_sync import sync_logos


def _benzinga_response(entries):
    resp = Mock()
    resp.json.return_value = {'ok': True, 'data': entries}
    resp.raise_for_status.return_value = None
    return resp


def _entry(symbol, files):
    return {'files': files, 'securities': [{'symbol': symbol}]}


class TestLogoSync:
    def test_stores_best_mark_by_priority(self, db_session, sample_watchlist, sample_company):
        entries = [_entry('AAPL', {
            'logo_dark': 'https://img/logo_dark.png',
            'mark_dark': 'https://img/mark_dark.png',
        })]
        with patch('app.services.logo_sync.requests.get',
                   return_value=_benzinga_response(entries)) as mock_get:
            updated, considered = sync_logos('test-token')

        assert (updated, considered) == (1, 1)
        assert sample_company.logo_url == 'https://img/mark_dark.png'  # mark beats logo
        params = mock_get.call_args.kwargs['params']
        assert params['token'] == 'test-token'
        assert params['search_keys'] == 'AAPL'
        assert params['search_keys_type'] == 'symbol'

    def test_handles_legacy_fields_key(self, db_session, sample_watchlist, sample_company):
        """API v2.1 may return 'fields' instead of 'files' — both must work."""
        entries = [{'fields': {'mark_dark': 'https://img/mark.png'},
                    'securities': [{'symbol': 'AAPL'}]}]
        with patch('app.services.logo_sync.requests.get',
                   return_value=_benzinga_response(entries)):
            updated, _ = sync_logos('t')
        assert updated == 1
        assert sample_company.logo_url == 'https://img/mark.png'

    def test_handles_bare_array_response(self, db_session, sample_watchlist, sample_company):
        """Some API versions return a bare JSON array instead of {data: [...]}."""
        resp = Mock()
        resp.json.return_value = [_entry('AAPL', {'mark_dark': 'https://img/mark.png'})]
        resp.raise_for_status.return_value = None
        with patch('app.services.logo_sync.requests.get', return_value=resp):
            updated, _ = sync_logos('t')
        assert updated == 1
        assert sample_company.logo_url == 'https://img/mark.png'

    def test_duplicate_entries_only_counted_once(self, db_session, sample_watchlist, sample_company):
        entries = [
            _entry('AAPL', {'mark_dark': 'https://img/v1.png'}),
            _entry('AAPL', {'mark_dark': 'https://img/v2.png'}),
        ]
        with patch('app.services.logo_sync.requests.get',
                   return_value=_benzinga_response(entries)):
            updated, _ = sync_logos('t')
        assert updated == 1
        assert sample_company.logo_url == 'https://img/v1.png'

    def test_watchlisted_only_by_default(self, db_session, sample_watchlist,
                                         sample_company, sample_company_2):
        # TSLA exists but is on no watchlist — must not be queried
        with patch('app.services.logo_sync.requests.get',
                   return_value=_benzinga_response([])) as mock_get:
            _, considered = sync_logos('t')
        assert considered == 1
        assert 'TSLA' not in mock_get.call_args.kwargs['params']['search_keys']

        with patch('app.services.logo_sync.requests.get',
                   return_value=_benzinga_response([])) as mock_get:
            _, considered = sync_logos('t', only_watchlisted=False)
        assert considered == 2

    def test_no_fields_leaves_company_untouched(self, db_session, sample_watchlist, sample_company):
        with patch('app.services.logo_sync.requests.get',
                   return_value=_benzinga_response([_entry('AAPL', {})])):
            updated, _ = sync_logos('t')
        assert updated == 0
        assert sample_company.logo_url is None

    def test_request_failure_skips_batch(self, db_session, sample_watchlist, sample_company):
        import requests as requests_lib
        with patch('app.services.logo_sync.requests.get',
                   side_effect=requests_lib.ConnectionError('down')):
            updated, considered = sync_logos('t')
        assert (updated, considered) == (0, 1)

    def test_unchanged_url_not_counted(self, db_session, sample_watchlist, sample_company):
        sample_company.logo_url = 'https://img/mark_dark.png'
        db_session.session.commit()
        entries = [_entry('AAPL', {'mark_dark': 'https://img/mark_dark.png'})]
        with patch('app.services.logo_sync.requests.get',
                   return_value=_benzinga_response(entries)):
            updated, _ = sync_logos('t')
        assert updated == 0


class TestChatPayloadLogo:
    def test_chats_include_logo_url(self, client, auth_headers, db_session,
                                    sample_watchlist, sample_company):
        sample_company.logo_url = 'https://img/mark.svg'
        db_session.session.commit()
        resp = client.get('/api/v1/chats/', headers=auth_headers)
        chat = resp.get_json()['chats'][0]
        assert chat['company']['logo_url'] == 'https://img/mark.svg'

    def test_company_detail_includes_logo_url(self, client, auth_headers, db_session,
                                              sample_company):
        sample_company.logo_url = 'https://img/mark.svg'
        db_session.session.commit()
        resp = client.get(f'/api/v1/companies/{sample_company.id}', headers=auth_headers)
        assert resp.get_json()['company']['logo_url'] == 'https://img/mark.svg'
