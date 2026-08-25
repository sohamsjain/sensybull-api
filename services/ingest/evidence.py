"""
evidence.py — verify a model's supporting quote, then make it clickable.

The briefing tells the reader what happened. Evidence shows them the words
in the filing that say so, and takes them to those words on sec.gov. That
only builds trust if the quote is genuinely in the document, so nothing the
model writes is displayed as a quote: what ships is the span of the *source
document* that the model's quote matched.

The pipeline for one cited quote:

1. **Locate.** The quote is matched against each source document (the
   primary 8-K document and any EX-99 exhibit we fetched) using overlapping
   four-word shingles, following Dolphin et al., "Grounded Event Extraction
   from SEC 8-K Filings" (arXiv:2607.08346): a quote is accepted only when
   at least 40% of its shingles appear verbatim in the document. Fuzzy
   matching is what makes the check usable — models routinely re-wrap
   whitespace, straighten a curly apostrophe or drop a footnote marker
   while copying — and 40% is far above what unrelated text reaches, so a
   fabricated quote cannot pass.

2. **Snap.** The matching shingles pin the quote to an exact character span
   in the document. That span, not the model's transcription, becomes the
   stored quote. A displayed quote is therefore verbatim source text by
   construction rather than by the model's good behaviour.

3. **Link.** The span is turned into a browser text fragment
   (text_fragment.py) appended to the URL of the document it came from, so
   a click opens the filing on sec.gov scrolled to the passage with it
   highlighted. When no unambiguous fragment exists the entry keeps the
   document URL and says so (``highlighted: false``), which is still better
   than the EDGAR index page the feed otherwise links to.

Unlike the paper we do not retry the model when a quote fails to validate:
the briefing runs one pass against a tight per-minute token budget (see
briefing.py), and dropping the unverifiable quote already gives the
guarantee that matters — nothing unverified is ever shown. It costs
coverage, not trust.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from parser import item_label_at
from text_fragment import FragmentBuilder, append_fragment

# Shingle size and coverage floor from the paper (§3.2, "Quote grounding").
SHINGLE_SIZE = 4
MIN_COVERAGE = 0.40

# A quote of fewer than SHINGLE_SIZE words cannot be shingled, and is too
# short to prove anything on its own — dropped rather than exact-matched.
MIN_QUOTE_WORDS = SHINGLE_SIZE

# Longest quote we will carry. The prompt asks for ≤300 characters; anything
# past this is a model that started transcribing the filing, so it is cut at
# a word boundary before matching (never after, which would desynchronize
# the stored quote from the span the link points at).
MAX_QUOTE_CHARS = 400

# Evidence is a proof, not a second summary: three quotes is already more
# than a reader will check.
MAX_EVIDENCE = 3

# Punctuation the model swaps freely while copying. Folded away for
# matching only — the stored quote keeps whatever the filing used.
_PUNCT_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",   # curly single quotes
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u2033": '"',   # curly double quotes
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",   # hyphens and dashes
    "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u00a0": " ", "\u2007": " ", "\u202f": " ", "\u2009": " ",   # exotic spaces
    "\u200b": "",                                                  # zero-width space
})


def _fold(text: str) -> str:
    """Matching form: case- and punctuation-insensitive."""
    return text.translate(_PUNCT_FOLD).lower()


def _display(text: str) -> str:
    """Reading form of a span that may cross block boundaries.

    Whitespace only — the filing's own punctuation is left exactly as it
    was written, because this is the text shown to the reader as a quote.
    """
    return re.sub(r"\s+", " ", text).strip()


# Edge punctuation is dropped from a word before it is matched. A model
# copying a sentence out of a filing loses a trailing comma or a closing
# parenthesis constantly, and with four-word shingles one such slip kills
# four shingles at once — enough to sink an otherwise perfect quote below
# the coverage floor. Interior punctuation is kept, so "pay-in-kind" and
# "$1.2" still carry their shape.
_EDGE_PUNCT = re.compile(r"^[^0-9a-z]+|[^0-9a-z]+$")


def _key(word: str) -> str:
    return _EDGE_PUNCT.sub("", _fold(word))


def _keyed_words(text: str) -> list[tuple[str, int, int]]:
    """(match key, start, end) per word. Pure-punctuation words are skipped:
    they carry no evidence and only misalign the two token streams."""
    out = []
    for m in re.finditer(r"\S+", text):
        key = _key(m.group())
        if key:
            out.append((key, m.start(), m.end()))
    return out


@dataclass(frozen=True)
class SourceDoc:
    """One document a quote could have come from.

    ``text`` must be ``strip_html(html, preserve_nbsp=True)`` — the fragment
    builder needs the non-breaking spaces and the block newlines.
    """
    label: str   # "primary" or an exhibit type like "EX-99.1"
    url: str
    text: str


class _Index:
    """Word list + shingle index + fragment builder for one document."""

    __slots__ = ("doc", "words", "shingles", "fragments")

    def __init__(self, doc: SourceDoc) -> None:
        self.doc = doc
        self.words = _keyed_words(doc.text)
        self.shingles: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for i in range(len(self.words) - SHINGLE_SIZE + 1):
            key = tuple(w[0] for w in self.words[i:i + SHINGLE_SIZE])
            self.shingles[key].append(i)
        self.fragments = FragmentBuilder(doc.text)

    def locate(self, quote: str) -> tuple[int, int, float] | None:
        """Character span of ``quote`` in this document, plus its coverage.

        Returns None when the quote does not clear the coverage floor — the
        model cited something this document does not say.
        """
        words = [w[0] for w in _keyed_words(quote)]
        if len(words) < MIN_QUOTE_WORDS:
            return None

        keys = [
            tuple(words[i:i + SHINGLE_SIZE])
            for i in range(len(words) - SHINGLE_SIZE + 1)
        ]
        # Vote for a single alignment (document position minus quote
        # position) so shingles that happen to recur elsewhere in the filing
        # cannot stretch the span across unrelated text.
        votes: Counter[int] = Counter()
        hits: dict[int, list[int]] = defaultdict(list)
        for offset, key in enumerate(keys):
            for pos in self.shingles.get(key, ()):
                votes[pos - offset] += 1
                hits[pos - offset].append(pos)
        if not votes:
            return None

        # Ties go to the earliest alignment, so the same input always
        # resolves to the same span.
        alignment, matched = min(votes.items(), key=lambda kv: (-kv[1], kv[0]))
        coverage = matched / len(keys)
        if coverage < MIN_COVERAGE:
            return None

        positions = hits[alignment]
        lo, hi = self._widen(min(positions),
                             max(positions) + SHINGLE_SIZE - 1,
                             alignment, words)
        return self._span(lo, hi) + (coverage,)

    def _widen(self, lo: int, hi: int, alignment: int,
               words: list[str]) -> tuple[int, int]:
        """Grow a matched run out to the quote's full extent.

        The shingles pin down the middle of the quote; its first and last few
        words are usually the ones the model reworded, so they sit outside
        every intact shingle. Walk outwards while the document still reads
        the same as the quote, which recovers them without ever running past
        what the quote actually claimed.
        """
        limit = len(self.words) - 1
        while lo > 0 and 0 <= lo - 1 - alignment < len(words) \
                and self.words[lo - 1][0] == words[lo - 1 - alignment]:
            lo -= 1
        while hi < limit and 0 <= hi + 1 - alignment < len(words) \
                and self.words[hi + 1][0] == words[hi + 1 - alignment]:
            hi += 1
        return lo, min(hi, limit)

    def _span(self, lo: int, hi: int) -> tuple[int, int]:
        """Character span of words [lo, hi], trimmed of a dangling separator.

        A quote that ends on a comma reads like the sentence was cut off
        mid-thought; a full stop reads like a sentence.
        """
        start, end = self.words[lo][1], self.words[hi][2]
        while end > start and self.doc.text[end - 1] in " \t,;:":
            end -= 1
        return start, end

    def source_label(self, start: int) -> str:
        """Where in the document the span sits, for the quote's caption."""
        if self.doc.label != "primary":
            return self.doc.label
        return item_label_at(self.doc.text, start) or "the filing"


