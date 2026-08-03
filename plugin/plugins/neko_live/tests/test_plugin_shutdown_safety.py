from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.core.runtime import LiveRuntime
from plugin.plugins.neko_live.core.runtime_live_session import invalidate_live_session


@pytest.mark.asyncio
async def test_stop_removes_instruction_overlays_without_response_callbacks(
    runtime: LiveRuntime,
) -> None:
    runtime.instructions_injected = True
    runtime.instructions_signature = "active-scene"
    runtime.developer_instructions_injected = True

    await runtime.stop()

    assert len(runtime.plugin.pushed_messages) == 2
    assert {
        message["metadata"]["context_type"]
        for message in runtime.plugin.pushed_messages
    } == {"developer_mode", "live_scene"}
    assert all(
        message["metadata"]["delivery_intent"] == "passive_context"
        and message["metadata"]["context_expired"] is True
        and message["ai_behavior"] == "read"
        and message["visibility"] == []
        for message in runtime.plugin.pushed_messages
    )
    assert runtime.instructions_injected is False
    assert runtime.instructions_signature == ""
    assert runtime.developer_instructions_injected is False


def test_shutdown_invalidation_still_requests_replaceable_context_cleanup() -> None:
    reset_calls: list[dict[str, object]] = []
    runtime = SimpleNamespace(
        _live_session_generation=3,
        _stopping=True,
        live_audience_session=SimpleNamespace(finish_session=lambda: None),
        live_events=SimpleNamespace(reset=lambda **kwargs: reset_calls.append(kwargs)),
        live_support_events=SimpleNamespace(reset=lambda: None),
    )

    invalidate_live_session(runtime)

    assert reset_calls == [{}]
