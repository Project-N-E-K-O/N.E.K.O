from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest


from plugin.plugins.sts2_autoplay.tests._isolated_loader import load_isolated_sts2_module


PROJECT_ROOT = Path(__file__).resolve().parents[4]


ContextFlowMixin = load_isolated_sts2_module("sts2_context_flow_test_pkg", "context_flow").ContextFlowMixin


class ContextFlowService(ContextFlowMixin):
    def __init__(self) -> None:
        self._cfg: dict[str, Any] = {}


def run(coro):
    return asyncio.run(coro)


@pytest.mark.unit
def test_action_interval_uses_default_when_config_is_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    service = ContextFlowService()
    service._cfg = {"action_interval_seconds": "bad"}
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    run(service._await_action_interval())

    assert sleeps == [0.5]


@pytest.mark.unit
def test_stable_step_config_helpers_fallback_on_invalid_values() -> None:
    service = ContextFlowService()
    service._cfg = {
        "stable_state_attempts": "bad",
        "poll_interval_active_seconds": "bad",
        "post_action_settle_attempts": "bad",
        "post_action_delay_seconds": "bad",
    }

    assert service._safe_config_int("stable_state_attempts", 4) == 4
    assert service._safe_config_float("poll_interval_active_seconds", 1.0) == 1.0
    assert service._safe_config_int("post_action_settle_attempts", 6) == 6
    assert service._safe_config_float("post_action_delay_seconds", 0.5) == 0.5
