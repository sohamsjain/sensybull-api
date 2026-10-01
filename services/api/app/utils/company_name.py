"""Company names as readers see them — mirrors sensybull-web `src/lib/company-name.ts`.

EDGAR stores filer names shouted ("PURE CYCLE CORP") while newswire names
arrive cased ("Eos Energy Enterprises, Inc."). Everything a reader sees —
alert emails, push titles, chat-app messages — goes through
`display_company_name()`, which cases shouted names and drops trailing legal
forms, so "MICRON TECHNOLOGY INC" and "Micron Technology, Inc." both read
"Micron Technology", exactly as on the web. Machine payloads (webhooks, the
REST API) keep the stored name untouched.

Keep the rules in sync with the web file; tests in tests/test_company_name.py
pin the same cases.
"""

import re

# Legal forms and initialisms that title casing would ruin.
_KEEP_UPPER = {
    'LLC', 'PLC', 'LP', 'LLP', 'LLLP', 'NV', 'BV', 'AG', 'SA', 'SAB', 'AB',
    'ASA', 'OYJ', 'SE', 'KGAA', 'PBC',
    'USA', 'US', 'UK', 'EU', 'UAE', 'AI', 'IT', 'EV', 'TV', 'HVAC', 'REIT',
    'ETF', 'SPAC', 'ADR', 'ADS', 'PC', 'MRI', 'LED', 'GPS', 'IP',
}

# Shouted abbreviations that are words, not initialisms.
_KEEP_TITLE = {
    'LTD': 'Ltd', 'MFG': 'Mfg', 'MGMT': 'Mgmt', 'GRP': 'Grp', 'HLDG': 'Hldg',
    'HLDGS': 'Hldgs', 'BROS': 'Bros', 'SVCS': 'Svcs', 'MTG': 'Mtg',
    'PTNRS': 'Ptnrs', 'PRTNRS': 'Prtnrs',
}

_SMALL_WORDS = set(
    'a an and as at but by de del for from in into la las los nor of on or '
    'the to van von with y'.split()
)

_ROMAN = re.compile(r'^(?:I{1,3}|IV|VI{0,3}|IX|XI{0,2}|XII)$')
_DOTTED_INITIALISM = re.compile(r'^(?:[A-Z]\.){2,}$')
_ORDINAL = re.compile(r'^(\d+)(ST|ND|RD|TH)$')
_EDGE_PUNCT = re.compile(r'^[\W_]+|[\W_]+$')
_LETTER_RUN = re.compile(r'[^\W\d_]+')
_STATE_TAG = re.compile(r'\s*/[A-Za-z]{2,3}/?\s*$')

# Trailing legal forms dropped for display. Excludes words that are part of
# how a company is known ("Holdings", "Group", "Company", "Trust", "Bancorp").
_LEGAL_SUFFIX = {
    'inc', 'incorporated', 'corp', 'corporation', 'co', 'ltd', 'limited',
    'plc', 'llc', 'lp', 'nv', 'sa', 'ag', 'se',
}


def _is_shouted(name: str) -> bool:
    return any(c.isupper() for c in name) and not any(c.islower() for c in name)


def _title_run(match: re.Match) -> str:
    run = match.group(0)
    if re.match(r'^MC[A-Z]{2,}$', run):
        return 'Mc' + run[2] + run[3:].lower()
    return run[0] + run[1:].lower()


def _case_token(token: str, is_edge: bool) -> str:
    if not any(c.isupper() for c in token):
        return token
    if _DOTTED_INITIALISM.match(token.rstrip(',;')):
        return token

    core = _EDGE_PUNCT.sub('', token)
    if core in _KEEP_UPPER:
        return token
    if core in _KEEP_TITLE:
        return token.replace(core, _KEEP_TITLE[core])
    if _ROMAN.match(core) or len(core) == 1:
        return token

    ordinal = _ORDINAL.match(core)
    if ordinal:
        return token.replace(core, ordinal.group(1) + ordinal.group(2).lower())
    # A short token carrying a digit is a mark, not a word: 3M, P10, K12.
    if any(c.isdigit() for c in core) and sum(c.isalpha() for c in core) <= 3:
        return token

    # Vowel-less runs are initialisms: PBF, NRG, CRH.
    if re.match(r'^[A-Z]{2,5}$', core) and not re.search(r'[AEIOUY]', core):
        return token
    # AT&T, H&R.
    if '&' in core and all(len(p) <= 2 for p in core.split('&')):
        return token

    if not is_edge and core.lower() in _SMALL_WORDS:
        return token.lower()

    titled = _LETTER_RUN.sub(_title_run, token)
    return re.sub(r"'S(?![^\W\d_])", "'s", titled)


def case_company_name(name: str | None) -> str:
    """Title-case a shouted EDGAR name; pass every other name through as-is."""
    if not name:
        return ''
    if not _is_shouted(name):
        return name
    tokens = re.split(r'(\s+)', name)
    words = [t for t in tokens if any(c.isalpha() for c in t)]
    first, last = (words[0], words[-1]) if words else (None, None)
    return ''.join(
        t if t.isspace() or not t else _case_token(t, t == first or t == last)
        for t in tokens
    )


def display_company_name(name: str | None) -> str:
    """The name a reader would say: cased, minus trailing legal forms and state tags."""
    full = case_company_name(name)
    if not full:
        return ''
    words = _STATE_TAG.sub('', full).split()
    while len(words) > 1:
        if re.sub(r'[.,]', '', words[-1]).lower() not in _LEGAL_SUFFIX:
            break
        words.pop()
    short = re.sub(r'[\s,&]+$', '', ' '.join(words))
    return short or full
