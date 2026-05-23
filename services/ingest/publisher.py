# services/ingest/publisher.py
"""
Redis publisher. Publishes FilingEvent JSON to the `filing:new` channel.
Fails silently — a Redis outage must never break the ingest pipeline.
"""

import logging
import os

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        import redis
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def publish_filing(event_json: str) -> None:
    """Publish a FilingEvent JSON string to Redis. Swallows all errors."""
    try:
        _get_client().publish("filing:new", event_json)
        log.debug("Published to Redis filing:new")
    except Exception as exc:
        log.warning("Redis publish failed (filing:new): %s", exc)
