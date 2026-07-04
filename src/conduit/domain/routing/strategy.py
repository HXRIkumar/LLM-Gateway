"""Routing contract.

A :class:`RoutingStrategy` turns a request into a :class:`RoutingDecision` — the
chosen provider and model, an ordered fallback plan, and the reason. Every
strategy implements this one interface, so the MVP's static mapping and the V3
cost/latency/capability strategies are interchangeable behind it. Pure domain:
no framework, no vendor, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from conduit.domain.schemas import ChatCompletionRequest


@dataclass(frozen=True, slots=True)
class RoutingTarget:
    """A provider + provider-native model pair in a fallback plan."""

    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The outcome of routing a single request.

    ``fallbacks`` is the ordered plan the reliability layer (V2) walks on
    failure; it is empty under static routing. ``reason`` is recorded per request
    for later analysis.
    """

    provider: str
    model: str
    reason: str
    fallbacks: tuple[RoutingTarget, ...] = ()


@runtime_checkable
class RoutingStrategy(Protocol):
    """Chooses a provider/model for a request."""

    def route(self, request: ChatCompletionRequest) -> RoutingDecision: ...
