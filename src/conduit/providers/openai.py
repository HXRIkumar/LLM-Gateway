"""OpenAI provider adapter.

Because the canonical schema *is* the OpenAI shape (ADR-0003), translation here
is thin: the request is forwarded and responses are validated back into the
canonical models. The adapter's real job is quarantining OpenAI's HTTP details —
auth headers, SSE framing, and error bodies — and mapping upstream failures onto
``domain.errors`` so retry/fallback and the edge stay provider-agnostic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from conduit.config import Settings
from conduit.domain.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderRateLimited,
    ProviderTimeout,
    UpstreamInvalidRequest,
)
from conduit.domain.schemas import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
)
from conduit.providers.base import HealthStatus, ModelInfo, ModelPricing

DEFAULT_OPENAI_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="gpt-4o",
        context_window=128_000,
        supports_tools=True,
        supports_json_mode=True,
        supports_vision=True,
        pricing=ModelPricing(input_per_1k_usd=0.005, output_per_1k_usd=0.015),
    ),
    ModelInfo(
        id="gpt-4o-mini",
        context_window=128_000,
        supports_tools=True,
        supports_json_mode=True,
        supports_vision=True,
        pricing=ModelPricing(input_per_1k_usd=0.00015, output_per_1k_usd=0.0006),
    ),
]

_SSE_DATA_PREFIX = "data:"
_SSE_DONE = "[DONE]"


class OpenAIProvider:
    """Adapter for the OpenAI Chat Completions API."""

    name: str = "openai"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        api_key: str,
        base_url: str,
        models: Sequence[ModelInfo] | None = None,
    ) -> None:
        self._http = http_client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._models = list(models) if models is not None else DEFAULT_OPENAI_MODELS

    @property
    def models(self) -> Sequence[ModelInfo]:
        return self._models

    # --- translation --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _payload(self, request: ChatCompletionRequest, *, stream: bool) -> dict[str, Any]:
        payload = request.model_dump(exclude_none=True)
        payload["stream"] = stream
        return payload

    @property
    def _completions_url(self) -> str:
        return f"{self._base_url}/chat/completions"

    # --- execution ----------------------------------------------------------

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        try:
            response = await self._http.post(
                self._completions_url,
                json=self._payload(request, stream=False),
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("openai request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai request failed: {exc.__class__.__name__}") from exc

        if response.status_code >= 400:
            raise self._map_error(response)
        return ChatCompletionResponse.model_validate(response.json())

    async def stream_chat_completion(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        try:
            async with self._http.stream(
                "POST",
                self._completions_url,
                json=self._payload(request, stream=True),
                headers=self._headers(),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise self._map_error(response)
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped.startswith(_SSE_DATA_PREFIX):
                        continue
                    data = stripped[len(_SSE_DATA_PREFIX) :].strip()
                    if data == _SSE_DONE:
                        return
                    yield ChatCompletionChunk.model_validate_json(data)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("openai stream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai stream failed: {exc.__class__.__name__}") from exc

    async def health(self) -> HealthStatus:
        try:
            response = await self._http.get(f"{self._base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            return HealthStatus(healthy=False, detail=exc.__class__.__name__)
        return HealthStatus(healthy=response.status_code < 500)

    # --- error mapping ------------------------------------------------------

    def _map_error(self, response: httpx.Response) -> ProviderError:
        message = self._extract_message(response) or f"openai returned {response.status_code}"
        status = response.status_code
        if status in (401, 403):
            return ProviderAuthError(message)
        if status == 429:
            return ProviderRateLimited(message)
        if status == 408:
            return ProviderTimeout(message)
        if 400 <= status < 500:
            return UpstreamInvalidRequest(message)
        return ProviderError(message)

    @staticmethod
    def _extract_message(response: httpx.Response) -> str | None:
        try:
            data = response.json()
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return str(error["message"])
        return None


def build_openai_provider(settings: Settings, http_client: httpx.AsyncClient) -> OpenAIProvider:
    """Factory used by the provider registry."""
    api_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    return OpenAIProvider(http_client, api_key=api_key, base_url=settings.openai_base_url)
