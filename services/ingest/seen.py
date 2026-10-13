"""
seen.py — which feed entries have already been handled.

The seen set lives in a JSON file under DATA_DIR and is mirrored into a
Redis hash (one per file, `ingest:seen:<filename>`). The worker's disk is
wiped on every deploy; without the mirror each deploy started from an empty
set and re-briefed the whole EDGAR feed window (~40 filings, each an LLM
call against the Groq budget). The file stays the primary copy so a Redis
outage never stops ingest — the mirror is best-effort both ways.

Only changed entries are written to Redis (HSET/HDEL of the diff against
what was last mirrored), so a save after every published event stays cheap
however large the set grows.
"""

import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

SEEN_FILE = os.path.join(os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__))), "seen.json")
TTL_DAYS  = 30  # entries older than this are safe to prune

_REDIS_PREFIX = "ingest:seen:"

# After a Redis error the mirror stands down this long, so an outage costs
# one timeout per minute rather than one per save.
_BACKOFF_SECONDS = 60

_client = None
_client_failed = False
_down_until = 0.0
# path → the {entry_id: ts} last confirmed written to Redis
_mirrored: dict[str, dict[str, str]] = {}


def _redis():
    """Lazily-built Redis client, or None when REDIS_URL is unset or the
    client can't be built (the mirror is then simply off)."""
    global _client, _client_failed
    if time.monotonic() < _down_until:
        return None
    if _client is not None or _client_failed:
        return _client
    url = os.environ.get("REDIS_URL", "")
    if not url:
        _client_failed = True
        return None
    try:
        import redis
        _client = redis.from_url(url, decode_responses=True,
                                 socket_timeout=3, socket_connect_timeout=2)
    except Exception as exc:
        log.warning("Seen-set Redis mirror disabled: %s", exc)
        _client_failed = True
    return _client


def _stand_down(what: str, exc: Exception) -> None:
    global _down_until
    _down_until = time.monotonic() + _BACKOFF_SECONDS
    log.warning("Seen set: Redis %s failed (%s) — file only for %ds",
                what, exc, _BACKOFF_SECONDS)


def _redis_key(path: str) -> str:
    return _REDIS_PREFIX + os.path.basename(path)


def _load_file(path: str) -> dict[str, str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
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


def load_seen(path: str = SEEN_FILE) -> dict[str, str]:
    """Load seen set as {entry_id: iso_timestamp}: the file, plus whatever
    the Redis mirror holds (after a deploy the file is gone and the mirror
    is all there is).

    Handles legacy format (plain JSON array of ID strings) by assigning the
    current time as the timestamp — those entries will be pruned after TTL_DAYS.
    """
    seen = _load_file(path)

    client = _redis()
    if client is not None:
        try:
            mirrored = client.hgetall(_redis_key(path)) or {}
            _mirrored[path] = dict(mirrored)
            restored = 0
            for entry_id, ts in mirrored.items():
                if entry_id not in seen:
                    seen[entry_id] = ts
                    restored += 1
            if restored:
                log.info("Seen set: restored %d entr%s from Redis (%s)",
                         restored, "y" if restored == 1 else "ies", os.path.basename(path))
        except Exception as exc:
            _stand_down("read", exc)

    return seen


def _sync_redis(pruned: dict[str, str], path: str) -> None:
    client = _redis()
    if client is None:
        return
    before = _mirrored.get(path, {})
    changed = {k: v for k, v in pruned.items() if before.get(k) != v}
    removed = [k for k in before if k not in pruned]
    if not changed and not removed:
        return
    try:
        key = _redis_key(path)
        pipe = client.pipeline()
        if changed:
            pipe.hset(key, mapping=changed)
        if removed:
            pipe.hdel(key, *removed)
        pipe.execute()
        _mirrored[path] = dict(pruned)
    except Exception as exc:
        # _mirrored is left as it was, so the next save retries this diff
        _stand_down("write", exc)


def save_seen(seen: dict[str, str], path: str = SEEN_FILE) -> None:
    """Prune entries older than TTL_DAYS, atomically write to disk, then
    mirror the change to Redis."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)
    pruned = {}
    for entry_id, ts in seen.items():
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt >= cutoff:
                pruned[entry_id] = ts
        except (ValueError, AttributeError):
            pruned[entry_id] = ts  # unparseable timestamp — keep to avoid re-broadcast

    dir_ = os.path.dirname(path)
    with tempfile.NamedTemporaryFile(
        "w", dir=dir_, delete=False, suffix=".tmp", encoding="utf-8"
    ) as tmp:
        json.dump(pruned, tmp)
        tmp_path = tmp.name
    os.replace(tmp_path, path)

    _sync_redis(pruned, path)
