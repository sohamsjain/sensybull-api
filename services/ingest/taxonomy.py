"""
taxonomy.py — the three-tier event taxonomy used to classify filings.

The taxonomy has three tiers: a `primary` bucket (8 of them), a `secondary`
grouping inside it, and a `tertiary` leaf — the ~120 specific event kinds an
8-K or press release can report ("ceo_departure", "covenant_violation",
"clinical_trial_results").

Only the LEAF is asked of the LLM. Naming the exact event is a far easier
classification problem than picking one of a dozen broad buckets: the leaves
are mutually exclusive and their names carry their own definition, so the
model doesn't have to guess where we'd file it.

**The end user never sees the three tiers.** The product surface is one
simple category per event — the display label of the leaf's PRIMARY bucket
(see `CATEGORIES`). That is what `briefing.primary_event_type`,
`event_types`, the feed's filter chips, and every alert channel carry. The
leaf classification is kept alongside it (`briefing.taxonomy`) for analytics
and future routing, never for display.

Adding a leaf is cheap and does not change the product surface. Adding a
PRIMARY bucket adds a filter chip — that's a product decision.
"""

from dataclasses import dataclass

TAXONOMY_VERSION = "1.0"

# ── Tier 1: the simple, user-facing categories ───────────────────────────
# slug → display label. This mapping IS the product surface: the whole
# taxonomy collapses onto these labels plus "Other".
PRIMARY_LABELS: dict[str, str] = {
    "leadership_and_governance": "Leadership & Governance",
    "financial_results":         "Financial Results",
    "strategic_transactions":    "Strategic Transactions",
    "capital_and_financing":     "Capital & Financing",
    "operations_and_strategy":   "Operations & Strategy",
    "risk_events":               "Risk Events",
    "regulatory_and_compliance": "Regulatory & Compliance",
    "shareholder_activity":      "Shareholder Activity",
}

# Fallback label for anything unclassifiable. Not a taxonomy bucket — it
# has no leaves — but it is a real value on the wire and in the DB.
OTHER = "Other"

# The canonical display list, in the order the frontend renders its chips.
CATEGORIES: list[str] = [*PRIMARY_LABELS.values(), OTHER]

