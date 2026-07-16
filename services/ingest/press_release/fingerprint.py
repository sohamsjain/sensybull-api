"""
fingerprint.py — content fingerprints for press-release / 8-K dedup.

The same announcement appears up to three times: on one or more newswires
and again as an EX-99 exhibit inside the follow-up 8-K. Wire copies are
near-verbatim but never byte-identical (exhibit captions, EDGAR headers,
entity/whitespace differences), so every event carries both an exact hash
and a fuzzy simhash:

  match(a, b) is true when
    - exact fingerprints are equal, or
    - headline fingerprints are equal, or
    - the simhash Hamming distance is <= SIMHASH_MATCH_DISTANCE
      (callers must additionally require the same company).

NOTE: the match rule is duplicated in
services/api/app/services/realtime/pr_dedup.py (the two services share no
code). Keep the normalization and thresholds in sync.
"""

import hashlib
import re

# Max Hamming distance (out of 64 bits) at which two bodies count as the
# same release. Distances just above this are logged by callers as
# near-misses for tuning.
SIMHASH_MATCH_DISTANCE = 8

# Normalized text is truncated before hashing so tail boilerplate that
# varies between copies (contact blocks, safe-harbor language, exhibit
# footers) cannot break the exact match.
_NORMALIZED_TEXT_CAP = 10_000

_SHINGLE_SIZE = 3

# Everything from these markers on is legal/contact boilerplate that varies
# between the wire copy and the EX-99 copy of the same release — cut before
# hashing so it can't inflate the simhash distance.
_TAIL_MARKERS = re.compile(
    r"(?im)^[^\S\n]*(?:"
    r"forward[\s\-]+looking\s+(?:statements?|information)"
    r"|safe\s+harbor"
    r"|cautionary\s+(?:note|statement|language)"
    r"|about\s+\S+"                 # "About Micron", "About the Company"
    r"|(?:investor|media)\s+(?:relations\s+)?contacts?\b"
    r"|for\s+(?:more|further)\s+information"
    r"|source[:\s]"
    r")"
)

# Exhibit caption lines ("Exhibit 99.1") only exist on the 8-K copy.
_EXHIBIT_CAPTION = re.compile(r"(?im)^[^\S\n]*exhibit\s+99[.\d]*\s*$")


def clean_release_body(text: str) -> str:
    """Reduce a release body to its substantive announcement text: drop
    exhibit captions and truncate at the boilerplate tail."""
    m = _TAIL_MARKERS.search(text)
    if m and m.start() > 200:   # never let a marker in the opening wipe the body
        text = text[:m.start()]
    return _EXHIBIT_CAPTION.sub("", text)


def normalize_text(text: str) -> str:
    """Lowercase, strip non-alphanumerics, collapse whitespace, cap length.

    Input should already be plain text (callers strip HTML first, e.g. via
    parser.strip_html).
    """
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_NORMALIZED_TEXT_CAP]


def exact_fingerprint(headline: str, body: str) -> str:
    """sha256 hex of the normalized headline + body."""
    norm = normalize_text(headline) + "\n" + normalize_text(body)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def headline_fingerprint(headline: str) -> str:
    """sha256 hex of the normalized headline alone."""
    return hashlib.sha256(normalize_text(headline).encode("utf-8")).hexdigest()


def _shingle_hash(shingle: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big"
    )


def simhash64(text: str) -> str:
    """64-bit simhash over word 3-shingles of the normalized text, as 16 hex chars.

    Empty/too-short text hashes to all zeros — callers must treat "0" * 16
    as "no fingerprint" rather than a wildcard match.
    """
    words = normalize_text(text).split()
    if len(words) < _SHINGLE_SIZE:
        return "0" * 16

    weights = [0] * 64
    for i in range(len(words) - _SHINGLE_SIZE + 1):
        h = _shingle_hash(" ".join(words[i:i + _SHINGLE_SIZE]))
        for bit in range(64):
            if h & (1 << bit):
                weights[bit] += 1
            else:
                weights[bit] -= 1

    value = 0
    for bit in range(64):
        if weights[bit] > 0:
            value |= 1 << bit
    return f"{value:016x}"


def hamming(simhash_a: str, simhash_b: str) -> int:
    """Hamming distance between two 16-hex-char simhashes (65 = incomparable)."""
    try:
        a, b = int(simhash_a, 16), int(simhash_b, 16)
    except (ValueError, TypeError):
        return 65
    if a == 0 or b == 0:
        return 65  # "no fingerprint" never matches anything
    return bin(a ^ b).count("1")


def fingerprints_match(
    fp_a: dict, fp_b: dict, max_distance: int = SIMHASH_MATCH_DISTANCE
) -> bool:
    """Apply the canonical match rule to two fingerprint dicts.

    Each dict carries: exact, headline, simhash (any may be empty).
    Company identity is NOT checked here — callers must only compare
    fingerprints of events for the same company.
    """
    if fp_a.get("exact") and fp_a.get("exact") == fp_b.get("exact"):
        return True
    if fp_a.get("headline") and fp_a.get("headline") == fp_b.get("headline"):
        return True
    return hamming(fp_a.get("simhash", ""), fp_b.get("simhash", "")) <= max_distance


def build_fingerprints(headline: str, body: str) -> dict:
    """Convenience: the full fingerprint dict for a headline + plain-text body.

    The body is boilerplate-cleaned first so the wire copy and the EX-99
    copy of the same release hash the same substantive text.
    """
    body = clean_release_body(body)
    return {
        "exact": exact_fingerprint(headline, body),
        "headline": headline_fingerprint(headline),
        "simhash": simhash64(body),
    }
