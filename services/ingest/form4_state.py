"""
form4_state.py — rolling window of qualifying insider buys per issuer.

File-backed like seen.py: {issuer_cik: [{owner_cik, owner_name, value,
date, accession}, ...]}, pruned to the cluster window on every save,
written atomically. Powers cluster detection (2+ distinct insiders buying
within FORM4_CLUSTER_WINDOW_DAYS).
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

from form4 import CLUSTER_WINDOW_DAYS

BUYS_FILE = os.path.join(
    os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__))),
    "form4_buys.json",
)


def load_buys() -> dict[str, list[dict]]:
    try:
        with open(BUYS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _in_window(buy: dict, cutoff: datetime) -> bool:
    try:
        dt = datetime.fromisoformat(str(buy.get("date", "")).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= cutoff
    except (ValueError, TypeError):
        return False


def record_buy(state: dict[str, list[dict]], issuer_cik: str, buy: dict,
               window_days: int = CLUSTER_WINDOW_DAYS) -> list[dict]:
    """Record a qualifying buy and return all buys in the current window.

    Idempotent per accession — re-recording the same filing doesn't
    double-count. Mutates `state`; caller saves via save_buys().
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    buys = [b for b in state.get(issuer_cik, []) if _in_window(b, cutoff)]
    if not any(b.get("accession") == buy.get("accession") for b in buys):
        buys.append(buy)
    state[issuer_cik] = buys
    return buys


def save_buys(state: dict[str, list[dict]],
              window_days: int = CLUSTER_WINDOW_DAYS) -> None:
    """Prune everything outside the window, then atomically write."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    pruned = {}
    for cik, buys in state.items():
        kept = [b for b in buys if _in_window(b, cutoff)]
        if kept:
            pruned[cik] = kept

    dir_ = os.path.dirname(BUYS_FILE)
    with tempfile.NamedTemporaryFile(
        "w", dir=dir_, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tmp:
        json.dump(pruned, tmp)
        tmp_path = tmp.name
    os.replace(tmp_path, BUYS_FILE)
