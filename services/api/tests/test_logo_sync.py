"""Tests that API payloads include the logo_url field."""


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
