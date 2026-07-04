"""OpenAI-compatible embeddings adapter (implements the ``Embedder`` port).

Used by the semantic cache to embed request prompts. Reuses the shared httpx
client and the OpenAI base URL/key. Off unless the semantic cache is enabled; in
tests the port is swapped for a deterministic fake, so this never needs a live
backend.
"""

from __future__ import annotations

from typing import Any

import httpx

from conduit.domain.errors import ProviderError


class OpenAIEmbedder:
    """Embed text via the OpenAI ``/embeddings`` endpoint."""

    def __init__(
        self, http_client: httpx.AsyncClient, *, api_key: str, base_url: str, model: str
    ) -> None:
        self._http = http_client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def embed(self, text: str) -> list[float]:
        try:
            response = await self._http.post(
                f"{self._base_url}/embeddings",
                json={"model": self._model, "input": text},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"embedding request failed: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise ProviderError(f"embedding request failed: HTTP {response.status_code}")
        data: dict[str, Any] = response.json()
        return list(data["data"][0]["embedding"])
