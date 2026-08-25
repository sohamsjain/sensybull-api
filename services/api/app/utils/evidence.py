"""Validation for the `evidence` block on an incoming briefing.

Evidence is the one part of a briefing the reader is invited to click: each
entry is a quote from the source document plus a URL that opens that
document scrolled to the quote (see `services/ingest/evidence.py` for how
the quote is verified and the link built). Because it becomes an outbound
link in the UI, the API does not take ingest's word for its shape.

Validated once here, at the single point where events enter the database —
the Redis subscriber — so every reader of `briefing_json` sees the same
clean structure and no route has to defend itself.

What this enforces:

* structure — a list of objects with a non-empty string quote, capped in
  length and in count;
* destination — links must be https on an SEC host. A malformed or
  off-host URL costs the entry its link, not its quote: the words are
  still worth showing, and an unlinked quote is exactly what a filing
  whose passage could not be anchored produces anyway.
"""

from urllib.parse import urlsplit

# Mirrors evidence.MAX_EVIDENCE / MAX_QUOTE_CHARS on the ingest side. Kept
# a little looser than the producer so a legitimate change there does not
# need a simultaneous deploy here.
MAX_ENTRIES = 4
MAX_QUOTE_CHARS = 600
MAX_LABEL_CHARS = 64

# Documents live on www.sec.gov; the bare domain redirects there.
ALLOWED_HOSTS = frozenset({"www.sec.gov", "sec.gov"})


def _safe_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        return ""
    return value


def _text(value: object, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def sanitize_evidence(raw: object) -> list[dict]:
    """Return the entries fit to store, dropping anything malformed."""
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for entry in raw:
        if len(out) >= MAX_ENTRIES:
            break
        if not isinstance(entry, dict):
            continue
        quote = _text(entry.get("quote"), MAX_QUOTE_CHARS)
        if not quote:
            continue
        doc_url = _safe_url(entry.get("doc_url"))
        deep_link = _safe_url(entry.get("url"))
        out.append({
            "quote": quote,
            "event_type": _text(entry.get("event_type"), MAX_LABEL_CHARS),
            "source": _text(entry.get("source"), MAX_LABEL_CHARS),
            "doc_url": doc_url,
            # Falling back to the document is a better link than none, but
            # it no longer scrolls to the passage — so the flag that
            # promises it does has to fall with the deep link.
            "url": deep_link or doc_url,
            "highlighted": bool(entry.get("highlighted")) and bool(deep_link),
        })
    return out
