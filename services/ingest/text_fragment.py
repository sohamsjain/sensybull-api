"""
text_fragment.py — build W3C text-fragment directives (``#:~:text=...``).

A supporting quote is only proof if the reader can find it in the source
document. Browsers implement scroll-to-text-fragment: a URL whose fragment
ends in ``:~:text=<snippet>`` scrolls to the first match of <snippet> in the
rendered page and highlights it. The host does not have to cooperate, so
this works on sec.gov exactly as it works anywhere else, with no viewer,
proxy or copy of the filing on our side.

The rules we have to satisfy, from the spec, and what each one costs us:

* Matching runs over the *rendered* text, case-insensitively, with runs of
  collapsible whitespace treated as a single space. So the snippet has to
  be whitespace-normalized, and case is free.
* A single snippet may not span a block boundary. A quote that runs across
  paragraphs therefore has to use the range form ``text=<start>,<end>``:
  start and end are each block-contained, and the browser highlights
  everything between them. ``strip_html`` marks every block boundary with a
  newline, which is exactly the signal needed to pick block-safe snippets.
* A non-breaking space is *not* collapsible whitespace, so where the filing
  has one the snippet must carry it through verbatim (percent-encoded).
  That is why the evidence path strips HTML with ``preserve_nbsp=True``.
* The browser highlights the FIRST match in the document. A snippet that
  also occurs earlier lands the reader somewhere else, so every candidate
  is simulated against the document before it is emitted, and accepted only
  when the text the browser would highlight reads the same as the quote.

Nothing here talks to the network: it is a pure function of the document
text, so it is fully testable against fixtures.
"""

import re
from urllib.parse import quote as _urlquote

# A quote shorter than this is emitted whole (``text=<quote>``), which gives
# the browser the most to match on. Longer ones use the range form so the
# URL stays a link rather than a paragraph.
MAX_EXACT_CHARS = 300

# Words taken from each end of the quote in the range form. Start small —
# a short snippet is more robust to a stray character in the middle of the
# quote — and grow only when the shorter one resolves somewhere else.
MIN_SNIPPET_WORDS = 5
MAX_SNIPPET_WORDS = 24

# Refuse to emit a directive longer than this; past it the "link" is no
# longer something a person can copy, paste or eyeball.
MAX_DIRECTIVE_CHARS = 1_200


def _collapse(text: str) -> str:
    """Whitespace-normalize a snippet the way the matcher will.

    Only spaces and tabs collapse. U+00A0 is left alone: CSS does not treat
    it as collapsible whitespace, so the browser will insist on finding one.
    """
    return re.sub(r"[ \t]+", " ", text).strip()


def _fold(text: str) -> str:
    """Collapse + case-fold — the form two strings are compared in."""
    return _collapse(text).lower()


def _encode(snippet: str) -> str:
    """Percent-encode one snippet for use inside a text directive.

    ``-`` and ``,`` are the directive's own delimiters, so they can never
    survive raw; ``quote`` leaves ``-`` alone by default.
    """
    return _urlquote(snippet, safe="").replace("-", "%2D")


class _Block:
    """One block of document text, with a case-folded copy to search.

    ``offsets`` maps every index in the folded copy back to an index in the
    block's own text, with one extra entry at the end so a match's exclusive
    end index resolves too.
    """

    __slots__ = ("start", "folded", "offsets")

    def __init__(self, start: int, text: str) -> None:
        self.start = start
        chars: list[str] = []
        offsets: list[int] = []
        prev_space = False
        for i, ch in enumerate(text):
            if ch in " \t":
                if prev_space:
                    continue
                chars.append(" ")
                offsets.append(i)
                prev_space = True
                continue
            lowered = ch.lower()
            # A handful of characters case-fold to more than one character
            # (U+0130, ...). Keeping the original preserves the 1:1 index
            # map; matching just stays case-sensitive for that one character.
            chars.append(lowered if len(lowered) == 1 else ch)
            offsets.append(i)
            prev_space = False
        offsets.append(len(text))
        self.folded = "".join(chars)
        self.offsets = offsets

    def span(self, lo: int, hi: int) -> tuple[int, int]:
        """Document offsets for a match at [lo, hi) in the folded copy."""
        return self.start + self.offsets[lo], self.start + self.offsets[hi]


