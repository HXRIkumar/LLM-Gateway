"""Unit tests for the pure routing policy value."""

from __future__ import annotations

from conduit.domain.routing.policy import DEFAULT_POLICY, Policy


def test_default_permits_all() -> None:
    assert DEFAULT_POLICY.permits("openai") is True
    assert DEFAULT_POLICY.permits("ollama") is True


def test_deny_list_blocks() -> None:
    policy = Policy(deny_providers=("openai",))
    assert policy.permits("openai") is False
    assert policy.permits("ollama") is True


def test_allow_list_restricts() -> None:
    policy = Policy(allow_providers=("ollama",))
    assert policy.permits("ollama") is True
    assert policy.permits("openai") is False


def test_deny_wins_over_allow() -> None:
    policy = Policy(allow_providers=("openai", "ollama"), deny_providers=("openai",))
    assert policy.permits("openai") is False
    assert policy.permits("ollama") is True
