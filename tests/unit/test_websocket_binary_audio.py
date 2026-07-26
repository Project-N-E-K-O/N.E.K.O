from __future__ import annotations

import asyncio
import json
import struct

import pytest

import main_routers.websocket_router as websocket_router
from main_routers.websocket_router import _decode_binary_audio_frame


class _ProtocolManager:
    def __init__(
        self,
        *,
        authorization_result: bool = True,
        control_result: bool = True,
    ) -> None:
        self.pending_agent_callbacks = []
        self.websocket = None
        self.active_session_is_idle = True
        self._starting_session_count = 0
        self.authorization_result = authorization_result
        self.control_result = control_result
        self.calls: list[tuple[str, object]] = []
        self.statuses: list[dict] = []
        self.cleanup_calls = 0

    def _begin_voice_input_connection(self, connection_id: str) -> None:
        self.calls.append(("begin", connection_id))

    async def _ensure_voice_input_session_authorized(
        self,
        connection_id: str,
    ) -> bool:
        self.calls.append(("authorize", connection_id))
        return self.authorization_result

    async def _handle_voice_input_control(self, event: str, generation, **kwargs) -> bool:
        self.calls.append(
            (
                "control",
                {
                    "event": event,
                    "generation": generation,
                    **kwargs,
                },
            )
        )
        return self.control_result

    def set_goodbye_silent(self, active: bool, reason: str) -> None:
        self.calls.append(("goodbye", (active, reason)))

    def reset_session_start_circuit(self) -> None:
        self.calls.append(("reset_start_circuit", None))

    def start_session(self, *_args, **_kwargs):
        self.calls.append(("start_session", None))

        async def _complete() -> None:
            return None

        return _complete()

    async def stream_data(self, message: dict) -> None:
        self.calls.append(("stream_data", message))

    async def send_status(self, payload: str) -> None:
        self.statuses.append(json.loads(payload))

    async def cleanup(self, *, expected_websocket) -> None:
        assert expected_websocket is self.websocket
        self.cleanup_calls += 1


class _EventWebSocket:
    client = "test-client"

    def __init__(self, messages: list[dict]) -> None:
        self.events = [
            {
                "type": "websocket.receive",
                "text": json.dumps(message),
            }
            for message in messages
        ]
        self.events.append({"type": "websocket.disconnect", "code": 1000})
        self.sent_text: list[str] = []
        self.closed = False

    async def accept(self) -> None:
        return None

    async def receive(self) -> dict:
        await asyncio.sleep(0)
        return self.events.pop(0)

    async def send_text(self, payload: str) -> None:
        self.sent_text.append(payload)

    async def close(self) -> None:
        self.closed = True


