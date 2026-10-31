from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from main_logic import core
from main_logic.agent_event_bus import conversation_reply_origin
from tests.unit.test_offline_late_completion_turn_ownership import (
    _client, _make_callback_media_manager, _observe, _text, _wire,
)
from tests.unit.test_proactive_sm_integration import _FakeOmniOffline, _make_mgr

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _callback(channel, source_kind, source_name="", *, origin="event", task_id=""):
    return {
        "channel": channel,
        "source_kind": source_kind,
        "source_name": source_name,
        "origin": origin,
        "task_id": task_id,
        "status": "completed",
        "summary": "callback text",
    }


@pytest.mark.parametrize(
    ("callbacks", "expected"),
    [
        ([_callback("plugin:alpha", "plugin", "alpha", task_id="untrusted")], ("plugin", "alpha", None)),
        ([_callback("plugin:alpha", "plugin", "alpha"), _callback("plugin:beta", "plugin", "beta")], ("plugin", None, None)),
        ([_callback("plugin:alpha", "plugin", "alpha"), _callback("computer_use", "cu", origin="task_result", task_id="t1")], ("agent", None, None)),
        ([_callback("computer_use", "cu", origin="task_result", task_id="t1"), _callback("computer_use", "cu", origin="task_result", task_id="t1")], ("agent", None, "t1")),
        ([_callback("computer_use", "cu", origin="task_result", task_id="t1"), _callback("computer_use", "cu", origin="task_result", task_id="t2")], ("agent", None, None)),
        ([_callback("plugin:alpha", "plugin", "alpha", origin="task_result", task_id="forged")], ("plugin", "alpha", None)),
    ],
)
async def test_final_injected_callback_batch_sets_evidenced_origin(callbacks, expected):
    snapshots = []
    class Session(_FakeOmniOffline):
        async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None, on_committed_text=None):
            snapshots.append(dict(conversation_reply_origin.get()))
            if on_committed:
                on_committed()
            return True
    manager = _make_mgr(session=Session())
    assert await core.LLMSessionManager._deliver_agent_callbacks_text(manager, list(callbacks))
    value = snapshots[0]
    assert (value["kind"], value["plugin_id"], value["task_id"]) == expected
    assert value["is_proactive"] is True
    assert conversation_reply_origin.get() is None


async def test_user_input_with_inline_agent_context_remains_user_interaction(monkeypatch):
    import main_logic.core.turn as turn_module

    monkeypatch.setattr("utils.token_tracker.TokenTracker.get_instance", MagicMock())
    facts = []
    monkeypatch.setattr(turn_module, "schedule_conversation_fact_observed_best_effort",
                        lambda name, *, fact, payload: facts.append((fact, payload)))
    client = _client([[_text("answer"), _text("", "stop")]])
    manager = _observe(_make_callback_media_manager(client))
    del manager.send_lanlan_response
    manager.pending_agent_callbacks = [_callback("computer_use", "cu", origin="task_result", task_id="task")]
    manager._fire_task = lambda coro: coro.close()
    _wire(manager, client)
    await core.LLMSessionManager._process_stream_data_internal(
        manager, {"input_type": "text", "data": "question", "source": "react-chat-window"},
    )
    started = next(payload for fact, payload in facts if fact == "reply.started")
    assert started["origin"]["kind"] == "user_interaction"
    assert not manager.pending_agent_callbacks


async def test_origin_uses_only_surviving_callbacks():
    from main_logic.proactive_delivery import DELIVERY_RETRACTED_KEY

    captured = []
    class Session(_FakeOmniOffline):
        async def prompt_ephemeral(self, instruction, *, images=None, on_committed=None, on_committed_text=None):
            captured.append(dict(conversation_reply_origin.get()))
            if on_committed:
                on_committed()
            return True
    manager = _make_mgr(session=Session())
    retired = _callback("computer_use", "cu", origin="task_result", task_id="old")
    retired[DELIVERY_RETRACTED_KEY] = True
    active = _callback("plugin:alpha", "plugin", "alpha")
    assert await core.LLMSessionManager._deliver_agent_callbacks_text(manager, [retired, active])
    assert captured[0]["kind"] == "plugin"
    assert captured[0]["plugin_id"] == "alpha"
    assert captured[0]["task_id"] is None
