from __future__ import annotations

import math

import pytest

from plugin.server.requests.common import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    coerce_timeout,
)


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (True, DEFAULT_TIMEOUT_SECONDS),
        (False, DEFAULT_TIMEOUT_SECONDS),
        (0, DEFAULT_TIMEOUT_SECONDS),
        (-1, DEFAULT_TIMEOUT_SECONDS),
        (1, 1.0),
        (10.5, 10.5),
        ("bad", DEFAULT_TIMEOUT_SECONDS),
    ],
)
def test_coerce_timeout_basic(raw: object, expected: float) -> None:
    assert coerce_timeout(raw) == expected


@pytest.mark.plugin_unit
def test_coerce_timeout_clamps_upper_bound() -> None:
    assert coerce_timeout(MAX_TIMEOUT_SECONDS + 1000) == MAX_TIMEOUT_SECONDS


@pytest.mark.plugin_unit
@pytest.mark.parametrize("raw", [math.inf, -math.inf, math.nan])
def test_coerce_timeout_rejects_non_finite(raw: float) -> None:
    assert coerce_timeout(raw) == DEFAULT_TIMEOUT_SECONDS

