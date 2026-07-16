"""Fingerprints: exact/headline hashes and simhash fuzziness."""

from press_release.fingerprint import (
    build_fingerprints,
    exact_fingerprint,
    fingerprints_match,
    hamming,
    headline_fingerprint,
    normalize_text,
    simhash64,
)

_PR_BODY = """
BOISE, Idaho, July 15, 2026 (GLOBE NEWSWIRE) -- Micron Technology, Inc.
(Nasdaq: MU) today announced it has entered into a definitive agreement to
acquire ChipWorks Corporation for $2.1 billion in cash, expected to close in
the fourth quarter of 2026 subject to regulatory approval. The acquisition
adds advanced packaging capacity and is expected to be accretive to earnings
in the first full year. Micron will fund the transaction with cash on hand
and $500 million of new term debt. The boards of both companies have
unanimously approved the transaction, which requires ChipWorks shareholder
approval at a special meeting expected in September 2026.
"""

# The same release as it appears inside the 8-K's EX-99.1: exhibit caption,
# different whitespace, EDGAR-style header noise, and a boilerplate tail.
_EX99_BODY = """
Exhibit 99.1

Micron Technology Announces Definitive Agreement to Acquire ChipWorks

BOISE, Idaho, July 15, 2026 -- Micron Technology, Inc. (Nasdaq: MU) today
announced it has entered into a definitive agreement to acquire ChipWorks
Corporation for $2.1 billion in cash, expected to close in the fourth
quarter of 2026 subject to regulatory approval.   The acquisition adds
advanced packaging capacity and is expected to be accretive to earnings in
the first full year. Micron will fund the transaction with cash on hand and
$500 million of new term debt. The boards of both companies have unanimously
approved the transaction, which requires ChipWorks shareholder approval at a
special meeting expected in September 2026.

Forward-Looking Statements: This press release contains forward-looking
statements regarding the proposed transaction.
"""

_UNRELATED = """
AUSTIN, Texas -- Tesla, Inc. (NASDAQ: TSLA) today announced financial
results for the second quarter ended June 30, 2026. Revenue was $27.9
billion, up 4% year over year, and GAAP net income was $2.1 billion.
Automotive gross margin improved to 19.4% on higher deliveries.
"""


def test_normalize_text():
    assert normalize_text("  Hello,\n\tWORLD!! 42 ") == "hello world 42"


def test_exact_and_headline_fingerprints_stable():
    a = exact_fingerprint("Headline!", "Body text.")
    b = exact_fingerprint("headline", "body TEXT")
    assert a == b
    assert headline_fingerprint("Headline!") == headline_fingerprint("HEADLINE")


def test_simhash_matches_ex99_variant_but_sha_does_not():
    fp_pr = build_fingerprints("Micron to acquire ChipWorks", _PR_BODY)
    fp_ex = build_fingerprints("something else entirely", _EX99_BODY)

    # Byte-different copies: exact hashes differ...
    assert fp_pr["exact"] != fp_ex["exact"]
    # ...but the simhash distance is small
    assert hamming(fp_pr["simhash"], fp_ex["simhash"]) <= 8
    assert fingerprints_match(fp_pr, fp_ex)


def test_unrelated_bodies_do_not_match():
    fp_a = build_fingerprints("Micron to acquire ChipWorks", _PR_BODY)
    fp_b = build_fingerprints("Tesla Q2 results", _UNRELATED)
    assert not fingerprints_match(fp_a, fp_b)
    assert hamming(fp_a["simhash"], fp_b["simhash"]) > 8


def test_headline_match_alone_suffices():
    fp_a = {"exact": "x1", "headline": "h1", "simhash": "0" * 16}
    fp_b = {"exact": "x2", "headline": "h1", "simhash": "0" * 16}
    assert fingerprints_match(fp_a, fp_b)


def test_empty_simhash_never_matches():
    assert simhash64("") == "0" * 16
    assert hamming("0" * 16, "0" * 16) == 65
    fp_a = {"exact": "a", "headline": "b", "simhash": "0" * 16}
    fp_b = {"exact": "c", "headline": "d", "simhash": "0" * 16}
    assert not fingerprints_match(fp_a, fp_b)


def test_hamming_garbage_is_incomparable():
    assert hamming("not-hex", "abcd" * 4) == 65