class FragmentBuilder:
    """Builds text-fragment directives against one document's plain text.

    Constructed once per source document and reused for every quote taken
    from it — the block index is the expensive part.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self._blocks: list[_Block] = []
        offset = 0
        for raw in text.split("\n"):
            if raw.strip():
                self._blocks.append(_Block(offset, raw))
            offset += len(raw) + 1  # the newline that split() removed

    # -- matching ---------------------------------------------------------

    def _matches(self, needle: str, after: int = 0):
        """Every block-contained match of a folded needle, in document order.

        ``after`` skips matches that start before that document offset, which
        is how the range form's end snippet is required to follow its start.
        """
        if not needle:
            return
        for block in self._blocks:
            pos = 0
            while True:
                found = block.folded.find(needle, pos)
                if found < 0:
                    break
                span = block.span(found, found + len(needle))
                if span[0] >= after:
                    yield span
                pos = found + 1

    def _simulate(self, start_snippet: str, end_snippet: str | None
                  ) -> tuple[int, int] | None:
        """What the browser would highlight for this directive, or None.

        Mirrors the spec's search: the first match of the start snippet, and
        for the range form the first match of the end snippet that begins at
        or after it.
        """
        first = next(self._matches(_fold(start_snippet)), None)
        if first is None:
            return None
        if end_snippet is None:
            return first
        tail = next(self._matches(_fold(end_snippet), after=first[1]), None)
        if tail is None:
            return None
        return first[0], tail[1]

    # -- building ---------------------------------------------------------

    def _words(self, start: int, end: int) -> list[tuple[int, int]]:
        """(start, end) offsets of the words in text[start:end].

        U+00A0 counts as part of a word, not as a separator: it has to travel
        into the snippet verbatim, so a run joined by one stays one token.
        """
        return [
            (start + m.start(), start + m.end())
            for m in re.finditer(r"[^ \t\n]+", self.text[start:end])
        ]

    def _candidates(self, start: int, end: int) -> list[tuple[str, str | None]]:
        quote = self.text[start:end]
        head_end = self.text.find("\n", start)
        if head_end < 0 or head_end > end:
            head_end = end
        tail_start = self.text.rfind("\n", start, end)
        tail_start = start if tail_start < 0 else tail_start + 1

        out: list[tuple[str, str | None]] = []
        if "\n" not in quote and len(quote) <= MAX_EXACT_CHARS:
            out.append((quote, None))

        head = self._words(start, head_end)
        tail = self._words(tail_start, end)
        one_block = head_end >= end
        for n in range(MIN_SNIPPET_WORDS, MAX_SNIPPET_WORDS + 1):
            if len(head) < n or len(tail) < n:
                break
            # Within a single block the two snippets must not overlap, or
            # the end snippet would match inside the start snippet.
            if one_block and len(head) < 2 * n:
                break
            out.append((
                self.text[head[0][0]:head[n - 1][1]],
                self.text[tail[-n][0]:tail[-1][1]],
            ))
        return out

    def build(self, start: int, end: int) -> str | None:
        """A directive that highlights text[start:end], or None if none fits.

        None is a real outcome, not a failure: the quote may be text the
        document repeats verbatim in a place we cannot distinguish, or too
        fragmented to snippet. Callers link to the document itself instead.
        """
        target = _fold(self.text[start:end])
        if not target:
            return None

        for start_snippet, end_snippet in self._candidates(start, end):
            if not _collapse(start_snippet):
                continue
            directive = "text=" + _encode(_collapse(start_snippet))
            if end_snippet is not None:
                directive += "," + _encode(_collapse(end_snippet))
            if len(directive) > MAX_DIRECTIVE_CHARS:
                continue
            span = self._simulate(start_snippet, end_snippet)
            # Landing on a different copy of the same sentence is still a
            # correct highlight; landing on different words is not.
            if span and _fold(self.text[span[0]:span[1]]) == target:
                return directive
        return None


def append_fragment(url: str, directive: str) -> str:
    """Attach a text directive to a document URL.

    ``:~:`` separates the fragment directive from any ordinary fragment, so
    a URL that already carries an anchor keeps it.
    """
    if not url or not directive:
        return url
    return f"{url}{'' if '#' in url else '#'}:~:{directive}"
