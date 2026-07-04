"""Latency-optimized ranking (pure).

Rank capable candidates by observed latency (ascending) from a stats snapshot.
Candidates with no observed latency sort after those that do, preserving input
order among themselves — so a cold start (no stats at all) is a no-op that leaves
the input (e.g. cost/config) order intact.
"""

from __future__ import annotations

from collections.abc import Sequence

from conduit.domain.routing.catalog import Candidate
from conduit.domain.routing.stats import LatencySnapshot


def rank_by_latency(candidates: Sequence[Candidate], snapshot: LatencySnapshot) -> list[Candidate]:
    def sort_key(candidate: Candidate) -> tuple[bool, float]:
        latency = snapshot.get((candidate.provider, candidate.model.id))
        return (latency is None, latency if latency is not None else 0.0)

    return sorted(candidates, key=sort_key)
