"""
Fundamentals — company financials sourced from SEC's free XBRL companyfacts API.

Public entry point: ``get_company_snapshot(cik)`` returns a normalized
FundamentalsSnapshot dict (latest-reported balance-sheet / income metrics),
cached in the ``company_fundamentals`` table and refreshed only when stale.
"""
from app.services.fundamentals.service import get_company_snapshot  # noqa: F401
from app.services.fundamentals.extract import build_snapshot  # noqa: F401
from app.services.fundamentals.client import fetch_company_facts  # noqa: F401
