"""
forms.py — which SEC forms we ingest: the 8-K family only.

ALLOWED_FORMS is an exact-form whitelist. EDGAR's getcurrent feed
prefix-matches its &type= parameter, so a "8-K" query also returns noise
like 8-K12B — anything not in this set is dropped in main.py.

July 2026 rollback: multi-form ingest (SC 13D/G stakes, tenders,
merger/contested proxies, delistings, NT late filings, Form 4 insider
buys) was removed wholesale — those pipelines were too hard to debug and
resolve. Don't re-add forms without an explicit product decision.
"""

ALLOWED_FORMS = {"8-K", "8-K/A"}

_8K_HINT = "Focus on the reported Items; the filer is the subject company."

# Form-specific analyst guidance appended to the LLM system prompt.
LLM_HINTS: dict[str, str] = {
    "8-K": _8K_HINT,
    "8-K/A": _8K_HINT + " This is an AMENDMENT to a previously filed 8-K. "
            "Describe only what THIS amendment states — many amendments "
            "merely refile or add an exhibit or correct an earlier item, "
            "and if so, say exactly that. NEVER reconstruct or guess the "
            "original transaction's terms; if this text does not restate "
            "them, omit them.",
}
