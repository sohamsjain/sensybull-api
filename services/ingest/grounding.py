"""
grounding.py — deterministic verification of LLM briefing output against
the exact source text that was sent to the model.

Zero-trust policy: the model's narrative is treated as unverified until
every material fact in it — dollar amounts, share counts, percentages,
prices, dates, and named entities — is located in the source text. Facts
that cannot be grounded disqualify the field they appear in; ungrounded
narrative (headline/summary) disqualifies the whole briefing, which then
falls back to a deterministic facts-only rendering.

The checks are one-directional by design: a false positive here drops a
truthful sentence (annoying, recoverable — the filing link is right there);
a false negative would put an invented fact in front of a user (never
acceptable). When in doubt, reject.

Pure functions, no I/O.
"""

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[.,;:()\"“”]")
_APOSTROPHES = re.compile(r"['’‘]")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation/apostrophes, collapse whitespace."""
    text = _APOSTROPHES.sub("", text.lower())
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

_MAGNITUDES = {
    "thousand": 1e3, "k": 1e3,
    "million": 1e6, "m": 1e6, "mm": 1e6,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "trillion": 1e12,
}

_NUM_RE = re.compile(
    r"(\$)?\s*"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(thousand|million|billion|trillion|mm|bn|k|m|b)?(%)?"
    # not followed by another digit — a bare (?![\w.]) would backtrack on a
    # sentence-ending period and split "$4,300,000." into "$4,300"
    r"(?!\d)",
    re.IGNORECASE,
)


def _parse(raw: str) -> float:
    return float(raw.replace(",", ""))


def corpus_number_values(text: str) -> set[float]:
    """Every numeric value present in the source text, in both raw and
    magnitude-scaled form ("$30 million" grounds both 30 and 30000000)."""
    values: set[float] = set()
    for m in _NUM_RE.finditer(text):
        base = _parse(m.group(2))
        values.add(base)
        suffix = (m.group(3) or "").lower()
        if suffix in _MAGNITUDES:
            values.add(base * _MAGNITUDES[suffix])
    return values


@dataclass
class _Claim:
    text: str          # the matched token, for error messages
    candidates: set[float]


def _material_number_claims(text: str) -> list[_Claim]:
    """Numbers in generated text that MUST be grounded.

    Trivial small integers (no $, %, magnitude, comma-grouping, or decimal
    point) are skipped — "three of 7 directors" style counts are too noisy
    to verify and too small to mislead. Everything that looks like money,
    a quantity, a price, or a percentage is material.
    """
    claims: list[_Claim] = []
    for m in _NUM_RE.finditer(text):
        dollar, raw, suffix, pct = m.group(1), m.group(2), (m.group(3) or "").lower(), m.group(4)
        base = _parse(raw)
        material = bool(
            dollar or pct or suffix in _MAGNITUDES
            or "," in raw or "." in raw or base >= 1000
        )
        if not material:
            continue
        # A 4-digit year is handled by the date checks, not the number check.
        if not dollar and not pct and not suffix and raw.isdigit() and 1900 <= base <= 2100:
            continue
        candidates = {base * _MAGNITUDES[suffix]} if suffix in _MAGNITUDES else {base}
        claims.append(_Claim(text=m.group(0).strip(), candidates=candidates))
    return claims


def ungrounded_numbers(claim_text: str, corpus_values: set[float]) -> list[str]:
    """Material numbers in claim_text with no counterpart in the source."""
    return [
        c.text for c in _material_number_claims(claim_text)
        if not (c.candidates & corpus_values)
    ]


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_MONTHS = [
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
]
_MONTH_ABBR = [m[:3] for m in _MONTHS]

_TEXT_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS + _MONTH_ABBR) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


def _date_variants(year: int, month: int, day: int) -> list[str]:
    """Normalized textual representations a filing might use for a date."""
    name, abbr = _MONTHS[month - 1], _MONTH_ABBR[month - 1]
    return [
        f"{name} {day} {year}",
        f"{abbr} {day} {year}",
        f"{day} {name} {year}",
        f"{month}/{day}/{year}",
        f"{month:02d}/{day:02d}/{year}",
        f"{year}-{month:02d}-{day:02d}",
    ]


def date_grounded(year: int, month: int, day: int, corpus_norm: str) -> bool:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return False
    return any(v in corpus_norm for v in _date_variants(year, month, day))


def iso_date_grounded(iso: str, corpus_norm: str) -> bool:
    """Ground an ISO YYYY-MM-DD date (catalyst dates) against the source."""
    m = _ISO_DATE_RE.fullmatch(iso.strip())
    if not m:
        return False
    return date_grounded(int(m.group(1)), int(m.group(2)), int(m.group(3)), corpus_norm)


def ungrounded_dates(claim_text: str, corpus_norm: str) -> list[str]:
    """Full dates written in generated prose that the source never states."""
    bad: list[str] = []
    for m in _TEXT_DATE_RE.finditer(claim_text):
        month_raw = m.group(1).lower()
        month = (_MONTHS.index(month_raw) + 1 if month_raw in _MONTHS
                 else _MONTH_ABBR.index(month_raw[:3]) + 1)
        if not date_grounded(int(m.group(3)), month, int(m.group(2)), corpus_norm):
            bad.append(m.group(0))
    for m in _SLASH_DATE_RE.finditer(claim_text):
        if not date_grounded(int(m.group(3)), int(m.group(1)), int(m.group(2)), corpus_norm):
            bad.append(m.group(0))
    for m in _ISO_DATE_RE.finditer(claim_text):
        if not date_grounded(int(m.group(1)), int(m.group(2)), int(m.group(3)), corpus_norm):
            bad.append(m.group(0))
    return bad


# ---------------------------------------------------------------------------
# Named entities
# ---------------------------------------------------------------------------

# Lowercase words allowed INSIDE a proper-name run without breaking it
_NAME_CONNECTORS = {"of", "and", "&", "for", "de", "da", "la", "the"}

_TOKEN_RE = re.compile(r"[A-Za-z&][\w&.\-']*")


def _capitalized_runs(text: str) -> list[str]:
    """Multi-word capitalized spans — the proper names asserted by the text.

    Runs need >= 2 capitalized tokens ("Ecovative Design LLC"), which keeps
    ordinary sentence-initial capitals out of scope.
    """
    runs: list[str] = []
    current: list[str] = []
    cap_count = 0
    for tok in _TOKEN_RE.findall(text):
        if tok[0].isupper() or tok == "&":
            current.append(tok)
            if tok != "&":
                cap_count += 1
        elif tok.lower() in _NAME_CONNECTORS and current:
            current.append(tok)
        else:
            if cap_count >= 2:
                runs.append(" ".join(current).strip())
            current, cap_count = [], 0
    if cap_count >= 2:
        runs.append(" ".join(current).strip())
    # Trim trailing connectors ("Solstice Advanced Materials and")
    cleaned = []
    for run in runs:
        words = run.split()
        while words and words[-1].lower() in _NAME_CONNECTORS:
            words.pop()
        if len([w for w in words if w[0].isupper() and w != "&"]) >= 2:
            cleaned.append(" ".join(words))
    return cleaned


def ungrounded_names(claim_text: str, corpus_norm: str,
                     allowed: tuple[str, ...] = ()) -> list[str]:
    """Proper-name spans asserted by the claim that the source never mentions."""
    allowed_norm = [normalize(a) for a in allowed if a]
    bad: list[str] = []
    for run in _capitalized_runs(claim_text):
        run_norm = normalize(run)
        if not run_norm or run_norm in corpus_norm:
            continue
        # Allowed if the run is part of an allowed name, or an allowed name
        # appears in the run as WHOLE WORDS — a bare substring test would
        # let ticker "ESI" whitelist "Ecovative D[esi]gn LLC".
        padded = f" {run_norm} "
        if any(run_norm in a or f" {a} " in padded for a in allowed_norm):
            continue
        bad.append(run)
    return bad


# ---------------------------------------------------------------------------
# Field-level verification
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "the", "and", "with", "from", "that", "this", "into", "under", "over",
    "have", "been", "will", "shall", "such", "each", "than", "then",
}


def _content_words(value: str) -> list[str]:
    return [w for w in normalize(value).split()
            if len(w) >= 4 and w not in _STOPWORDS]


@dataclass
class Corpus:
    """Pre-computed views of the source text for repeated checks."""
    raw: str
    norm: str = ""
    numbers: set[float] = field(default_factory=set)

    def __post_init__(self):
        self.norm = normalize(self.raw)
        self.numbers = corpus_number_values(self.raw)


def build_corpus(source_text: str) -> Corpus:
    return Corpus(raw=source_text)


def narrative_problems(text: str, corpus: Corpus,
                       allowed_names: tuple[str, ...] = ()) -> list[str]:
    """All grounding failures in a narrative field. Empty list == grounded."""
    if not text:
        return []
    problems = [f"number not in filing: {t}"
                for t in ungrounded_numbers(text, corpus.numbers)]
    problems += [f"date not in filing: {t}"
                 for t in ungrounded_dates(text, corpus.norm)]
    problems += [f"name not in filing: {t}"
                 for t in ungrounded_names(text, corpus.norm, allowed_names)]
    return problems


# deal_terms keys whose values are interpretive labels rather than extracted
# facts — they still must echo language that exists somewhere in the source.
_INTERPRETIVE_TERMS = {"deal_status", "deal_type", "consideration_type"}


def verify_deal_terms(terms: dict[str, str], corpus: Corpus,
                      allowed_names: tuple[str, ...] = ()) -> tuple[dict[str, str], list[str]]:
    """Return (grounded_terms, dropped_reasons)."""
    kept: dict[str, str] = {}
    dropped: list[str] = []
    for key, value in terms.items():
        problems = narrative_problems(value, corpus, allowed_names)
        if key == "counterparty":
            # Strictest check: the named counterparty must literally appear.
            if normalize(value) not in corpus.norm:
                problems.append(f"counterparty not in filing: {value}")
        elif key in _INTERPRETIVE_TERMS or not problems:
            # Interpretive labels (and prose with no checkable facts) must
            # at least echo a content word from the source.
            words = _content_words(value)
            if words and not any(w in corpus.norm for w in words):
                problems.append(f"no supporting language in filing: {value}")
        if problems:
            dropped.append(f"{key}: " + "; ".join(problems))
        else:
            kept[key] = value
    return kept, dropped


def verify_catalysts(catalysts: list[dict], corpus: Corpus) -> tuple[list[dict], list[str]]:
    """Keep only catalysts whose date (if given) and description are grounded."""
    kept: list[dict] = []
    dropped: list[str] = []
    for cat in catalysts:
        event = cat.get("event", "")
        date = cat.get("date")
        problems = narrative_problems(event, corpus)
        if date and not iso_date_grounded(str(date), corpus.norm):
            problems.append(f"date not in filing: {date}")
        if problems:
            dropped.append(f"{event or date}: " + "; ".join(problems))
        else:
            kept.append(cat)
    return kept, dropped