def _truncate_words(quote: str, limit: int) -> str:
    if len(quote) <= limit:
        return quote
    cut = quote[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > limit // 2 else cut


def resolve_evidence(cited: list[tuple[str, str]],
                     docs: list[SourceDoc]) -> list[dict]:
    """Turn (event_type, model quote) pairs into verified evidence entries.

    Quotes that no source document supports are dropped silently — the
    briefing is still published, just without that proof.

    Each returned entry:
        {
          "quote":       exact source text, whitespace-normalized
          "event_type":  the label this quote was cited for ("" if none)
          "source":      "Item 5.02" | "EX-99.1" | "the filing"
          "doc_url":     the document containing the quote
          "url":         doc_url + text fragment, when one could be built
          "highlighted": whether url scrolls to and highlights the passage
        }
    """
    indexes = [_Index(d) for d in docs if d.text.strip()]
    if not indexes:
        return []

    out: list[dict] = []
    # Spans already published, per document. Two cited quotes very often
    # overlap — the same sentence with one more clause — and showing the
    # reader the same passage twice is worse than showing it once.
    claimed: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for event_type, raw_quote in cited:
        if len(out) >= MAX_EVIDENCE:
            break
        quote = _truncate_words(re.sub(r"\s+", " ", raw_quote).strip(),
                                MAX_QUOTE_CHARS)
        if not quote:
            continue

        best: tuple[float, _Index, int, int] | None = None
        for index in indexes:
            found = index.locate(quote)
            if found and (best is None or found[2] > best[0]):
                best = (found[2], index, found[0], found[1])
        if best is None:
            continue

        _, index, start, end = best
        text = _display(index.doc.text[start:end])
        if not text:
            continue
        spans = claimed[id(index)]
        if any(start < other_end and other_start < end
               for other_start, other_end in spans):
            continue
        spans.append((start, end))

        directive = index.fragments.build(start, end)
        out.append({
            "quote": text,
            "event_type": event_type,
            "source": index.source_label(start),
            "doc_url": index.doc.url,
            "url": (append_fragment(index.doc.url, directive)
                    if directive else index.doc.url),
            "highlighted": bool(directive and index.doc.url),
        })
    return out
