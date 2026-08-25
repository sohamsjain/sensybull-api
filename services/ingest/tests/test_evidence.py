"""Supporting-quote verification and EDGAR deep links.

The promise this feature makes to a reader is narrow and absolute: the
quoted text is in the filing, and clicking it lands on those words. These
tests hold both halves of that promise.
"""

import re
from urllib.parse import unquote

import pytest

from evidence import SourceDoc, resolve_evidence
from parser import strip_html
from text_fragment import FragmentBuilder, append_fragment

DOC_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/a8k.htm"
EX_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000010/ex991.htm"

FILING_HTML = """
<html><body>
<p>Item 5.02 Departure of Directors or Certain Officers</p>
<p>On July 1, 2026, the Company announced the departure of Jane Roe, the
Company&rsquo;s Chief Executive Officer, effective June 30, 2026.</p>
<p>The Board appointed John Doe as interim Chief Executive Officer while it
conducts a search for a permanent successor.</p>
<p>Item 1.01 Entry into a Material Definitive Agreement</p>
<p>On July 1, 2026, the Company entered into an agreement to acquire Acme
Corp. for total consideration of $1.2&nbsp;billion in cash.</p>
</body></html>
"""


def _text(html: str) -> str:
    return strip_html(html, preserve_nbsp=True)


def _docs():
    return [SourceDoc(label="primary", url=DOC_URL, text=_text(FILING_HTML))]


def _directive(url: str) -> str:
    assert "#:~:" in url, url
    return url.split("#:~:", 1)[1]


class TestQuoteVerification:
    def test_exact_quote_is_kept_and_linked(self):
        [entry] = resolve_evidence(
            [("Leadership Change",
              "the Company announced the departure of Jane Roe")],
            _docs(),
        )
        assert entry["quote"] == "the Company announced the departure of Jane Roe"
        assert entry["highlighted"] is True
        assert entry["url"].startswith(DOC_URL + "#:~:text=")
        assert entry["source"] == "Item 5.02"
        assert entry["event_type"] == "Leadership Change"

    def test_stored_quote_is_the_filings_text_not_the_models(self):
        # A straight apostrophe and a dropped comma — the ordinary drift of a
        # model copying text. What ships is the filing's own characters.
        [entry] = resolve_evidence(
            [("", "the Company's Chief Executive Officer effective June 30 2026")],
            _docs(),
        )
        assert entry["quote"] == (
            "the Company’s Chief Executive Officer, effective June 30, 2026."
        )

    def test_fabricated_quote_is_dropped(self):
        assert resolve_evidence(
            [("Acquisition",
              "the Company confirmed it will pay a special dividend of $4 per share")],
            _docs(),
        ) == []

    def test_quote_too_short_to_prove_anything_is_dropped(self):
        assert resolve_evidence([("", "the Company")], _docs()) == []

    def test_quotes_are_capped_and_deduplicated(self):
        quote = "The Board appointed John Doe as interim Chief Executive Officer"
        entries = resolve_evidence(
            [("Leadership Change", quote),
             ("Leadership Change", quote + " while it conducts a search")],
            _docs(),
        )
        assert len(entries) == 1

    def test_quote_is_attributed_to_the_item_it_sits_in(self):
        [entry] = resolve_evidence(
            [("Acquisition", "entered into an agreement to acquire Acme Corp.")],
            _docs(),
        )
        assert entry["source"] == "Item 1.01"

    def test_exhibit_quote_links_to_the_exhibit(self):
        exhibit = "<html><body><p>Acme Corp. shareholders will receive $42.00 in " \
                  "cash for each share they hold.</p></body></html>"
        docs = _docs() + [SourceDoc(label="EX-99.1", url=EX_URL, text=_text(exhibit))]
        [entry] = resolve_evidence(
            [("Acquisition", "shareholders will receive $42.00 in cash for each share")],
            docs,
        )
        assert entry["source"] == "EX-99.1"
        assert entry["doc_url"] == EX_URL

    def test_no_source_document_means_no_evidence(self):
        assert resolve_evidence([("", "anything at all here")], []) == []

    def test_quote_survives_a_document_with_no_url(self):
        docs = [SourceDoc(label="primary", url="", text=_text(FILING_HTML))]
        [entry] = resolve_evidence(
            [("", "the Company announced the departure of Jane Roe")], docs)
        assert entry["url"] == ""
        assert entry["highlighted"] is False


