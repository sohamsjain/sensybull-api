import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

SEEN_FILE = os.path.join(os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__))), "seen.json")
TTL_DAYS  = 30  # entries older than this are safe to prune


def load_seen() -> dict[str, str]:
    """Load seen set as {entry_id: iso_timestamp}.

    Handles legacy format (plain JSON array of ID strings) by assigning the
    current time as the timestamp — those entries will be pruned after TTL_DAYS.
    """
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}

    if isinstance(data, list):
        # Migrate from old set format: keep all existing IDs for one more TTL window
        now = datetime.now(timezone.utc).isoformat()
        return {entry_id: now for entry_id in data if isinstance(entry_id, str)}

    if isinstance(data, dict):
        return data

    return {}


def save_seen(seen: dict[str, str]) -> None:
    """Prune entries older than TTL_DAYS, then atomically write to disk."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
    pruned = {}
    for entry_id, ts in seen.items():
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt >= cutoff:
                pruned[entry_id] = ts
        except (ValueError, AttributeError):
            pruned[entry_id] = ts  # unparseable timestamp — keep to avoid re-broadcast

    dir_ = os.path.dirname(SEEN_FILE)
    with tempfile.NamedTemporaryFile(
        "w", dir=dir_, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tmp:
        json.dump(pruned, tmp)
        tmp_path = tmp.name
    os.replace(tmp_path, SEEN_FILE)