# ── Tiers 2 and 3 ────────────────────────────────────────────────────────
# primary slug → secondary slug → {tertiary slug: description}.
# Descriptions are the definition of record; most are NOT sent to the model
# (see PROMPT_BLOCK) because the leaf names already say what they mean.
_TAXONOMY: dict[str, dict[str, dict[str, str]]] = {
    "leadership_and_governance": {
        "executive_leadership": {
            "ceo_appointment": "New CEO appointment with background, employment terms, and compensation.",
            "ceo_departure": "CEO departure, resignation, retirement, termination, or death with circumstances and transition.",
            "cfo_appointment": "New CFO appointment with background, terms, and transition timeline.",
            "cfo_departure": "CFO departure with circumstances and transition arrangements.",
            "executive_officer_appointment": "Other named executive officer appointment with position and terms.",
            "executive_officer_departure": "Other named executive officer departure with circumstances.",
            "executive_compensation_change": "Material executive or director compensation changes including new employment agreements, amendments, severance, change-in-control provisions, or equity awards. Consolidates all compensation-related events into a single tag.",
        },
        "board_of_directors": {
            "director_appointment": "New director election or appointment with background, qualifications, and committee assignments.",
            "director_departure": "Director resignation, removal, retirement, or decision not to stand for re-election, including any disagreements causing departure.",
        },
        "corporate_control": {
            "control_acquisition": "Acquisition of controlling interest by new shareholder, investor group, or entity.",
            "control_disposition": "Loss of controlling interest through sale, dilution, or other transaction.",
            "going_private_transaction": "Going-private, management buyout, or similar transaction ending public trading.",
            "reverse_merger": "Reverse merger where private operating company merges with public shell, becoming publicly traded. Includes shell company status changes.",
        },
        "governance_documents": {
            "charter_amendment": "Certificate or articles of incorporation amendment, including changes to shareholder voting, dividend, or liquidation rights. Use this for the governance action; use preferred_stock_modification for preferred-specific term changes.",
            "bylaw_amendment": "Bylaw amendment with nature of changes and impact on governance or shareholder rights.",
            "code_of_ethics_change": "Code of ethics amendment or waiver for principal executive, financial, or accounting officers.",
            "shareholder_rights_plan": "Adoption, amendment, or termination of a shareholder rights plan (poison pill) or similar anti-takeover mechanism.",
            "preferred_stock_modification": "Material modification of preferred stock terms including conversion, dividend, redemption, or voting provisions.",
            "fiscal_year_change": "Fiscal year end change with old and new dates, transition period, and rationale.",
        },
    },
    "financial_results": {
        "earnings_and_performance": {
            "quarterly_earnings": "Quarterly financial results including revenue, net income, EPS, and key operating metrics with management commentary.",
            "annual_earnings": "Annual financial results with year-over-year comparisons, key metrics, and forward outlook.",
            "preliminary_results": "Preliminary or unaudited financial results before formal statement preparation.",
            "nav_per_share": "Net asset value per share for BDCs, closed-end funds, interval funds, or non-traded REITs.",
        },
        "guidance_and_outlook": {
            "guidance_issuance_or_update": "Issuance, revision, or reaffirmation of financial guidance including revenue projections, earnings estimates, and key metric forecasts. Consolidates all guidance-related disclosures regardless of disclosure mechanism.",
            "guidance_withdrawal": "Withdrawal or suspension of previously issued financial guidance.",
        },
        "impairments_and_charges": {
            "goodwill_impairment": "Goodwill impairment charge when carrying value exceeds fair value, with amount and affected reporting unit.",
            "asset_impairment": "Long-lived or intangible asset impairment or write-down with asset nature and circumstances.",
            "investment_impairment": "Other-than-temporary impairment on investments or securities with amount and nature.",
            "material_charge_or_gain": "Material nonrecurring charges, gains, or unusual items significantly affecting results (e.g., restructuring charges, legal settlements, one-time gains).",
        },
        "financial_integrity": {
            "financial_restatement": "Prior financial statements should not be relied upon due to material errors requiring restatement.",
            "accounting_error_correction": "Material accounting error correction not requiring full restatement but requiring disclosure.",
            "audit_opinion_withdrawal": "Withdrawal of auditor opinion on prior statements requiring reissuance.",
            "internal_control_weakness": "Material weakness in internal control over financial reporting requiring disclosure and remediation.",
        },
    },
    "strategic_transactions": {
        "deal_agreements": {
            "acquisition_agreement": "Definitive agreement to acquire a business, subsidiary, or division, specifying price, financing, and conditions.",
            "merger_agreement": "Definitive merger or business combination agreement with exchange ratios and terms.",
            "divestiture_agreement": "Definitive agreement to sell a business unit, subsidiary, or product line.",
            "joint_venture_agreement": "Joint venture, partnership, or collaboration agreement defining shared activities and governance.",
            "licensing_agreement": "Licensing or technology transfer agreement for patents, trademarks, or proprietary technology.",
        },
        "deal_completions": {
            "acquisition_completion": "Completed business acquisition with final purchase price, financing, and integration plans.",
            "merger_completion": "Completed merger or business combination with resulting ownership structure.",
            "divestiture_completion": "Completed sale or divestiture with proceeds, gain/loss, and strategic rationale.",
            "spinoff_completion": "Completed spinoff, split-off, or separation creating independent entity with distribution details.",
            "asset_purchase_or_sale": "Completed purchase or sale of significant individual assets (real estate, equipment, IP) outside ordinary course.",
        },
        "deal_terminations": {
            "deal_termination": "Termination, expiration, or non-renewal of a material agreement due to term completion, mutual agreement, or exercise of termination rights.",
            "deal_breach_default": "Termination of a material agreement due to breach or default, with associated penalties or disputes.",
            "deal_withdrawal": "Withdrawal from or cancellation of a pending agreement before completion, with rationale and any fees.",
        },
    },
    "capital_and_financing": {
        "debt_activity": {
            "debt_issuance": "Bonds, notes, or debentures issuance with principal, rate, maturity, covenants, and use of proceeds.",
            "credit_facility": "Credit agreement, loan facility, or revolving credit arrangement. Includes amendments modifying capacity, rates, covenants, or maturity.",
            "credit_facility_draw": "Borrowing under existing credit facilities or term loans with amount and purpose.",
            "debt_retirement": "Purchase, redemption, tender, or early retirement of outstanding debt securities.",
            "guarantee_or_letter_of_credit": "Issuance or release of guarantees, letters of credit, or keepwell agreements.",
            "off_balance_sheet_arrangement": "Off-balance sheet obligations including VIEs, synthetic leases, or structured finance with real exposure.",
        },
        "debt_distress": {
            "debt_acceleration": "Events triggering debt acceleration, interest rate increases, or debt term modifications.",
            "covenant_violation": "Breach or anticipated breach of financial covenants, with waivers obtained or amendments to cure.",
            "payment_default": "Actual failure to make required principal or interest payment on outstanding debt.",
            "rating_downgrade_trigger": "Credit rating downgrade triggering contractual consequences (increased rates, collateral, acceleration).",
        },
        "equity_activity": {
            "public_offering": "Public equity offering or registered direct offering with underwriting terms.",
            "private_placement": "Private placement of unregistered equity to accredited investors or QIBs, with terms and registration rights.",
            "pipe_transaction": "PIPE transaction involving unregistered securities at market discount with registration rights.",
            "warrant_or_conversion": "Unregistered securities from warrant/option exercise or convertible security conversion.",
            "equity_compensation_grant": "Unregistered securities under equity compensation plans for employees, directors, or consultants.",
            "acquisition_consideration_shares": "Unregistered securities issued as merger or acquisition consideration.",
            "rights_offering": "Rights offering to existing shareholders to purchase additional shares at a discount.",
            "underwriting_agreement": "Underwriting or placement agent agreement for securities distribution in offerings.",
        },
        "shareholder_returns": {
            "dividend_declaration": "Cash dividend, special dividend, or distribution declaration with amount, record date, and payment date.",
            "dividend_policy_change": "Material change to dividend policy including suspension, reduction, or significant increase.",
            "share_repurchase_program": "Share buyback program authorization, expansion, or update with amount and timing.",
            "tender_offer": "Tender offer to acquire outstanding securities (equity, debt, or trust preferred) with pricing and conditions.",
            "stock_split": "Forward or reverse stock split with ratio, dates, and share impact.",
        },
        "credit_ratings": {
            "credit_rating_change": "Credit rating upgrade, downgrade, outlook modification, or watch placement by rating agencies.",
        },
    },
    "operations_and_strategy": {
        "restructuring": {
            "restructuring_plan": "Restructuring or cost reduction initiative with expected charges, timeline, and anticipated savings.",
            "facility_closure": "Facility closures, consolidations, or relocations with locations, employees, and charges.",
            "workforce_reduction": "Significant layoffs, RIFs, or voluntary separation programs with positions, severance, and timeline.",
            "business_line_exit": "Decision to exit or discontinue a business line, product category, or market segment.",
        },
        "business_developments": {
            "significant_contract_award": "Material contract win or commercial agreement outside ordinary course affecting future revenues.",
            "product_or_service_launch": "Significant new product, service offering, or technology development affecting future performance.",
            "clinical_trial_results": "Clinical trial data for pharma, biotech, or medical devices including efficacy and safety findings.",
            "patent_milestone": "Patent grants, issuances, or significant IP milestones from USPTO or international offices.",
            "regulatory_decision": "Regulatory approvals, rejections, complete response letters, or information requests from FDA, EPA, or other agencies.",
            "strategic_initiative": "Strategic plans, business transformation, or market entry/exit that represent material changes to business direction.",
            "esg_sustainability_commitment": "Material ESG initiative, sustainability target, or environmental/social development the company considers significant.",
        },
        "material_agreements": {
            "supply_or_distribution_agreement": "Material supply, distribution, or purchasing commitment establishing long-term obligations.",
            "lease_agreement": "Significant lease for real property, equipment, or facilities including operating, finance, and sale-leaseback.",
            "settlement_agreement": "Settlement resolving material litigation, regulatory proceedings, or disputes. Use this instead of material_litigation when the event is the settlement itself.",
            "partnership_or_collaboration": "Partnership, collaboration, or commercial relationship agreement affecting operations or competitive position.",
        },
    },
    "risk_events": {
        "legal_proceedings": {
            "material_litigation": "Significant litigation updates, outcomes, or judgments not covered by settlement_agreement.",
            "class_action_filing": "Class action lawsuit filed against the company with claims and potential exposure.",
            "regulatory_investigation": "SEC, DOJ, or other regulatory investigation, Wells notice, or enforcement action.",
        },
        "bankruptcy_and_insolvency": {
            "voluntary_bankruptcy": "Voluntary bankruptcy filing (Chapter 7, 11, 15) with circumstances and expected impact.",
            "involuntary_bankruptcy": "Involuntary bankruptcy petition by creditors with petitioner identity and amounts.",
            "receivership_appointment": "Appointment of receiver, trustee, or conservator to control assets with scope and duration.",
            "bankruptcy_emergence": "Confirmation of reorganization plan or emergence from bankruptcy with creditor treatment and new capital structure.",
            "going_concern": "Going concern doubt expressed by auditors or management, substantial doubt about ability to continue operations.",
        },
        "security_and_safety": {
            "cybersecurity_incident": "Material cybersecurity incident, data breach, ransomware, or information security event affecting systems or data.",
            "natural_disaster_impact": "Natural disaster, catastrophic event, or force majeure impact on operations, facilities, or financial condition.",
            "mine_safety_violation": "Mine safety violations, citations, or penalties from MSHA requiring corrective action or fines.",
            "mine_closure": "Mining operations closure due to safety concerns or violation patterns with production impact.",
        },
    },
    "regulatory_and_compliance": {
        "auditor_matters": {
            "auditor_resignation": "Independent auditor resignation with date and any disagreements.",
            "auditor_dismissal": "Independent auditor dismissal with reasons and reportable events.",
            "new_auditor_engagement": "New auditor engagement with prior consultations and change disclosures.",
            "auditor_disagreement": "Disagreements with auditors on accounting principles, disclosures, or audit scope.",
        },
        "exchange_listing": {
            "listing_deficiency_notice": "Notice of failure to satisfy continued listing requirements with cure period.",
            "listing_compliance_regained": "Regained listing compliance after previous deficiency notice.",
            "delisting_determination": "Exchange determination to delist securities with effective date and reasons.",
            "listing_transfer": "Transfer between exchanges or market tiers, or initial listing approval.",
            "voluntary_delisting": "Voluntary decision to delist with rationale and alternative trading arrangements.",
        },
        "asset_backed_securities": {
            "abs_structural_change": "Material changes to ABS structure including servicer/trustee replacement, credit enhancement modification, or trust document amendments.",
            "abs_distribution_failure": "Failure to make required ABS distribution with missed amount, reasons, and expected remediation.",
        },
    },
    "shareholder_activity": {
        "shareholder_meetings": {
            "shareholder_meeting_notice": "Annual or special meeting announcement with dates, agenda, and record date.",
            "annual_meeting_results": "Annual meeting results including elections, ratifications, and proposal outcomes.",
            "special_meeting_results": "Special meeting results on mergers, charter amendments, or other significant actions.",
        },
        "shareholder_activism": {
            "activist_investor_campaign": "Activist engagement including demands, proxy contests, settlements, or board nominations.",
            "shareholder_proposal_outcome": "Shareholder proposal outcomes with support levels and management response.",
            "director_nomination": "Shareholder director nominations under proxy access or advance notice bylaws.",
        },
        "investor_communications": {
            "investor_presentation": "Investor presentations, conference materials, or earnings call supplements disclosed for broad dissemination.",
            "business_update": "Business performance, market conditions, or operational updates communicated to investors.",
            "capital_allocation_update": "Capital allocation strategy, financial plan, or investment priorities communicated to investment community.",
        },
        "insider_trading": {
            "trading_plan_10b5_1": "Rule 10b5-1 trading plan adoption, modification, or termination by officers, directors, or affiliates.",
            "benefit_plan_blackout": "Blackout period notice during which benefit plan trading will be suspended.",
        },
    },
}


