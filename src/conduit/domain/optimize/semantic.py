"""Semantic-cache policy: prompt text, cosine similarity, and the ports.

Pure — no I/O, no framework. The ``Embedder`` and ``SemanticIndex`` ports are
implemented by infra adapters (an embeddings provider and a Redis vector store).
Semantic hits reuse the exact-match response cache: the index maps a prompt
vector to the *same* cache key, and the response is fetched from that cache
(ADR-0008). Safety guards are shared with the exact cache via ``is_cacheable`` —
tool/vision/non-deterministic requests never reach this path.
"""

from __future__ import annotations

import math
from typing import Protocol

from conduit.domain.schemas import ChatCompletionRequest


def prompt_text(request: ChatCompletionRequest) -> str:
    """A canonical string over the request's text messages, for embedding."""
    parts = []
    for message in request.messages:
        if isinstance(message.content, str):
            parts.append(f"{message.role}: {message.content}")
    return "\n".join(parts)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1, 1]; 0 when either vector is zero or mismatched."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class Embedder(Protocol):
    """Port: turn text into an embedding vector (infra provides the adapter)."""

    async def embed(self, text: str) -> list[float]: ...


class SemanticIndex(Protocol):
    """Port: a vector index mapping cache keys to prompt embeddings."""

    async def add(self, key: str, vector: list[float]) -> None: ...
    async def nearest(self, vector: list[float]) -> tuple[str, float] | None: ...
