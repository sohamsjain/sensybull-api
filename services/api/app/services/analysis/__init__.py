"""
Analysis — second-order, company-specific interpretation of a filing.

- ``playbooks``: event-type-specific, deterministic financial ratios (Python math,
  never the LLM).
- ``llm``: turns the numbers + filing facts into an unbiased insight and a revised
  per-company thesis.
- ``engine``: ties fundamentals + playbook + thesis history together and persists
  an EventAnalysis + ThesisRevision.
"""