@dataclass(frozen=True)
class Category:
    """One leaf of the taxonomy, with the tiers it hangs under."""
    tertiary: str
    secondary: str
    primary: str
    description: str

    @property
    def label(self) -> str:
        """The simple, user-facing category this leaf collapses to."""
        return PRIMARY_LABELS[self.primary]


BY_TERTIARY: dict[str, Category] = {
    tertiary: Category(tertiary, secondary, primary, description)
    for primary, secondaries in _TAXONOMY.items()
    for secondary, leaves in secondaries.items()
    for tertiary, description in leaves.items()
}


def display_category(tertiary: str) -> str:
    """The user-facing label for a leaf, or "Other" if it isn't one."""
    node = BY_TERTIARY.get((tertiary or "").strip().lower())
    return node.label if node else OTHER


def validate_tertiaries(raw: object, limit: int = 3) -> list[str]:
    """Keep only real leaf slugs from a model answer, deduped and capped."""
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            continue
        slug = value.strip().lower()
        if slug in BY_TERTIARY and slug not in out:
            out.append(slug)
    return out[:limit]


def to_labels(tertiaries: list[str], limit: int = 3) -> list[str]:
    """Collapse leaves to their display categories, order-preserving.

    Several leaves routinely share one bucket (a merger agreement plus the
    financing that funds it), so the label list is usually shorter than the
    leaf list — that is the point: one simple category, not three tiers.
    """
    out: list[str] = []
    for tertiary in tertiaries:
        label = display_category(tertiary)
        if label not in out:
            out.append(label)
    return out[:limit] or [OTHER]


