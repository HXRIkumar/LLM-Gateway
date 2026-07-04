"""Routing policy value object (pure).

A declarative policy: the routing ``objective`` plus provider allow/deny lists.
Strategies consume this; the service layer loads it from the DB (key over org,
default when none) and converts the row into this pure value.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Policy:
    objective: str = "balanced"  # cost | latency | balanced
    allow_providers: tuple[str, ...] | None = None  # None = all allowed
    deny_providers: tuple[str, ...] = ()

    def permits(self, provider: str) -> bool:
        if provider in self.deny_providers:
            return False
        return self.allow_providers is None or provider in self.allow_providers


DEFAULT_POLICY = Policy()
