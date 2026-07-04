"""Provider capability & pricing catalog (pure).

Lists candidate ``(provider, model)`` pairs and filters them by a request's
capability requirements. The candidate metadata (context window, tools, JSON
mode, vision, per-token pricing) comes from ``providers/base.ModelInfo``;
observed latency is *not* stored here — it is read per candidate from the stats
port (Task 6), keeping this catalog static and pure.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from conduit.providers.base import ModelInfo


@dataclass(frozen=True, slots=True)
class Candidate:
    """A routable option: a provider serving one advertised model."""

    provider: str
    model: ModelInfo


@dataclass(frozen=True, slots=True)
class Requirements:
    """Capabilities a request needs (produced by classification, Task 2)."""

    min_context: int = 0
    needs_tools: bool = False
    needs_vision: bool = False
    needs_json_mode: bool = False


class Catalog:
    """An immutable set of candidates, filterable by capability."""

    def __init__(self, candidates: Iterable[Candidate]) -> None:
        self._candidates: list[Candidate] = list(candidates)

    def all(self) -> Sequence[Candidate]:
        return self._candidates

    def capable(self, requirements: Requirements) -> list[Candidate]:
        """Return only the candidates that satisfy every requirement."""
        return [c for c in self._candidates if _satisfies(c.model, requirements)]

    def find(self, provider: str, model_id: str) -> Candidate | None:
        for candidate in self._candidates:
            if candidate.provider == provider and candidate.model.id == model_id:
                return candidate
        return None


def _satisfies(model: ModelInfo, req: Requirements) -> bool:
    if model.context_window < req.min_context:
        return False
    if req.needs_tools and not model.supports_tools:
        return False
    if req.needs_vision and not model.supports_vision:
        return False
    return not (req.needs_json_mode and not model.supports_json_mode)
