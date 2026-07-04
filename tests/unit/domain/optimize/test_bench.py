"""Unit tests for the pure percentile helper."""

from __future__ import annotations

import pytest

from conduit.domain.optimize.bench import percentile


def test_empty_is_zero() -> None:
    assert percentile([], 0.5) == 0.0


def test_single_value() -> None:
    assert percentile([7.0], 0.99) == 7.0


def test_interpolates_between_values() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)


def test_percentiles_are_monotonic() -> None:
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 0.5) <= percentile(values, 0.95) <= percentile(values, 0.99)