def _install_protocol_endpoint(
    monkeypatch,
    *,
    manager: _ProtocolManager,
    websocket: _EventWebSocket,
    game_active: bool = False,
):
    session_ids: dict[str, object] = {}
    route_external_calls: list[dict] = []

    async def _route_external(_name: str, message: dict):
        route_external_calls.append(message)
        return False

    monkeypatch.setattr(websocket_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(
        websocket_router,
        "get_session_manager",
        lambda: {"Lan": manager},
    )
    monkeypatch.setattr(
        websocket_router,
        "get_session_id",
        lambda: session_ids,
    )
    monkeypatch.setattr(
        websocket_router,
        "is_game_route_active",
        lambda _name: game_active,
    )
    monkeypatch.setattr(
        websocket_router,
        "route_external_stream_message",
        _route_external,
    )
    return session_ids, route_external_calls


def test_binary_audio_frame_decodes_pcm_and_sample_rate() -> None:
    payload = struct.pack("<4sI3h", b"NEKO", 48_000, 1, -2, 3)

    message = _decode_binary_audio_frame(payload)

    assert message == {
        "action": "stream_data",
        "input_type": "audio",
        "sample_rate_hz": 48_000,
        "data": [1, -2, 3],
    }


def test_binary_audio_frame_decodes_extreme_sample_values() -> None:
    samples = [-32_768, 32_767, 0, -1, 1]
    payload = struct.pack("<4sI5h", b"NEKO", 16_000, *samples)

    assert _decode_binary_audio_frame(payload)["data"] == samples


@pytest.mark.parametrize(
    "payload",
    [
        b"bad",
        struct.pack("<4sIh", b"FAIL", 16_000, 1),
        struct.pack("<4sIh", b"NEKO", 44_100, 1),
        struct.pack("<4sI", b"NEKO", 16_000) + b"\x00",
    ],
)
def test_binary_audio_frame_rejects_invalid_contract(payload: bytes) -> None:
    with pytest.raises(ValueError, match="VOICE_BINARY_FRAME_INVALID"):
        _decode_binary_audio_frame(payload)


def test_binary_audio_frame_rejects_more_than_one_second_before_pcm_unpack() -> None:
    payload = struct.pack("<4sI", b"NEKO", 48_000) + (b"\x00\x00" * 48_001)

    with pytest.raises(ValueError, match="VOICE_BINARY_FRAME_INVALID: frame is too large"):
        _decode_binary_audio_frame(payload)


@pytest.mark.asyncio
async def test_websocket_drops_bad_binary_frame_and_processes_next_message(
    monkeypatch,
) -> None:
    class _Manager:
        def __init__(self) -> None:
            self.pending_agent_callbacks = []
            self.websocket = None
            self.cleanup_calls = 0

        def _begin_voice_input_connection(self, _connection_id: str) -> None:
            return None

        async def cleanup(self, *, expected_websocket) -> None:
            assert expected_websocket is websocket
            self.cleanup_calls += 1

    class _WebSocket:
        client = "test-client"

        def __init__(self) -> None:
            self.events = [
                {"type": "websocket.receive", "bytes": b"bad"},
                {
                    "type": "websocket.receive",
                    "text": json.dumps({"action": "ping"}),
                },
                {"type": "websocket.disconnect", "code": 1000},
            ]
            self.sent_text: list[str] = []

        async def accept(self) -> None:
            return None

        async def receive(self) -> dict:
            return self.events.pop(0)

        async def send_text(self, payload: str) -> None:
            self.sent_text.append(payload)

    manager = _Manager()
    websocket = _WebSocket()
    session_ids: dict[str, object] = {}
    monkeypatch.setattr(websocket_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(
        websocket_router,
        "get_session_manager",
        lambda: {"Lan": manager},
    )
    monkeypatch.setattr(
        websocket_router,
        "get_session_id",
        lambda: session_ids,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    assert [json.loads(payload) for payload in websocket.sent_text] == [
        {"type": "pong"}
    ]
    assert manager.cleanup_calls == 1


@pytest.mark.asyncio
async def test_documented_legacy_audio_flow_authorizes_before_session_and_pcm(
    monkeypatch,
) -> None:
    pcm_message = {
        "action": "stream_data",
        "input_type": "audio",
        "sample_rate_hz": 16_000,
        "data": [1, -1],
    }
    manager = _ProtocolManager()
    websocket = _EventWebSocket(
        [
            {"action": "start_session", "input_type": "audio"},
            pcm_message,
        ]
    )
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    call_names = [name for name, _payload in manager.calls]
    assert call_names.index("authorize") < call_names.index("start_session")
    assert call_names.index("start_session") < call_names.index("stream_data")
    assert [
        payload for name, payload in manager.calls if name == "stream_data"
    ] == [pcm_message]
    assert manager.statuses == []
    assert manager.cleanup_calls == 1


@pytest.mark.asyncio
async def test_explicit_voice_control_stays_on_authoritative_path(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    websocket = _EventWebSocket(
        [
            {
                "action": "voice_input_control",
                "event": "lease_sync",
                "lease_generation": 1,
                "owner": "core",
                "hard_muted": False,
                "focus_suppressed": False,
            },
            {"action": "start_session", "input_type": "audio"},
        ]
    )
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    assert [name for name, _payload in manager.calls].count("control") == 1
    assert [name for name, _payload in manager.calls].count("authorize") == 1
    assert [name for name, _payload in manager.calls].count("start_session") == 1
    assert manager.statuses == []


@pytest.mark.asyncio
async def test_voice_input_control_noops_for_manager_without_mixin_hook(
    monkeypatch,
) -> None:
    class _MixinlessManager:
        def __init__(self) -> None:
            self.pending_agent_callbacks = []
            self.websocket = None
            self.statuses: list[dict] = []
            self.cleanup_calls = 0

        async def send_status(self, payload: str) -> None:
            self.statuses.append(json.loads(payload))

        async def cleanup(self, *, expected_websocket) -> None:
            assert expected_websocket is websocket
            self.cleanup_calls += 1

    manager = _MixinlessManager()
    websocket = _EventWebSocket(
        [
            {
                "action": "voice_input_control",
                "event": "lease_sync",
                "lease_generation": 1,
            },
            {"action": "ping"},
        ]
    )
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    # Without the getattr guard the missing hook raises AttributeError, the
    # loop dies with SERVER_ERROR and the trailing ping never gets its pong.
    assert [json.loads(payload) for payload in websocket.sent_text] == [
        {"type": "pong"}
    ]
    assert manager.statuses == []
    assert manager.cleanup_calls == 1


@pytest.mark.asyncio
async def test_rejected_control_and_unauthorized_start_report_fixed_statuses(
    monkeypatch,
) -> None:
    manager = _ProtocolManager(
        authorization_result=False,
        control_result=False,
    )
    websocket = _EventWebSocket(
        [
            {
                "action": "voice_input_control",
                "event": "invalid",
                "lease_generation": 0,
            },
            {"action": "start_session", "input_type": "audio"},
        ]
    )
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    assert "start_session" not in [name for name, _payload in manager.calls]
    assert manager.statuses == [
        {
            "code": "VOICE_INPUT_CONTROL_REJECTED",
            "details": {"reason": "invalid_or_stale_control"},
        },
        {
            "code": "VOICE_INPUT_LEASE_REQUIRED",
            "details": {"reason": "voice_input_control_required"},
        },
    ]


@pytest.mark.asyncio
async def test_game_audio_route_never_claims_legacy_core_lease(
    monkeypatch,
) -> None:
    pcm_message = {
        "action": "stream_data",
        "input_type": "audio",
        "data": [1, -1],
    }
    manager = _ProtocolManager()
    websocket = _EventWebSocket(
        [
            {"action": "start_session", "input_type": "audio"},
            pcm_message,
        ]
    )
    _session_ids, route_external_calls = _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
        game_active=True,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    assert "authorize" not in [name for name, _payload in manager.calls]
    assert route_external_calls == [
        {"input_type": "audio", "stt_provider": "realtime"},
        {"input_type": "audio", "stt_provider": "realtime"},
    ]


@pytest.mark.asyncio
async def test_replaced_socket_cannot_authorize_or_start_audio(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    websocket = _EventWebSocket(
        [{"action": "start_session", "input_type": "audio"}]
    )
    session_ids, _route_external_calls = _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
    )
    original_receive = websocket.receive

    async def _replace_connection_before_dispatch() -> dict:
        event = await original_receive()
        if event.get("type") == "websocket.receive":
            session_ids["Lan"] = object()
        return event

    websocket.receive = _replace_connection_before_dispatch

    await websocket_router.websocket_endpoint(websocket, "Lan")

    assert "authorize" not in [name for name, _payload in manager.calls]
    assert "start_session" not in [name for name, _payload in manager.calls]
    assert websocket.closed is True
