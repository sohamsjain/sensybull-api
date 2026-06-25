"""
queue.py — tiny Redis-list job queue between the subscriber and the worker.

The subscriber LPUSHes a freshly-persisted FilingEvent id; the analysis worker
BRPOPs it. This is intentionally lightweight (no Celery): the DB's
``analysis_status='pending'`` column is the durable source of truth, so a lost
queue entry is recovered by the worker's startup sweep.
"""
import logging
import os

import redis

log = logging.getLogger(__name__)

ANALYSIS_QUEUE = "analysis:queue"

_client = None


def _redis():
    global _client
    if _client is None:
        url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        _client = redis.from_url(url, decode_responses=True)
    return _client


def enqueue(event_id: str) -> None:
    """Queue an event for analysis. Never raises — the pending sweep is the backstop."""
    try:
        _redis().lpush(ANALYSIS_QUEUE, event_id)
    except Exception:
        log.exception("analysis queue: failed to enqueue %s (will be swept later)", event_id)


def analysis_enabled() -> bool:
    """Feature flag / kill-switch. When false the subscriber fans out immediately
    (legacy behavior) instead of deferring to the worker."""
    return os.environ.get("ANALYSIS_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off")
