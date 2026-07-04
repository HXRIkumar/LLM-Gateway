"""Live routing signals port (pure).

The domain reads observed latency through this port; an infra/services adapter
implements it from Phase 2's usage data. A snapshot maps ``(provider, model)`` to
observed latency in ms; a missing entry means "no data yet".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

LatencySnapshot = Mapping[tuple[str, str], float]


class LatencyStats(Protocol):
    async def snapshot(
        self, targets: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], float]: ...
