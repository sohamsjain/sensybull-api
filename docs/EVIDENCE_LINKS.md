# Supporting quotes and EDGAR deep links

Every briefing is written by a language model. Evidence is the part that
isn't: a short passage of the filing itself, shown as a quote, that opens
the source document on sec.gov scrolled to those words with them
highlighted. It exists to answer one question a reader is entitled to ask —
*where does it say that?* — in one click.

The mechanism follows the grounding recipe in Dolphin et al., *Grounded
Event Extraction from SEC 8-K Filings with a Fine-Grained Taxonomy*
(arXiv:2607.08346), and adds the link.

## The chain

| Stage | Code | What it guarantees |
| --- | --- | --- |
| Cite | `services/ingest/briefing.py` | The same briefing pass returns up to 3 quotes, each tagged with the event type it proves. No extra model call, no extra latency. |
| Verify | `services/ingest/evidence.py` | The quote is matched against the source document with overlapping 4-word shingles; ≥40% must appear verbatim. A fabricated quote cannot clear that floor. |
| Snap | `services/ingest/evidence.py` | The matching shingles pin the quote to a character span, and **that span** is what gets stored. The displayed quote is source text by construction, not by the model behaving. |
| Link | `services/ingest/text_fragment.py` | The span becomes a [text fragment](https://developer.mozilla.org/docs/Web/URI/Fragment/Text_fragments) on the document's own URL. |
| Guard | `services/api/app/utils/evidence.py` | On write, an entry whose link is not `https` on an SEC host keeps its quote and loses its link. |

Why fuzzy matching rather than exact: models re-wrap whitespace, straighten
a curly apostrophe, and drop a trailing comma constantly while copying.
Exact matching would reject good quotes at a high rate; 40% of 4-word
shingles is far above anything unrelated text reaches, so it buys tolerance
without buying fabrications.

Why we drop rather than retry: the paper feeds a failed quote back to the
model for up to three attempts. Our briefing is one pass against a tight
per-minute token budget (see `_TOTAL_TEXT_CAP` in `briefing.py`), and
dropping an unverifiable quote already delivers the guarantee that matters —
nothing unverified is ever shown. It costs coverage, not trust.

## What makes a fragment hold

`text_fragment.py` builds a directive and then **simulates the browser's own
search against the document** before emitting it, accepting the candidate
only if the text the browser would highlight reads the same as the quote.
Three properties of the spec drive the design:

- **Snippets cannot cross a block boundary.** A quote spanning paragraphs
  uses the range form `text=<start>,<end>`. `strip_html` writes one `\n` per
  block, which is exactly the boundary signal needed.
- **A non-breaking space is not collapsible whitespace.** EDGAR documents
  are full of them (`Maria&nbsp;Delgado`), so the evidence path strips HTML
  with `preserve_nbsp=True` and the directive carries `%C2%A0` through.
- **The browser highlights the first match.** A snippet that also occurs
  earlier lands the reader somewhere else, which the simulation catches; the
  builder then tries a longer snippet.

## Degradation

Nothing here can leave a reader stranded:

- No unambiguous fragment → `highlighted: false`, and `url` opens the
  document itself. Still better than the event's `edgar_url`, which is the
  EDGAR **index page**.
- No verified quote at all → no panel. The briefing publishes unchanged.
- A browser without text-fragment support (Firefox < 131, Safari < 16.1,
  some in-app webviews) → the fragment is ignored and the document opens at
  the top. The quote is on screen either way, so the reader can still find
  it with ⌘F.

## Alternatives considered

- **Serving our own highlighted copy of the filing.** Full control and works
  in every browser, but it means re-hosting SEC documents, rewriting their
  relative asset URLs, and defending a proxy. Text fragments need none of
  that and keep sec.gov as the thing the reader is actually looking at,
  which is the whole point of the feature.
- **Character offsets into the raw HTML.** Needed for our own viewer;
  useless for a link to someone else's page. Skipped until there is a
  viewer to need it.
