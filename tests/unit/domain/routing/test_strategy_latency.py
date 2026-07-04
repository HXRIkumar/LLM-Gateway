"""Unit tests for latency-optimized ranking (injected stats)."""

from __future__ import annotations

from conduit.domain.routing.catalog import Candidate
from conduit.domain.routing.strategies.latency import rank_by_latency
from conduit.providers.base import ModelInfo


def _candidate(provider: str, model_id: str) -> Candidate:
    return Candidate(provider=provider, model=ModelInfo(id=model_id, context_window=8_000))


A = _candidate("openai", "gpt-4o-mini")
B = _candidate("ollama", "llama3.2")
C = _candidate("ollama", "qwen2.5")


def test_fastest_observed_ranked_first() -> None:
    snapshot = {("openai", "gpt-4o-mini"): 800.0, ("ollama", "llama3.2"): 120.0}
    assert [c.model.id for c in rank_by_latency([A, B], snapshot)] == ["llama3.2", "gpt-4o-mini"]


def test_candidates_without_stats_sort_last_preserving_order() -> None:
    snapshot = {("ollama", "llama3.2"): 100.0}
    # A and C have no stats → after B, in input order.
    assert [c.model.id for c in rank_by_latency([A, B, C], snapshot)] == [
        "llama3.2",
        "gpt-4o-mini",
        "qwen2.5",
    ]


def test_cold_start_is_a_noop() -> None:
    assert rank_by_latency([A, B, C], {}) == [A, B, C]
