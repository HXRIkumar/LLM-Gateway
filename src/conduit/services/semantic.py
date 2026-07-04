"""Semantic cache orchestration (near-match lookup over the exact response cache).

Bundles an :class:`Embedder`, a :class:`SemanticIndex`, and the exact response
cache. ``probe`` embeds the request prompt, finds the nearest indexed prompt, and
returns the cached response when similarity clears the threshold; it also returns
the computed vector so a subsequent ``remember`` can index the fresh response
without re-embedding.
"""

from __future__ import annotations

from dataclasses import dataclass

from conduit.domain.optimize.cache import ResponseCache
from conduit.domain.optimize.semantic import Embedder, SemanticIndex, prompt_text
from conduit.domain.schemas import ChatCompletionRequest, ChatCompletionResponse


@dataclass(frozen=True, slots=True)
class SemanticProbe:
    """The outcome of a semantic lookup: the query vector + any near-match hit."""

    vector: list[float]
    hit: ChatCompletionResponse | None


class SemanticCache:
    """Near-match cache: embedding + vector index over the exact response cache."""

    def __init__(
        self,
        embedder: Embedder,
        index: SemanticIndex,
        cache: ResponseCache,
        threshold: float,
    ) -> None:
        self._embedder = embedder
        self._index = index
        self._cache = cache
        self._threshold = threshold

    async def probe(self, request: ChatCompletionRequest) -> SemanticProbe:
        vector = await self._embedder.embed(prompt_text(request))
        nearest = await self._index.nearest(vector)
        if nearest is None:
            return SemanticProbe(vector=vector, hit=None)
        near_key, similarity = nearest
        if similarity < self._threshold:
            return SemanticProbe(vector=vector, hit=None)
        return SemanticProbe(vector=vector, hit=await self._cache.get(near_key))

    async def remember(self, key: str, vector: list[float]) -> None:
        await self._index.add(key, vector)
