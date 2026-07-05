# services/api/app/services/alerts/thesis_format.py
"""
Shared formatting for thesis-aware alerts.

When a filing is judged against a user's thesis, every delivery channel
leads with the same verdict line. This is the single source of that copy
so email, push, SMS, Slack, Discord, Telegram, WhatsApp and webhooks stay
consistent.

The `assessment` passed around is a plain dict (not the ORM row) so it can
cross the alert thread-pool boundary safely:
    {"impact": "breaks"|"threatens"|"supports"|"neutral",
     "rationale": str, "thesis_status": "intact"|"watch"|"broken"}
"""

# impact → (short label, emoji, hex color for rich channels)
IMPACT_META: dict[str, tuple[str, str, str]] = {
    "breaks":    ("Thesis broken",    "🔴", "#dc2626"),
    "threatens": ("Thesis at risk",   "🟠", "#ea580c"),
    "supports":  ("Thesis supported", "🟢", "#16a34a"),
    "neutral":   ("Thesis unaffected", "⚪", "#6b7280"),
}

# Verdicts that warrant an enriched alert over channels (neutral falls back
# to the regular filing alert).
NOTIFY_IMPACTS = frozenset({"supports", "threatens", "breaks"})


def _meta(assessment: dict | None) -> tuple[str, str, str] | None:
    if not assessment:
        return None
    return IMPACT_META.get(assessment.get("impact", ""))


def label(assessment: dict | None) -> str | None:
    """Short verdict label, e.g. 'Thesis broken'."""
    m = _meta(assessment)
    return m[0] if m else None


def emoji(assessment: dict | None) -> str:
    m = _meta(assessment)
    return m[1] if m else ""


def color(assessment: dict | None) -> str | None:
    m = _meta(assessment)
    return m[2] if m else None


def subject_prefix(assessment: dict | None) -> str | None:
    """Email subject prefix, e.g. 'Thesis broken'."""
    return label(assessment)


def line(assessment: dict | None) -> str | None:
    """One-line verdict for text channels: '🔴 Thesis broken — <rationale>'."""
    m = _meta(assessment)
    if not m:
        return None
    lbl, emj, _ = m
    rationale = (assessment.get("rationale") or "").strip()
    return f"{emj} {lbl} — {rationale}" if rationale else f"{emj} {lbl}"
