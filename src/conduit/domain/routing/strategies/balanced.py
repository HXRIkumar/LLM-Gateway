"""Balanced ranking (pure): a weighted blend of normalized cost and latency.

Cost and observed latency are min-max normalized across the candidate set, then
combined with configurable weights (lower score = better). Candidates missing
latency data are treated as the slowest observed (conservative); if no latency is
known at all, ranking reduces to cost. Capability fit is already guaranteed —
candidates are pre-filtered — so it does not enter the score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from conduit.domain.routing.catalog import Candidate
from conduit.domain.routing.classify import estimate_prompt_tokens
from conduit.domain.routing.stats import ErrorRateSnapshot, LatencySnapshot
from conduit.domain.routing.strategies.cost import estimate_cost, expected_completion_tokens
from conduit.domain.schemas import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class BalancedWeights:
    cost: float = 0.5
    latency: float = 0.5
    # Adaptive penalty: recent error rate (0..1) times this weight is added to the
    # score, so a provider whose error rate is climbing is de-weighted. With no
    # error data (empty snapshot) every penalty is 0 → ranking is unchanged.
    error: float = 1.0


def _normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.0] * len(values)
    return [(value - lo) / (hi - lo) for value in values]


def rank_balanced(
    candidates: Sequence[Candidate],
    request: ChatCompletionRequest,
    snapshot: LatencySnapshot,
    weights: BalancedWeights | None = None,
    error_rates: ErrorRateSnapshot | None = None,
) -> list[Candidate]:
    if not candidates:
        return []
    weights = weights or BalancedWeights()
    error_rates = error_rates or {}
    prompt = estimate_prompt_tokens(request)
    completion = expected_completion_tokens(request)
    costs = [float(estimate_cost(c.model.pricing, prompt, completion)) for c in candidates]

    raw_latencies = [snapshot.get((c.provider, c.model.id)) for c in candidates]
    known = [value for value in raw_latencies if value is not None]
    worst = max(known) if known else 0.0
    latencies = [value if value is not None else worst for value in raw_latencies]
    errors = [error_rates.get((c.provider, c.model.id), 0.0) for c in candidates]

    norm_cost = _normalize(costs)
    norm_latency = _normalize(latencies)
    order = sorted(
        range(len(candidates)),
        key=lambda i: (
            weights.cost * norm_cost[i]
            + weights.latency * norm_latency[i]
            + weights.error * errors[i]
        ),
    )
    return [candidates[i] for i in order]
