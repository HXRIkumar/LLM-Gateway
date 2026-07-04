"""Optimization policies (Phase 5): caching, dedup, prediction, adaptation.

Pure domain logic behind ports. State (cache entries, vectors, locks, rolling
stats) lives in ``infra/`` adapters; nothing here does I/O or imports a framework.
"""
