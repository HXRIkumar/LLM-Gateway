"""Unit tests for the pure key helpers (hashing, generation, prefix)."""

from __future__ import annotations

import hashlib

from conduit.services.keys import (
    KEY_SCHEME,
    PREFIX_LENGTH,
    generate_token,
    hash_token,
    token_prefix,
)


def test_generated_token_has_scheme_and_is_random() -> None:
    first = generate_token()
    second = generate_token()
    assert first.startswith(KEY_SCHEME)
    assert first != second
    assert len(first) > 20


def test_hash_is_sha256_hex_and_not_the_plaintext() -> None:
    token = "ck-example-token"
    digest = hash_token(token)
    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert digest != token
    assert len(digest) == 64


def test_prefix_is_the_leading_slice() -> None:
    token = generate_token()
    assert token_prefix(token) == token[:PREFIX_LENGTH]
    assert len(token_prefix(token)) == PREFIX_LENGTH