# ── Prompt rendering ─────────────────────────────────────────────────────
# The leaf list goes into every briefing prompt, so it is rendered once at
# import and kept tight: grouped slugs, no descriptions. The whole prompt
# has to fit under the per-minute token ceiling alongside the filing text
# (see briefing._TOTAL_TEXT_CAP) — the full block with descriptions would
# be ~4x this size and crowd out the filing itself. The slugs are chosen to
# be self-defining; only genuinely ambiguous pairs get a hint below.

def _render_prompt_block() -> str:
    lines: list[str] = []
    for primary, secondaries in _TAXONOMY.items():
        lines.append(f"{PRIMARY_LABELS[primary].upper()}")
        for secondary, leaves in secondaries.items():
            lines.append(f"  {secondary}: " + " | ".join(leaves))
    return "\n".join(lines)


PROMPT_BLOCK = _render_prompt_block()

# Rules the leaf names alone don't settle. Each one is a boundary the model
# gets wrong without being told (usually because two leaves in different
# buckets could both plausibly fit, or because one leaf deliberately
# consolidates events that sound like several).
PROMPT_HINTS = """\
- executive_compensation_change covers ALL comp events — new or amended
  employment agreements, severance, change-in-control terms, equity awards.
- guidance_issuance_or_update covers issuing, revising AND reaffirming
  guidance; only a withdrawal is guidance_withdrawal.
- credit_facility covers amendments to an existing facility; a draw on one
  is credit_facility_draw.
- settlement_agreement when the event IS the settlement;
  material_litigation for any other litigation development.
- charter_amendment for the governance action;
  preferred_stock_modification when preferred terms themselves change.
- reverse_merger also covers a shell company status change.
- deal_termination for a normal end (term, mutual, termination right),
  deal_breach_default when it ends in breach, deal_withdrawal when a
  PENDING deal is called off.
- material_charge_or_gain for one-off charges or gains that aren't an
  impairment (restructuring charges, legal settlements, windfalls)."""