class TestTextFragment:
    def _builder(self, text: str) -> FragmentBuilder:
        return FragmentBuilder(text)

    def _fragment_for(self, text: str, needle: str) -> str | None:
        start = text.index(needle)
        return self._builder(text).build(start, start + len(needle))

    def test_short_quote_uses_the_exact_form(self):
        text = "Alpha beta gamma.\nThe Company entered into a merger agreement today."
        frag = self._fragment_for(text, "The Company entered into a merger agreement today.")
        assert frag is not None
        assert "," not in frag  # single snippet, not a range
        assert unquote(frag[len("text="):]) == (
            "The Company entered into a merger agreement today.")

    def test_quote_crossing_a_block_uses_the_range_form(self):
        text = ("On July 1, 2026, the Company entered into a definitive merger "
                "agreement with Acme Corp.\n"
                "The transaction is expected to close in the fourth quarter of "
                "the current fiscal year.")
        frag = self._builder(text).build(0, len(text))
        assert frag is not None
        start, end = frag[len("text="):].split(",")
        assert unquote(start).startswith("On July 1, 2026,")
        assert unquote(end).endswith("current fiscal year.")

    def test_repeated_opening_words_do_not_land_on_the_wrong_passage(self):
        # Both paragraphs open identically; only the tail distinguishes them.
        opening = "On July 1, 2026, the Company announced that it"
        text = (f"{opening} completed a routine renewal of its credit facility.\n"
                f"{opening} entered into a definitive agreement to acquire Acme Corp.")
        needle = f"{opening} entered into a definitive agreement to acquire Acme Corp."
        frag = self._fragment_for(text, needle)
        assert frag is not None
        # Whatever form it picks, the browser's own search has to land on the
        # second paragraph — which is what build() simulates before emitting.
        builder = self._builder(text)
        snippets = [unquote(p) for p in frag[len("text="):].split(",")]
        span = builder._simulate(snippets[0], snippets[1] if len(snippets) > 1 else None)
        assert text[span[0]:span[1]] == needle

    def test_non_breaking_space_is_carried_into_the_directive(self):
        text = "The purchase price is $1.2 billion payable in cash at closing."
        frag = self._fragment_for(text, text)
        assert frag is not None
        assert "%C2%A0" in frag

    def test_hyphens_are_escaped_so_they_cannot_read_as_delimiters(self):
        text = "The notes bear interest at a fixed rate on a pay-in-kind basis annually."
        frag = self._fragment_for(text, text)
        assert frag is not None
        assert "-" not in frag
        assert "%2D" in frag

    def test_text_absent_from_the_document_has_no_fragment(self):
        builder = self._builder("Some short filing text about nothing much.")
        assert builder.build(5, 5) is None

    def test_directive_is_appended_after_an_existing_anchor(self):
        assert append_fragment("https://x/a.htm#top", "text=hello") == \
            "https://x/a.htm#top:~:text=hello"
        assert append_fragment("https://x/a.htm", "text=hello") == \
            "https://x/a.htm#:~:text=hello"
        assert append_fragment("", "text=hello") == ""


class TestEndToEndLink:
    """The link a reader clicks must highlight the quote we showed them."""

    @pytest.mark.parametrize("quote", [
        "the Company announced the departure of Jane Roe",
        "The Board appointed John Doe as interim Chief Executive Officer",
        "entered into an agreement to acquire Acme Corp. for total consideration",
    ])
    def test_directive_resolves_back_to_the_displayed_quote(self, quote):
        docs = _docs()
        [entry] = resolve_evidence([("", quote)], docs)
        builder = FragmentBuilder(docs[0].text)
        snippets = [unquote(p) for p in _directive(entry["url"])[len("text="):].split(",")]
        span = builder._simulate(snippets[0], snippets[1] if len(snippets) > 1 else None)
        highlighted = re.sub(r"\s+", " ", docs[0].text[span[0]:span[1]]).strip()
        assert highlighted == entry["quote"]
