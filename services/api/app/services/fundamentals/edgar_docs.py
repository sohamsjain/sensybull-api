"""SEC filings list for the Documents section, from EDGAR's free
`submissions` API (we already hold every company's CIK).

Requires SEC_USER_AGENT like the rest of the EDGAR clients. Cached by the
route (6 h) — the submissions JSON is ~1 MB for large filers.
"""

import logging
import os

import requests

log = logging.getLogger(__name__)

SUBMISSIONS_URL = 'https://data.sec.gov/submissions/CIK{cik}.json'
ARCHIVE_URL = 'https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{doc}'
INDEX_URL = 'https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/'
TIMEOUT = 20

ANNUAL_FORMS = {'10-K', '10-K/A', '20-F', '20-F/A', '40-F', '10-KT'}
QUARTERLY_FORMS = {'10-Q', '10-Q/A', '6-K'}
PROXY_FORMS = {'DEF 14A', 'DEFA14A', 'DEFM14A'}
MAX_PER_GROUP = 12


class EdgarDocsError(Exception):
    pass


def fetch_documents(cik: str) -> dict:
    """{annual: [...], quarterly: [...], proxy: [...], recent_8k_count: int}"""
    user_agent = os.environ.get('SEC_USER_AGENT') or ''
    if not user_agent:
        raise EdgarDocsError('SEC_USER_AGENT not set')
    cik10 = str(cik).zfill(10)
    resp = requests.get(SUBMISSIONS_URL.format(cik=cik10),
                        headers={'User-Agent': user_agent, 'Accept': 'application/json'},
                        timeout=TIMEOUT)
    if resp.status_code == 404:
        return {'annual': [], 'quarterly': [], 'proxy': [], 'recent_8k_count': 0}
    if resp.status_code != 200:
        raise EdgarDocsError(f'EDGAR submissions HTTP {resp.status_code}')
    return parse_submissions(resp.json(), cik10)


def parse_submissions(data: dict, cik10: str) -> dict:
    recent = (data.get('filings') or {}).get('recent') or {}
    forms = recent.get('form') or []
    dates = recent.get('filingDate') or []
    accessions = recent.get('accessionNumber') or []
    docs = recent.get('primaryDocument') or []
    descs = recent.get('primaryDocDescription') or []
    report_dates = recent.get('reportDate') or []
    cik_int = str(int(cik10))

    out = {'annual': [], 'quarterly': [], 'proxy': [], 'recent_8k_count': 0}
    for i, form in enumerate(forms):
        accession = accessions[i] if i < len(accessions) else ''
        if not accession:
            continue
        entry = {
            'form': form,
            'filed': dates[i] if i < len(dates) else None,
            'period': (report_dates[i] if i < len(report_dates) else None) or None,
            'description': (descs[i] if i < len(descs) else None) or None,
            'url': _doc_url(cik_int, accession, docs[i] if i < len(docs) else ''),
        }
        if form in ANNUAL_FORMS and len(out['annual']) < MAX_PER_GROUP:
            out['annual'].append(entry)
        elif form in QUARTERLY_FORMS and len(out['quarterly']) < MAX_PER_GROUP:
            out['quarterly'].append(entry)
        elif form in PROXY_FORMS and len(out['proxy']) < MAX_PER_GROUP:
            out['proxy'].append(entry)
        elif form in ('8-K', '8-K/A'):
            out['recent_8k_count'] += 1
    return out


def _doc_url(cik_int: str, accession: str, primary_doc: str) -> str:
    nodash = accession.replace('-', '')
    if primary_doc:
        return ARCHIVE_URL.format(cik_int=cik_int, accession=nodash, doc=primary_doc)
    return INDEX_URL.format(cik_int=cik_int, accession=nodash)
