"""Request classification (pure): derive capability requirements from a request.

Fills the preflight *classify* seam (CLAUDE.md §5 step 3). Estimates prompt size
(→ required context window), and detects tools, vision (image parts), and JSON
mode. No I/O — the token estimate is a cheap char-based heuristic, deliberately
conservative; precise token counting is a V5 concern.
"""

from __future__ import annotations

from conduit.domain.routing.catalog import Requirements
from conduit.domain.schemas import ChatCompletionRequest

_CHARS_PER_TOKEN = 4


def _message_text_len(request: ChatCompletionRequest) -> int:
    total = 0
    for message in request.messages:
        content = message.content
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                text = part.get("text") if isinstance(part, dict) else None
                if isinstance(text, str):
                    total += len(text)
    return total


def _has_image_parts(request: ChatCompletionRequest) -> bool:
    for message in request.messages:
        if isinstance(message.content, list):
            for part in message.content:
                if isinstance(part, dict) and part.get("type") in {"image_url", "input_image"}:
                    return True
    return False


def estimate_prompt_tokens(request: ChatCompletionRequest) -> int:
    """Cheap, conservative prompt-token estimate (~4 chars/token)."""
    return _message_text_len(request) // _CHARS_PER_TOKEN + 1


def classify(request: ChatCompletionRequest) -> Requirements:
    """Derive the capability requirements a router must satisfy for this request."""
    completion_headroom = request.max_completion_tokens or request.max_tokens or 0
    needs_json = isinstance(request.response_format, dict) and request.response_format.get(
        "type"
    ) in {"json_object", "json_schema"}
    return Requirements(
        min_context=estimate_prompt_tokens(request) + completion_headroom,
        needs_tools=request.tools is not None or request.tool_choice is not None,
        needs_vision=_has_image_parts(request),
        needs_json_mode=needs_json,
    )
