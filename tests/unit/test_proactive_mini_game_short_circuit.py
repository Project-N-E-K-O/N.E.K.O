"""Tests for mini-game invite entry and short-circuit orchestration."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.proactive_chat import contracts
from main_logic.proactive_chat import mini_game_invite as invites
from main_logic.proactive_chat import service as proactive_service
from main_logic.session_state import SessionEvent
from main_routers import game_router
from main_routers.system_router import proactive_chat_flow
from utils import preferences


def test_last_user_message_at_is_derived_from_activity_snapshot() -> None:
    snapshot = SimpleNamespace(seconds_since_user_msg=12.5)

    assert invites._last_user_message_at_from_activity(
        snapshot,
        now=100.0,
    ) == 87.5
    assert invites._last_user_message_at_from_activity(None, now=100.0) is None
    assert invites._last_user_message_at_from_activity(
        SimpleNamespace(seconds_since_user_msg=None),
        now=100.0,
    ) is None


def test_entry_advance_normalizes_resolved_notification(monkeypatch) -> None:
    monkeypatch.setattr(
        invites,
        "_mini_game_invite_advance_response",
        lambda lanlan_name, last_user_msg_at: {
            "session_id": "invite-1",
            "action": "suppress",
        },
    )

    result = invites._advance_mini_game_invite_entry("Yui", 123.0)

    assert result == invites.MiniGameInviteEntryAdvance(
        session_id="invite-1",
        action="suppress",
    )


@pytest.mark.parametrize("outcome", (None, {}, {"action": "suppress"}))
def test_entry_advance_ignores_outcomes_without_session(monkeypatch, outcome) -> None:
    monkeypatch.setattr(
        invites,
        "_mini_game_invite_advance_response",
        lambda lanlan_name, last_user_msg_at: outcome,
    )

    assert invites._advance_mini_game_invite_entry("Yui", 123.0) is None


@pytest.mark.asyncio
async def test_short_circuit_builds_options_for_delivered_invite(monkeypatch) -> None:
    async def fake_deliver(**kwargs):
        return contracts._proactive_chat_body(
            message="mini-game invite delivered",
            channel="mini_game",
            game_type="soccer",
            invite_session_id="invite-1",
        )

    monkeypatch.setattr(invites, "_attempt_mini_game_invite_delivery", fake_deliver)

    short_circuit = await invites._run_mini_game_invite_short_circuit(
        lanlan_name="Yui",
        mgr=object(),
        activity_snapshot=object(),
        invite_lang="zh",
        master_name="博士",
    )

    assert short_circuit is not None
    assert short_circuit.result.status_code == 200
    assert short_circuit.result.body["action"] == "chat"
    assert short_circuit.options_payload is not None
    assert short_circuit.options_payload["type"] == "mini_game_invite_options"
    assert short_circuit.options_payload["session_id"] == "invite-1"
    assert short_circuit.options_payload["game_type"] == "soccer"


@pytest.mark.asyncio
async def test_short_circuit_preserves_pass_without_options(monkeypatch) -> None:
    body = contracts._proactive_pass_body(
        contracts.PROACTIVE_REASON_PASS_DELIVERY_BUSY,
        message="busy",
    )

    async def fake_deliver(**kwargs):
        return body

    monkeypatch.setattr(invites, "_attempt_mini_game_invite_delivery", fake_deliver)

    short_circuit = await invites._run_mini_game_invite_short_circuit(
        lanlan_name="Yui",
        mgr=object(),
        activity_snapshot=object(),
        invite_lang="zh",
        master_name="博士",
    )

    assert short_circuit is not None
    assert short_circuit.result.body is body
    assert short_circuit.options_payload is None


@pytest.mark.asyncio
async def test_short_circuit_returns_none_when_invite_does_not_fire(monkeypatch) -> None:
    async def fake_deliver(**kwargs):
        return None

    monkeypatch.setattr(invites, "_attempt_mini_game_invite_delivery", fake_deliver)

    assert await invites._run_mini_game_invite_short_circuit(
        lanlan_name="Yui",
        mgr=object(),
        activity_snapshot=object(),
        invite_lang="zh",
        master_name="博士",
    ) is None


@pytest.mark.asyncio
async def test_short_circuit_forwards_injected_persistence_root(
    monkeypatch,
    tmp_path,
) -> None:
    attempt = AsyncMock(return_value=None)
    monkeypatch.setattr(invites, "_attempt_mini_game_invite_delivery", attempt)

    await invites._run_mini_game_invite_short_circuit(
        lanlan_name="Yui",
        mgr=object(),
        activity_snapshot=object(),
        invite_lang="zh",
        master_name="Master",
        memory_dir=tmp_path,
    )

    assert attempt.await_args.kwargs["memory_dir"] == tmp_path


@pytest.mark.asyncio
async def test_router_adapter_sends_options_payload() -> None:
    send_json = AsyncMock()
    mgr = SimpleNamespace(
        websocket=SimpleNamespace(
            send_json=send_json,
            client_state=None,
        )
    )
    payload = {"type": "mini_game_invite_options", "session_id": "invite-1"}

    await proactive_chat_flow._push_mini_game_invite_options(mgr, payload)

    send_json.assert_awaited_once_with(payload)


@pytest.mark.asyncio
async def test_router_adapter_skips_falsey_websocket() -> None:
    class FalseyWebSocket(SimpleNamespace):
        def __bool__(self) -> bool:
            return False

    send_json = AsyncMock()
    mgr = SimpleNamespace(
        websocket=FalseyWebSocket(send_json=send_json, client_state=None),
    )

    await proactive_chat_flow._push_mini_game_invite_options(mgr, {"type": "x"})

    send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_adapter_skips_unavailable_websockets() -> None:
    disconnected_state = SimpleNamespace(CONNECTED=object())
    disconnected_send = AsyncMock()
    managers = (
        SimpleNamespace(websocket=None),
        SimpleNamespace(websocket=SimpleNamespace()),
        SimpleNamespace(
            websocket=SimpleNamespace(
                send_json=disconnected_send,
                client_state=disconnected_state,
            )
        ),
    )

    for mgr in managers:
        await proactive_chat_flow._push_mini_game_invite_options(
            mgr,
            {"type": "x"},
        )

    disconnected_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_adapter_preserves_missing_websocket_attribute_error() -> None:
    with pytest.raises(AttributeError):
        await proactive_chat_flow._push_mini_game_invite_options(
            SimpleNamespace(),
            {"type": "x"},
        )


@pytest.mark.asyncio
async def test_router_adapter_propagates_send_errors_to_business_caller() -> None:
    send_error = RuntimeError("send failed")
    mgr = SimpleNamespace(
        websocket=SimpleNamespace(
            send_json=AsyncMock(side_effect=send_error),
            client_state=None,
        )
    )

    with pytest.raises(RuntimeError, match="send failed"):
        await proactive_chat_flow._push_mini_game_invite_options(
            mgr,
            {"type": "x"},
        )


@pytest.mark.parametrize(
    "send_error",
    (None, RuntimeError("send failed")),
    ids=("send-ok", "send-failed"),
)
@pytest.mark.asyncio
async def test_router_short_circuit_returns_chat_and_pushes_options_once(
    monkeypatch,
    send_error,
    tmp_path: Path,
) -> None:
    """The real Router wiring adapts one short circuit into HTTP + one WS event."""
    payload = contracts._proactive_chat_body(
        message="mini-game invite delivered",
        channel="mini_game",
        game_type="soccer",
        invite_session_id="invite-1",
    )
    short_circuit = invites.MiniGameInviteShortCircuit(
        result=contracts.ProactiveChatResult(body=payload),
        options_payload={
            "type": "mini_game_invite_options",
            "session_id": "invite-1",
            "game_type": "soccer",
            "options": [],
        },
    )
    send_json = AsyncMock(side_effect=send_error)
    warning = MagicMock()
    state = SimpleNamespace(
        try_start_proactive=AsyncMock(return_value=True),
        fire=AsyncMock(),
    )
    snapshot = SimpleNamespace(
        state="casual_browsing",
        propensity="open",
        propensity_reasons=[],
        skip_probability=0.0,
        tone="casual",
        seconds_since_user_msg=None,
        unfinished_thread=None,
        anti_slack_pending=None,
        work_break_pending=None,
    )

    class InactiveManager(SimpleNamespace):
        bool_reads = 0
        active_reads = 0
        session_reads = 0

        def __bool__(self):
            self.bool_reads += 1
            return True

        @property
        def is_active(self):
            self.active_reads += 1
            return False

        @property
        def session(self):
            self.session_reads += 1
            return None

    mgr = InactiveManager(
        state=state,
        websocket=SimpleNamespace(send_json=send_json, client_state=None),
        _activity_tracker=SimpleNamespace(
            get_snapshot=AsyncMock(return_value=snapshot),
        ),
        is_goodbye_silent=lambda: False,
    )
    config_manager = SimpleNamespace(
        memory_dir=tmp_path,
        aget_character_data=AsyncMock(
            return_value=("博士", "Yui", None, None, None, {}, None, None, None),
        ),
    )
    session_manager = SimpleNamespace(get=lambda lanlan_name: mgr)
    request = SimpleNamespace(
        json=AsyncMock(
            return_value={
                "lanlan_name": "Yui",
                "language": "zh",
                "enabled_modes": ["home"],
            },
        ),
    )

    monkeypatch.setattr(
        proactive_chat_flow,
        "_validate_local_mutation_request",
        lambda request: None,
    )
    monkeypatch.setattr(
        proactive_chat_flow,
        "get_config_manager",
        lambda: config_manager,
    )
    monkeypatch.setattr(
        proactive_chat_flow,
        "get_session_manager",
        lambda: session_manager,
    )
    monkeypatch.setattr(game_router, "is_game_route_active", lambda name: False)
    monkeypatch.setattr(
        preferences,
        "ais_privacy_mode_enabled",
        AsyncMock(return_value=False),
    )
    invite_runner = AsyncMock(return_value=short_circuit)
    monkeypatch.setattr(
        proactive_chat_flow,
        "_run_mini_game_invite_short_circuit",
        invite_runner,
    )
    monkeypatch.setattr(proactive_service.logger, "warning", warning)

    response = await proactive_chat_flow.proactive_chat(request)

    assert response.status_code == 200
    body = json.loads(response.body)
    assert body["action"] == "chat"
    assert body["invite_session_id"] == "invite-1"
    assert body["next_schedule_fixed_mode"] is False
    send_json.assert_awaited_once_with(short_circuit.options_payload)
    assert invite_runner.await_args.kwargs["memory_dir"] == tmp_path
    if send_error is not None:
        warning.assert_any_call(
            "[%s] mini-game invite options WS push failed: %s",
            "Yui",
            send_error,
        )
    state.fire.assert_awaited_once_with(SessionEvent.PROACTIVE_DONE)
    assert mgr.bool_reads == 1
    assert mgr.active_reads == 1
    assert mgr.session_reads == 0
