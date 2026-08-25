"""Attention config floats must reject non-finite values (inf/-inf/NaN).

Old code ran float() straight into max()/min(), so inf survived into
_qq_settings and NaN compared falsy everywhere. _clamp_attention_float
rejects non-finite inputs with ValueError before clamping.
"""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    service = QQSettingsService(_Plugin())
    with pytest.raises(ValueError):
        asyncio.run(service._save_settings_locked(**kwarg))


def _save_locked(**kwargs) -> dict:
    """Run ``_save_settings_locked`` through a real success path; return the resulting settings.

    The send-threshold ceiling depends on the focus line already sitting in
    ``_qq_settings`` (same batch or stored), so the whole save path must run.
    """
    plugin = SimpleNamespace(
        _qq_settings={
            "group_attention_focus_threshold": 4.0,
            "group_attention_focus_send_threshold": 2.0,
            "group_attention_min_threshold": 1.0,
            "group_attention_max_score": 10.0,
        },
        _user_sessions={},
        _emit_log=lambda *a, **k: None,
        logger=MagicMock(),
        attention_service=None,
        qq_client=None,
        _running=False,
        _startup_error=None,
        _strategy_mode="",
        _ensure_qq_client_initialized=lambda: None,
    )
    service = QQSettingsService.__new__(QQSettingsService)
    service.plugin = plugin
    service._enforce_attention_for_dynamic_mode = lambda: None
    service._stamp_group_memory_transition = lambda *, enabled_after: None
    service._spawn_group_memory_sync_task = lambda coro: coro.close()

    async def _persist(overlay=None):
        return True

    service.persist_business_config = _persist
    asyncio.run(service._save_settings_locked(**kwargs))
    return plugin._qq_settings


def test_save_settings_clamps_send_threshold_to_focus_ceiling():
    """A send line saved above the focus line must be clamped down to the focus line."""
    settings = _save_locked(
        group_attention_focus_threshold=4.0,
        group_attention_focus_send_threshold=8.0,
    )
    assert settings["group_attention_focus_threshold"] == 4.0
    assert settings["group_attention_focus_send_threshold"] == 4.0


def test_save_settings_keeps_send_threshold_below_focus():
    """A send line already below the focus line is left untouched."""
    settings = _save_locked(
        group_attention_focus_threshold=4.0,
        group_attention_focus_send_threshold=2.5,
    )
    assert settings["group_attention_focus_send_threshold"] == 2.5


def test_save_settings_clamps_stored_send_when_focus_lowered():
    """Lowering the focus line below a stored higher send line pulls the send line down."""
    settings = _save_locked(group_attention_focus_threshold=1.5)
    assert settings["group_attention_focus_threshold"] == 1.5
    assert settings["group_attention_focus_send_threshold"] == 1.5


def test_save_settings_same_request_uses_new_focus_ceiling():
    """Both keys in one batch: the send line clamps against the new focus line."""
    settings = _save_locked(
        group_attention_focus_threshold=2.0,
        group_attention_focus_send_threshold=9.0,
    )
    assert settings["group_attention_focus_threshold"] == 2.0
    assert settings["group_attention_focus_send_threshold"] == 2.0


