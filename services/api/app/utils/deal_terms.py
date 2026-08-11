"""Normalization for the LLM-extracted `deal_terms` block.

The briefing model returns deal terms as free-form key/value strings, and
their casing is whatever the filing text (or the model's mood) produced:
"definitive agreement signed", "stock", "spac merger". Those strings are
rendered verbatim in the UI's Deal Terms panel, so they get normalized once
here — at the single point where events enter the database (the Redis
subscriber) — rather than at each of the several places that read them.

Two things are normalized:

* Keys are canonicalized to snake_case ("Deal Value" / "dealValue" →
  "deal_value") so the frontend's display order and label map keep working
  whatever shape the model answers with.
* Values are Title Cased, preserving anything that already carries its own
  capitalization ("SPAC", "Inc.", "iPhone") or that isn't a word at all
  ("$2.5B", "45%", "Q4 2026"). Values long enough to be a sentence rather
  than a label get sentence case instead — "Definitive Agreement Signed"
  reads right, "The Board Approved The Transaction On June 3" does not.

Mirrored in sensybull-web `src/lib/deal-terms.ts`, which applies the same
rules at render time so events persisted before this landed also display
correctly — keep the two in sync.
"""

import re

# Words that stay lowercase inside a title-cased value (never at the edges).
_SMALL_WORDS = frozenset(
    "a an and as at but by for from in into nor of on onto or per the to up "
    "via vs with".split()
)

# Past this many words a value is prose, not a label: title casing it would
# read as a headline. Deal statuses ("definitive agreement signed") and deal
# types ("stock-for-stock merger") sit well under the limit.
_TITLE_CASE_WORD_LIMIT = 6

# Segments inside a compound word are cased independently: "stock-for-stock"
# → "Stock-for-Stock", "cash/stock" → "Cash/Stock".
_COMPOUND_SPLIT = re.compile(r"([-/])")

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM_RUN = re.compile(r"[^0-9a-zA-Z]+")

_EDGE_PUNCT = "\"'“”‘’()[]{}.,;:!?"


def normalize_deal_terms(terms: object) -> dict:
    """Return a display-ready copy of a deal_terms mapping.

    Non-dict input yields {}. Entries with an empty key or value are
    dropped; non-string values are passed through untouched (ingest already
    coerces them to scalars — this layer must not silently discard data it
    doesn't recognize). When two keys collapse to the same canonical key,
    the first one wins.
    """
    if not isinstance(terms, dict):
        return {}

    out: dict = {}
    for key, value in terms.items():
        if not isinstance(key, str) or value in (None, ""):
            continue
        canonical = normalize_term_key(key)
        if not canonical or canonical in out:
            continue
        out[canonical] = (
            normalize_term_value(value) if isinstance(value, str) else value
        )
    return {k: v for k, v in out.items() if v not in (None, "")}


def normalize_term_key(key: str) -> str:
    """Canonicalize a deal-term key to snake_case: "Deal Value" → "deal_value"."""
    if not isinstance(key, str):
        return ""
    spaced = _CAMEL_BOUNDARY.sub("_", key.strip())
    return _NON_ALNUM_RUN.sub("_", spaced).strip("_").lower()


def normalize_term_value(value: str) -> str:
    """Title-case a deal-term value, leaving figures and acronyms alone."""
    if not isinstance(value, str):
        return value
    words = value.split()
    if not words:
        return ""
    if len(words) > _TITLE_CASE_WORD_LIMIT:
        return _sentence_case(words)
    last = len(words) - 1
    return " ".join(
        _case_word(word, edge=(i == 0 or i == last))
        for i, word in enumerate(words)
    )


def _sentence_case(words: list) -> str:
    """Capitalize only the opening word — used for prose-length values."""
    return " ".join([_case_word(words[0], edge=True)] + words[1:])


def _case_word(word: str, *, edge: bool) -> str:
    """Case one whitespace-delimited word, compound segments included.

    Only the first segment of a compound inherits the word's edge position:
    in "stock-for-stock" the inner "for" is a small word wherever the word
    itself sits, so it stays lowercase.
    """
    parts = _COMPOUND_SPLIT.split(word)
    return "".join(
        part if part in ("-", "/") else _case_segment(part, edge=edge and i == 0)
        for i, part in enumerate(parts)
    )


def _case_segment(segment: str, *, edge: bool) -> str:
    """Capitalize a single word segment, or return it untouched.

    Untouched when it carries a digit (a figure, not a word), already has a
    capital of its own (an acronym, a ticker, a proper name, "Inc."), or is
    a small word away from the value's edges.
    """
    if not segment:
        return segment
    if any(ch.isdigit() for ch in segment):
        return segment
    if any(ch.isupper() for ch in segment):
        return segment
    if not edge and segment.strip(_EDGE_PUNCT).lower() in _SMALL_WORDS:
        return segment
    for i, ch in enumerate(segment):
        if ch.isalpha():
            return segment[:i] + ch.upper() + segment[i + 1:]
    return segment
