"""
language.py — is this release written in English?

Wires syndicate translations of the same release (PR Newswire publishes a
German, French and Spanish copy beside the English one) and FMP carries no
language field, so the wire's own tag can't be the only check. This is a
dependency-free detector tuned for one question — English or not — on
release-length prose:

  1. script: mostly non-Latin letters (CJK, Cyrillic, Arabic, …) → not English;
  2. function words: English prose is ~25-40% function words; translations
     into other Latin-script languages score near zero on an English-only
     list. Words shared with German/French/Spanish ("in", "a", "an", "on",
     "die", "de") are deliberately left off the list.

A headline plus a one-line teaser is telegraphic English ("BIO-key Adds New
Middle East Government Security Agency Customer…") and scores below the
ratio, so on short text a low score alone isn't enough: it must also show
more German/French/Spanish/Italian/Dutch function words (or words in
Vietnamese-style extended Latin) than English ones.

Too little text to judge → assume English and let the later gates decide;
a missed translation is caught by the LLM, a dropped English release is lost.
"""

import re

_ENGLISH_FUNCTION_WORDS = frozenset("""
    the and of to is are was were with for that this which has have had
    by from its will be been their our about after into than more other
    such these those would could should also but not who what when where
    while over under between through during against today announced
    company new year million billion per
""".split())

# Function words of the languages wires translate into. Words that are also
# common in English press releases are left off: "die", "et" (Eastern
# Time), "los"/"las"/"del"/"el" (Los Angeles, Las Vegas, Wilmington, Del.,
# El Paso), "y" (y-o-y), "se", "per", "do", "van".
_FOREIGN_FUNCTION_WORDS = frozenset("""
    der das und ist mit für von den dem des ein eine einer nicht auf sich
    wird werden bei nach über
    le la les est avec pour dans une sur par du au aux qui que cette sont été
    es con para por una que su sus como más
    il della di che gli delle
    het een voor niet zijn wordt
    não os da
""".split())

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Below this many words the ratio is noise
_MIN_WORDS = 20
_MIN_FUNCTION_RATIO = 0.10
# Below this many words a low English ratio needs foreign evidence too.
# Covers a headline + FMP teaser (First Atlantic Nickel's, Oct 13 2026: 72
# words, 5 function words, no foreign ones); article bodies run to hundreds.
_SHORT_TEXT_WORDS = 150
_MAX_NON_LATIN_SHARE = 0.30


def _non_latin_share(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    # Latin-1 + Latin Extended-A/B cover English and accented Western
    # European text; anything above that is another script.
    non_latin = sum(1 for c in letters if ord(c) > 0x024F)
    return non_latin / len(letters)


def is_english(text: str) -> bool:
    """True unless the text is confidently not English."""
    text = text[:4000]
    if _non_latin_share(text) > _MAX_NON_LATIN_SHARE:
        return False
    words = [w.lower() for w in _WORD_RE.findall(text)]
    if len(words) < _MIN_WORDS:
        return True
    hits = sum(1 for w in words if w in _ENGLISH_FUNCTION_WORDS)
    if hits / len(words) >= _MIN_FUNCTION_RATIO:
        return True
    if len(words) >= _SHORT_TEXT_WORDS:
        return False
    # A word with letters past Latin Extended-B (Vietnamese "những", …) is
    # never English, even when the script check above let the text through.
    foreign = sum(1 for w in words
                  if w in _FOREIGN_FUNCTION_WORDS or any(ord(c) > 0x024F for c in w))
    return foreign <= hits
