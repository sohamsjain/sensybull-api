"""
Shared fixtures for ingest tests.

IMPORTANT: fetcher.py and briefing.py raise at import time when
SEC_USER_AGENT / Groq keys are missing, so env vars must be set before any
project module is imported. Run from services/ingest with PYTHONPATH=.
"""

import os
import sys

os.environ.setdefault("SEC_USER_AGENT", "Sensybull tests test@example.com")
os.environ.setdefault("GROQ_API_KEY", "test-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


@pytest.fixture
def fixture_text():
    def _load(name: str) -> str:
        with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as fh:
            return fh.read()
    return _load


@pytest.fixture
def ticker_map():
    return {
        "0000320193": {"ticker": "AAPL", "name": "Apple Inc."},
        "0001318605": {"ticker": "TSLA", "name": "Tesla, Inc."},
        "0000012345": {"ticker": "SMCP", "name": "SmallCap Industries Inc."},
    }
