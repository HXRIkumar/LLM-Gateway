"""Unit tests for the pure semantic-cache helpers."""

from __future__ import annotations

import pytest

from conduit.domain.optimize.semantic import cosine_similarity, prompt_text
from conduit.domain.schemas import ChatCompletionRequest


def test_cosine_identical_is_one() -> None:
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_or_mismatched_is_zero() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


def test_prompt_text_joins_string_messages_only() -> None:
    req = ChatCompletionRequest.model_validate(
        {
            "model": "m",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hello"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {}}]},
            ],
        }
    )
    assert prompt_text(req) == "system: be brief\nuser: hello"
