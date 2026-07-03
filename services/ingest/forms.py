"""
forms.py — the form-type registry: which SEC forms we ingest and how.

The registry is an exact-form whitelist. Anything the feeds return that is
not a key here is dropped — that is how amendments and routine variants
(SC 13G/A, S-4/A, SC TO-C, DEF 14A, 425, prefix noise like 424B5) die.
"Less noise, more signal": every form in this table earned its place.

Strategies:
- "8k_items"        decimal Item N.NN sections, item-based tiers (8-K family)
- "ownership_items" integer Item 1-7 sections (SC 13D/G), form-level tier
- "document"        no item structure; a bounded excerpt of the document
                    goes to the LLM (tenders, proxies, S-4, NT, 25, 15, CB)
- "form4_xml"       structured XML, no LLM; programmatic briefing (form4.py)
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class FormSpec:
    form: str                # exact EDGAR form type (registry key)
    tier: int                # form-level tier → max_tier (8-K family: item-based instead)
    category: str            # short label shown as Item.category / UI tag
    strategy: str            # "8k_items" | "ownership_items" | "document" | "form4_xml"
    subject_from: str        # "filer" | "feed_subject" (event is about a subject company)
    llm: bool                # generate an LLM briefing
    llm_hint: str            # form-specific analyst guidance appended to the system prompt
    default_event_type: str  # fallback classification when the LLM call fails


_8K_HINT = "Focus on the reported Items; the filer is the subject company."

FORM_REGISTRY: dict[str, FormSpec] = {spec.form: spec for spec in [
    # ── 8-K family (tier comes from items, not the form) ────────────────
    FormSpec("8-K",      3, "", "8k_items", "filer", True, _8K_HINT, "Other"),
    FormSpec("8-K/A",    3, "", "8k_items", "filer", True, _8K_HINT, "Other"),
    FormSpec("8-K12B",   3, "", "8k_items", "filer", True, _8K_HINT, "Other"),
    FormSpec("8-K12G3",  3, "", "8k_items", "filer", True, _8K_HINT, "Other"),
    FormSpec("8-K15D5",  3, "", "8k_items", "filer", True, _8K_HINT, "Other"),

    # ── Ownership stakes ─────────────────────────────────────────────────
    FormSpec("SC 13D", 1, "Activist Stake", "ownership_items", "feed_subject", True,
             "An investor crossed 5% with possible activist intent. Name the reporting "
             "person (the filer), stake size and percent from Item 5, cost basis from "
             "Item 3, and lead with Item 4 'Purpose of Transaction' — board seats, "
             "strategic alternatives, M&A intent. Everything is about the SUBJECT "
             "company's stock, not the filer's.",
             "Activist Initial"),
    FormSpec("SC 13D/A", 2, "Stake Amended", "ownership_items", "feed_subject", True,
             "Amendment to an existing 13D. Focus on what CHANGED: stake raised or cut, "
             "new intent language in Item 4, new agreements in Item 6. A stake cut below "
             "5% or dropped activist language is negative signal for the thesis.",
             "Activist Initial"),
    FormSpec("SC 13G", 3, "Passive Stake", "ownership_items", "feed_subject", True,
             "A passive investor crossed 5%. Name the holder and stake size. Passive "
             "stakes are low-signal unless the holder is a known activist or the "
             "company is small enough that the position is unusual.",
             "Other"),

    # ── Tenders / going-private ──────────────────────────────────────────
    FormSpec("SC TO-T", 1, "Tender Offer", "document", "feed_subject", True,
             "Third-party tender offer. Name the bidder, offer price and premium to "
             "market, consideration type, minimum-tender and financing conditions, and "
             "the expiration date (key catalyst).",
             "Tender Offer"),
    FormSpec("SC TO-I", 1, "Issuer Tender", "document", "filer", True,
             "Issuer self-tender / dutch auction. Extract the price or price range, "
             "dollar size, percent of shares outstanding, and expiration date.",
             "Issuer Tender"),
    FormSpec("SC 14D9", 2, "Tender Response", "document", "feed_subject", True,
             "Target board's response to a tender offer. Lead with the recommendation "
             "(accept, reject, neutral), fairness-opinion providers, and any go-shop "
             "or competing-bid language.",
             "Tender Offer"),
    FormSpec("SC 13E3", 1, "Going-Private", "document", "feed_subject", True,
             "Going-private transaction. Identify the acquiring insiders or sponsor, "
             "price and premium, special-committee process, and the required vote — "
             "the key catalyst.",
             "Going-Private"),

    # ── Mergers / proxies (merger + contested only; routine DEF 14A dropped)
    FormSpec("S-4", 2, "Merger Registration", "document", "filer", True,
             "Merger/exchange registration statement. Extract acquirer and target, "
             "exchange ratio or cash/stock mix, implied value, and expected close.",
             "M&A / Merger"),
    FormSpec("PREM14A", 1, "Merger Proxy (Prelim)", "document", "filer", True,
             "Preliminary merger proxy — often the first full look at deal terms. "
             "Extract the deal terms, shareholder vote date (key catalyst), "
             "termination fee, and any dissent/appraisal language.",
             "M&A / Merger"),
    FormSpec("DEFM14A", 2, "Merger Proxy", "document", "filer", True,
             "Definitive merger proxy. Extract deal terms, the shareholder vote date "
             "(key catalyst), termination fee, and any dissent/appraisal language.",
             "M&A / Merger"),
    FormSpec("PREC14A", 1, "Contested Proxy (Prelim)", "document", "filer", True,
             "Contested proxy fight. Identify dissident and management sides, board "
             "seats sought, dissident demands, and the meeting date (key catalyst).",
             "Activist Proxy"),
    FormSpec("DEFC14A", 1, "Contested Proxy", "document", "filer", True,
             "Contested proxy fight. Identify dissident and management sides, board "
             "seats sought, dissident demands, and the meeting date (key catalyst).",
             "Activist Proxy"),
    FormSpec("DFAN14A", 2, "Proxy Solicitation", "document", "feed_subject", True,
             "Proxy-fight solicitation material filed by a non-management party. State "
             "which side filed it, what they are asking shareholders to do, and any "
             "new arguments or nominees disclosed.",
             "Activist Proxy"),

    # ── Delisting / deregistration / distress ────────────────────────────
    FormSpec("25", 1, "Delisting", "document", "feed_subject", True,
             "Exchange delisting notice. State the exchange, whether removal is "
             "voluntary or for deficiency, and where the shares will trade next.",
             "Delisting"),
    FormSpec("25-NSE", 1, "Delisting", "document", "feed_subject", True,
             "Delisting notice filed by the exchange itself — usually involuntary "
             "(deficiency or non-compliance), a negative signal. State the exchange, "
             "the stated reason, and where the shares will trade next.",
             "Delisting"),
    FormSpec("15-12B", 1, "Deregistration", "document", "filer", True,
             "Deregistration / going dark: SEC reporting will cease. Flag liquidity "
             "loss and the information vacuum for minority holders.",
             "Going Dark"),
    FormSpec("15-12G", 1, "Deregistration", "document", "filer", True,
             "Deregistration / going dark: SEC reporting will cease. Flag liquidity "
             "loss and the information vacuum for minority holders.",
             "Going Dark"),
    FormSpec("15F-12B", 1, "Deregistration", "document", "filer", True,
             "Foreign private issuer deregistering from SEC reporting. Flag liquidity "
             "loss and the information vacuum for US holders.",
             "Going Dark"),
    FormSpec("NT 10-K", 1, "Late Annual Report", "document", "filer", True,
             "Late-filing notice — a distress signal. Extract the stated reason "
             "(restatement, auditor issues, going-concern, internal investigation) and "
             "the extended deadline (catalyst). Treat vague reasons as a red flag.",
             "Late Filing"),
    FormSpec("NT 10-Q", 2, "Late Quarterly Report", "document", "filer", True,
             "Late-filing notice — a distress signal. Extract the stated reason "
             "(restatement, auditor issues, going-concern, internal investigation) and "
             "the extended deadline (catalyst). Treat vague reasons as a red flag.",
             "Late Filing"),
    FormSpec("CB", 2, "Foreign Tender", "document", "feed_subject", True,
             "Foreign tender/rights offer notification. Identify the offeror, subject "
             "security, terms, and the deadline (catalyst).",
             "Tender Offer"),

    # ── Insider buying (no LLM — parsed from XML, see form4.py) ──────────
    FormSpec("4", 2, "Insider Buy", "form4_xml", "feed_subject", False, "",
             "Insider Buying"),
]}

# Forms where only the initial filing is signal — amendments are dropped even
# though the feed query's prefix matching returns them.
# (SC 13D/A is deliberately NOT here: 13D amendments carry stake changes.)
# Handled implicitly: "SC 13G/A", "S-4/A", "4/A" etc. are simply not registry keys.


@dataclass(frozen=True)
class FeedQuery:
    type_param: str   # value for &type= — EDGAR prefix-matches this
    count: int = 40
    pages: int = 1    # start=0, count, 2*count, ...


# One getcurrent query per form family. `type=` is PREFIX-matched by EDGAR,
# so a query can return forms outside the registry (e.g. type=4 also matches
# 424B5, type=25 matches 253G2) — the exact-form whitelist filter in main.py
# is what actually admits entries.
FEED_QUERIES: list[FeedQuery] = [
    FeedQuery("8-K"),
    FeedQuery("SC 13D"),           # also returns SC 13D/A — both wanted
    FeedQuery("SC 13G", count=100),  # /A flood filtered out; big page so initials survive
    FeedQuery("SC TO"),            # TO-T + TO-I wanted; TO-C filtered
    FeedQuery("SC 14D9"),
    FeedQuery("SC 13E3"),
    FeedQuery("S-4"),              # S-4/A, S-4MEF, S-4 POS filtered
    FeedQuery("PREM14A"),
    FeedQuery("DEFM14A"),
    FeedQuery("PREC14A"),
    FeedQuery("DEFC14A"),
    FeedQuery("DFAN14A"),
    FeedQuery("25"),               # 253G* filtered
    FeedQuery("15"),               # 15-15D etc. filtered
    FeedQuery("NT 10", count=100),  # deadline-day bursts
    FeedQuery("CB"),
    FeedQuery("4", count=100, pages=3),  # ~2k/day; 4/A, 40-F, 424B* filtered
]

# Map each feed query to the registry forms it can yield, so disabling forms
# via INGEST_FORMS also drops the now-pointless feed queries.
_QUERY_FORMS: dict[str, list[str]] = {
    q.type_param: [f for f in FORM_REGISTRY if f.startswith(q.type_param)]
    for q in FEED_QUERIES
}


def enabled_forms() -> set[str]:
    """Registry forms enabled for ingestion.

    INGEST_FORMS (comma-separated exact form names) restricts the set for
    staged rollout; unset or empty means all registry forms.
    """
    raw = os.environ.get("INGEST_FORMS", "").strip()
    if not raw:
        return set(FORM_REGISTRY)
    requested = {f.strip() for f in raw.split(",") if f.strip()}
    return requested & set(FORM_REGISTRY)


def get_spec(form_type: str) -> FormSpec | None:
    return FORM_REGISTRY.get(form_type)


def active_feed_queries() -> list[FeedQuery]:
    """Feed queries that can still yield at least one enabled form."""
    enabled = enabled_forms()
    return [
        q for q in FEED_QUERIES
        if any(f in enabled for f in _QUERY_FORMS[q.type_param])
    ]