# ── Deterministic 8-K item → category (facts-only briefings) ─────────────
# When the LLM never runs, there is no leaf classification to make — the
# SEC item number is all we know, so it maps straight to the simple
# category. Items 7.01 (Reg FD) and 8.01 (Other Events) are deliberately
# absent: they are catch-alls that say nothing about what happened.
ITEM_CATEGORIES: dict[str, str] = {
    "1.01": "Strategic Transactions",
    "1.02": "Strategic Transactions",
    "1.03": "Risk Events",
    "1.04": "Risk Events",
    "1.05": "Risk Events",
    "2.01": "Strategic Transactions",
    "2.02": "Financial Results",
    "2.03": "Capital & Financing",
    "2.04": "Capital & Financing",
    "2.05": "Operations & Strategy",
    "2.06": "Financial Results",
    "3.01": "Regulatory & Compliance",
    "3.02": "Capital & Financing",
    "3.03": "Leadership & Governance",
    "4.01": "Regulatory & Compliance",
    "4.02": "Financial Results",
    "5.01": "Leadership & Governance",
    "5.02": "Leadership & Governance",
    "5.03": "Leadership & Governance",
    "5.04": "Shareholder Activity",
    "5.05": "Leadership & Governance",
    "5.06": "Leadership & Governance",
    "5.07": "Shareholder Activity",
    "5.08": "Shareholder Activity",
    "6.01": "Regulatory & Compliance",
}


# ── Legacy labels ────────────────────────────────────────────────────────
# Events classified before the taxonomy shipped carry the old 12-label
# vocabulary. Their rows are never rewritten, so both the API's event-type
# filter and the frontend map them forward at read time — a user filtering
# on "Strategic Transactions" still sees the historical Acquisitions.
# Mirrored in services/api/app/routes/events.py and sensybull-web
# src/hooks/use-events.ts — keep the three in sync.
LEGACY_LABELS: dict[str, str] = {
    "Acquisition":            "Strategic Transactions",
    "Material Agreement":     "Operations & Strategy",
    "Earnings":               "Financial Results",
    "Bankruptcy":             "Risk Events",
    "Debt / Financing":       "Capital & Financing",
    "Restructuring":          "Operations & Strategy",
    "Leadership Change":      "Leadership & Governance",
    "Delisting":              "Regulatory & Compliance",
    "Restatement":            "Financial Results",
    "Cybersecurity Incident": "Risk Events",
    "Regulatory / Clinical":  "Operations & Strategy",
    "Other":                  OTHER,
}
