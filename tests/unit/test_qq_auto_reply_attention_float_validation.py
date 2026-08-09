"""Attention config floats must reject non-finite values (inf/-inf/NaN).

Old code ran float() straight into max()/min(), so inf survived into
_qq_settings and NaN compared falsy everywhere. _clamp_attention_float
rejects non-finite inputs with ValueError before clamping.
"""
from __future__ import annotations

import math

import pytest

from plugin.plugins.qq_auto_reply.settings_service import QQSettingsService


def test_accepts_finite_and_clamps_floor():
    assert QQSettingsService._clamp_attention_float(5.0, "x", floor=1.0) == 5.0
    assert QQSettingsService._clamp_attention_float(0.5, "x", floor=1.0) == 1.0


def test_accepts_finite_and_clamps_ceiling():
    assert QQSettingsService._clamp_attention_float(0.5, "x", floor=0.0, ceiling=1.0) == 0.5
    assert QQSettingsService._clamp_attention_float(3.0, "x", floor=0.0, ceiling=1.0) == 1.0


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_rejects_non_finite(bad):
    with pytest.raises(ValueError):
        QQSettingsService._clamp_attention_float(bad, "attention_fall_rate", floor=0.0)


def test_rejects_non_numeric():
    with pytest.raises(ValueError):
        QQSettingsService._clamp_attention_float("abc", "attention_fall_rate", floor=0.0)


class _Plugin:
    def __init__(self):
        self._qq_settings = {}
        self._emit_log = lambda *a, **k: None


@pytest.mark.parametrize(
    "kwarg",
    [
        {"attention_fall_rate": float("inf")},
        {"attention_base_rise_rate": float("-inf")},
        {"attention_consume_ratio": float("nan")},
        {"group_attention_max_score": float("inf")},
        {"attention_message_boost": float("nan")},
    ],
)
def test_save_settings_rejects_non_finite(kwarg):
    """save_settings raises ValueError for inf/-inf/NaN attention params, nothing persists."""
    import asyncio

    service = QQSettingsService(_Plugin())
    with pytest.raises(ValueError):
        asyncio.run(service._save_settings_locked(**kwarg))


