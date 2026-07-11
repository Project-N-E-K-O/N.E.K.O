from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.core.contracts import InteractionResult
from plugin.plugins.neko_roast.core.runtime_active_engagement import (
    maybe_trigger_active_engagement,
    trigger_active_engagement,
)


class _Pipeline:
    def __init__(self) -> None:
        self.events = []

    async def handle_event(self, event):
        self.events.append(event)
        return InteractionResult(accepted=True, status="pushed", event=event)


class _Audit:
    def record(self, *_args, **_kwargs) -> None:
        return None


class _Runtime:
    def __init__(self, active_status: dict) -> None:
        self.config = SimpleNamespace(live_mode="solo_stream")
        self.pipeline = _Pipeline()
        self.audit = _Audit()
        self._active_status = dict(active_status)
        self._active_engagement_last_attempt_at = 0.0
        self._now = 100.0

    def _active_engagement_now(self) -> float:
        return self._now

    def _active_engagement_min_interval_seconds(self) -> float:
        return 60.0

    def live_connection_snapshot(self) -> dict:
        return {}

    def live_status_summary(self, _live_connection=None) -> dict:
        return {"summary": "ready_to_stream"}

    def runtime_health_rows(self) -> list[dict]:
        return []

    def live_state_summary(self, _live_status, _health_rows=None) -> dict:
        return {"state": "quiet", "mode": "solo_stream"}

    def active_engagement_status(self, _live_status, _live_state) -> dict:
        return dict(self._active_status)

    def record_result(self, result: InteractionResult) -> None:
        self.last_result = result

    async def _select_active_engagement_topic(self) -> dict:
        return {"title": "topic"}


@pytest.mark.asyncio
async def test_trigger_active_engagement_requires_full_eligibility():
    runtime = _Runtime({"candidate": True, "eligible": False, "reason": "minimum_interval"})

    result = await trigger_active_engagement(runtime)

    assert result.status == "skipped"
    assert result.reason == "active_engagement.minimum_interval"
    assert runtime.pipeline.events == []


@pytest.mark.asyncio
async def test_manual_trigger_active_engagement_records_success_cooldown():
    runtime = _Runtime({"candidate": True, "eligible": True, "reason": "eligible"})

    result = await trigger_active_engagement(runtime)

    assert result.status == "pushed"
    assert runtime._active_engagement_last_attempt_at == 100.0
    assert len(runtime.pipeline.events) == 1


@pytest.mark.asyncio
async def test_maybe_trigger_active_engagement_records_attempt_after_success():
    runtime = _Runtime({"candidate": True, "eligible": True, "reason": "eligible"})

    result = await maybe_trigger_active_engagement(runtime)

    assert result is not None
    assert result.status == "pushed"
    assert runtime._active_engagement_last_attempt_at == 100.0
    assert len(runtime.pipeline.events) == 1
