# services/api/app/services/market_data/cache.py
"""
Best-effort Redis JSON cache for market data.

Every helper degrades to a no-op when Redis is unset or unreachable — the
company-sync cron runs without REDIS_URL and tests run without a Redis
server, so nothing here may raise.
"""

import json
import logging
import os

log = logging.getLogger(__name__)

_client = None
_client_failed = False


def _redis():
    global _client, _client_failed
    if _client is not None or _client_failed:
        return _client
    url = os.environ.get("REDIS_URL")
    if not url:
        _client_failed = True
        return None
    try:
        import redis
        _client = redis.from_url(url, decode_responses=True, socket_timeout=2)
    except Exception:
        _client_failed = True
        _client = None
    return _client


def cache_get(key: str):
    """Return the JSON-decoded value for key, or None."""
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def cache_set(key: str, value, ttl_seconds: int) -> None:
    """JSON-encode value under key with a TTL. Silently no-ops on failure."""
    client = _redis()
    if client is None:
        return
    try:
        client.set(key, json.dumps(value), ex=ttl_seconds)
    except Exception:
        pass
