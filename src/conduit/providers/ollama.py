"""Ollama provider adapter.

Ollama's native ``/api/chat`` API is deliberately *unlike* OpenAI's: generation
parameters live under an ``options`` block, token counts come back as
``prompt_eval_count`` / ``eval_count``, there is no response ``id``, and streams
are newline-delimited JSON rather than SSE. Translating to and from the canonical
schema here — and nothing leaking outward — is what validates the provider
abstraction against a genuinely different backend (ADR-0003).
"""

from __future__ import annotations

import json
import time
import uuid
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
    ChatCompletionMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceDelta,
    ChunkChoice,
    Message,
    Usage,
)
from conduit.providers.base import HealthStatus, ModelInfo

# Local models are free to run; pricing defaults to zero.
DEFAULT_OLLAMA_MODELS: list[ModelInfo] = [
    ModelInfo(id="llama3.2", context_window=131_072, supports_tools=True),
    ModelInfo(id="llama3.1", context_window=131_072, supports_tools=True),
    ModelInfo(id="qwen2.5", context_window=32_768, supports_tools=True),
]


class OllamaProvider:
    """Adapter for the Ollama native ``/api/chat`` API."""

    name: str = "ollama"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        base_url: str,
        models: Sequence[ModelInfo] | None = None,
    ) -> None:
        self._http = http_client
        self._base_url = base_url.rstrip("/")
        self._models = list(models) if models is not None else DEFAULT_OLLAMA_MODELS

    @property
    def models(self) -> Sequence[ModelInfo]:
        return self._models

    @property
    def _chat_url(self) -> str:
        return f"{self._base_url}/api/chat"

    # --- canonical -> Ollama ------------------------------------------------

    def _to_ollama_payload(self, request: ChatCompletionRequest, *, stream: bool) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.top_p is not None:
            options["top_p"] = request.top_p
        num_predict = request.max_completion_tokens or request.max_tokens
        if num_predict is not None:
            options["num_predict"] = num_predict
        if request.seed is not None:
            options["seed"] = request.seed
        if request.stop is not None:
            options["stop"] = [request.stop] if isinstance(request.stop, str) else request.stop

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [self._to_ollama_message(m) for m in request.messages],
            "stream": stream,
        }
        if options:
            payload["options"] = options
        if request.tools is not None:
            payload["tools"] = request.tools
        if isinstance(request.response_format, dict) and request.response_format.get("type") in {
            "json_object",
            "json_schema",
        }:
            payload["format"] = "json"
        return payload

    def _to_ollama_message(self, message: Message) -> dict[str, Any]:
        return {"role": message.role, "content": self._content_to_text(message.content)}

    @staticmethod
    def _content_to_text(content: str | list[dict[str, Any]] | None) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        )

    # --- Ollama -> canonical ------------------------------------------------

    @staticmethod
    def _new_id() -> str:
        return f"chatcmpl-{uuid.uuid4().hex}"

    @staticmethod
    def _finish_reason(data: dict[str, Any]) -> str | None:
        if not data.get("done"):
            return None
        reason = data.get("done_reason")
        return reason if isinstance(reason, str) else "stop"

    @staticmethod
    def _usage(data: dict[str, Any]) -> Usage | None:
        prompt = data.get("prompt_eval_count")
        completion = data.get("eval_count")
        if prompt is None and completion is None:
            return None
        prompt = prompt or 0
        completion = completion or 0
        return Usage(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        )

    def _to_canonical_response(
        self, data: dict[str, Any], request_model: str
    ) -> ChatCompletionResponse:
        message = data.get("message") or {}
        return ChatCompletionResponse(
            id=self._new_id(),
            created=int(time.time()),
            model=str(data.get("model", request_model)),
            choices=[
                Choice(
                    index=0,
                    message=ChatCompletionMessage(
                        role=str(message.get("role", "assistant")),
                        content=message.get("content", ""),
                    ),
                    finish_reason=self._finish_reason(data),
                )
            ],
            usage=self._usage(data),
        )

    # --- execution ----------------------------------------------------------

    async def chat_completion(self, request: ChatCompletionRequest) -> ChatCompletionResponse:
        try:
            response = await self._http.post(
                self._chat_url, json=self._to_ollama_payload(request, stream=False)
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("ollama request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama request failed: {exc.__class__.__name__}") from exc

        if response.status_code >= 400:
            raise self._map_error(response)
        return self._to_canonical_response(response.json(), request.model)

    async def stream_chat_completion(
        self, request: ChatCompletionRequest
    ) -> AsyncIterator[ChatCompletionChunk]:
        completion_id = self._new_id()
        created = int(time.time())
        first = True
        try:
            async with self._http.stream(
                "POST", self._chat_url, json=self._to_ollama_payload(request, stream=True)
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise self._map_error(response)
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    data = json.loads(stripped)
                    message = data.get("message") or {}
                    delta = ChoiceDelta(content=message.get("content") or None)
                    if first:
                        delta.role = str(message.get("role", "assistant"))
                        first = False
                    yield ChatCompletionChunk(
                        id=completion_id,
                        created=created,
                        model=str(data.get("model", request.model)),
                        choices=[
                            ChunkChoice(
                                index=0, delta=delta, finish_reason=self._finish_reason(data)
                            )
                        ],
                        usage=self._usage(data) if data.get("done") else None,
                    )
                    if data.get("done"):
                        return
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("ollama stream timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"ollama stream failed: {exc.__class__.__name__}") from exc

    async def health(self) -> HealthStatus:
        try:
            response = await self._http.get(f"{self._base_url}/api/tags")
        except httpx.HTTPError as exc:
            return HealthStatus(healthy=False, detail=exc.__class__.__name__)
        return HealthStatus(healthy=response.status_code < 500)

    # --- error mapping ------------------------------------------------------

    def _map_error(self, response: httpx.Response) -> ProviderError:
        message = self._extract_message(response) or f"ollama returned {response.status_code}"
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
            if isinstance(error, str):
                return error
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return str(error["message"])
        return None


def build_ollama_provider(settings: Settings, http_client: httpx.AsyncClient) -> OllamaProvider:
    """Factory used by the provider registry."""
    return OllamaProvider(http_client, base_url=settings.ollama_base_url)
