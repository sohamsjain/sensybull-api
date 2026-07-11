"""
forms.py — the form-type registry: which SEC forms we ingest and how.

The registry is an exact-form whitelist. Anything the feeds return that is
not a key here is dropped — that is how amendments and prefix noise die.

July 2026 rollback: multi-form ingest (SC 13D/G stakes, tenders,
merger/contested proxies, delistings, NT late filings, Form 4 insider
buys) was removed wholesale — those pipelines were too hard to debug and
resolve. Only the 8-K family is ingested: decimal Item N.NN sections,
item-based tiers, filer-attributed. Don't re-add other forms without an
explicit product decision.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FormSpec:
    form: str      # exact EDGAR form type (registry key)
    llm_hint: str  # form-specific analyst guidance appended to the system prompt


_8K_HINT = "Focus on the reported Items; the filer is the subject company."

FORM_REGISTRY: dict[str, FormSpec] = {spec.form: spec for spec in [
    FormSpec("8-K", _8K_HINT),
    FormSpec("8-K/A",
             _8K_HINT + " This is an AMENDMENT to a previously filed 8-K. "
             "Describe only what THIS amendment states — many amendments "
             "merely refile or add an exhibit or correct an earlier item, "
             "and if so, say exactly that. NEVER reconstruct or guess the "
             "original transaction's terms; if this text does not restate "
             "them, omit them."),
]}


@dataclass(frozen=True)
class FeedQuery:
    type_param: str   # value for &type= — EDGAR prefix-matches this
    count: int = 40
    pages: int = 1    # start=0, count, 2*count, ...


# `type=` is PREFIX-matched by EDGAR, so the query can return forms outside
# the registry (8-K12B, 8-K15D5, ...) — the exact-form whitelist filter in
# main.py is what actually admits entries.
FEED_QUERIES: list[FeedQuery] = [
    FeedQuery("8-K"),
]


def get_spec(form_type: str) -> FormSpec | None:
    return FORM_REGISTRY.get(form_type)
