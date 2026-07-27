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

    def set_independent_asr_handshake(self, value) -> None:
        self.calls.append(("asr_handshake", value))

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
    # An audio-mode start_session is a voice engagement: the connection
    # identity claim must land before the legacy authorization check.
    assert call_names.index("begin") < call_names.index("authorize")
    assert call_names.index("authorize") < call_names.index("start_session")
    assert call_names.index("start_session") < call_names.index("stream_data")
    assert [
        payload for name, payload in manager.calls if name == "stream_data"
    ] == [pcm_message]
    assert manager.statuses == []
    assert manager.cleanup_calls == 1


@pytest.mark.asyncio
async def test_start_session_forwards_independent_asr_handshake_before_dispatch(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    websocket = _EventWebSocket(
        [
            {
                "action": "start_session",
                "input_type": "audio",
                "independent_asr_enabled": True,
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

    # The raw field is forwarded on every start_session; an absent field is
    # forwarded as None so a stale override from a previous session clears
    # (the manager-side setter owns the strict bool validation).
    assert [
        payload for name, payload in manager.calls if name == "asr_handshake"
    ] == [True, None]
    call_names = [name for name, _payload in manager.calls]
    assert call_names.index("asr_handshake") < call_names.index("start_session")


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
    # Game voice still engages the connection identity exactly once even
    # though it never claims the legacy core lease.
    assert [name for name, _payload in manager.calls].count("begin") == 1
    assert route_external_calls == [
        {"input_type": "audio", "stt_provider": "realtime"},
        {"input_type": "audio", "stt_provider": "realtime"},
    ]


_LEASE_SYNC_MESSAGE = {
    "action": "voice_input_control",
    "event": "lease_sync",
    "lease_generation": 1,
    "owner": "core",
    "hard_muted": False,
    "focus_suppressed": False,
}
_PCM_MESSAGE = {
    "action": "stream_data",
    "input_type": "audio",
    "sample_rate_hz": 16_000,
    "data": [1, -1],
}


class _TwoPhaseWebSocket(_EventWebSocket):
    """Socket that delivers a first burst, then holds until released.

    Models a still-open recording socket: the first-phase messages flow
    immediately, everything after (including the disconnect) waits for the
    test to set ``release``.
    """

    def __init__(self, first: list[dict], second: list[dict]) -> None:
        super().__init__(first + second)
        self.release = asyncio.Event()
        self._gate_after = len(first)
        self._delivered = 0

    async def receive(self) -> dict:
        if self._delivered == self._gate_after:
            await self.release.wait()
        self._delivered += 1
        return await super().receive()


async def _drain_until(predicate, *, attempts: int = 500) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition not reached while draining event loop")


@pytest.mark.asyncio
async def test_second_non_voice_socket_does_not_reset_voice_connection(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [],
    )
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=recording_socket,
    )

    recording_task = asyncio.create_task(
        websocket_router.websocket_endpoint(recording_socket, "Lan")
    )
    await _drain_until(
        lambda: "stream_data" in [name for name, _payload in manager.calls]
    )
    begins_mid_recording = [
        payload for name, payload in manager.calls if name == "begin"
    ]
    assert len(begins_mid_recording) == 1

    # A second window for the same character opens mid-recording and only
    # ever uses text chat: it must not claim the voice connection, so the
    # recording socket's lease/PCM state stays untouched.
    chat_socket = _EventWebSocket(
        [
            {"action": "start_session", "input_type": "text"},
            {"action": "ping"},
        ]
    )
    await websocket_router.websocket_endpoint(chat_socket, "Lan")

    # Negative validation: no additional identity reset happened at accept
    # or on any non-voice message of the second socket.
    assert [
        payload for name, payload in manager.calls if name == "begin"
    ] == begins_mid_recording
    assert "authorize" not in [name for name, _payload in manager.calls]
    assert json.loads(chat_socket.sent_text[-1]) == {"type": "pong"}

    recording_socket.release.set()
    await recording_task


@pytest.mark.asyncio
async def test_reconnect_socket_claims_voice_connection_on_first_lease_sync(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    first_socket = _EventWebSocket([_LEASE_SYNC_MESSAGE, _PCM_MESSAGE])
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=first_socket,
    )

    await websocket_router.websocket_endpoint(first_socket, "Lan")
    # Reconnect: the frontend force-sends lease_sync on open, which is the
    # engagement that claims the new connection identity.
    reconnect_socket = _EventWebSocket([_LEASE_SYNC_MESSAGE])
    await websocket_router.websocket_endpoint(reconnect_socket, "Lan")

    assert [name for name, _payload in manager.calls] == [
        "begin",
        "control",
        "stream_data",
        "begin",
        "control",
    ]
    begins = [payload for name, payload in manager.calls if name == "begin"]
    assert begins[0] != begins[1]


@pytest.mark.asyncio
async def test_stale_socket_after_voice_takeover_is_closed_without_reclaim(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    stale_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [_PCM_MESSAGE],
    )
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=stale_socket,
    )

    stale_task = asyncio.create_task(
        websocket_router.websocket_endpoint(stale_socket, "Lan")
    )
    await _drain_until(
        lambda: "stream_data" in [name for name, _payload in manager.calls]
    )

    # A newer, still-open socket engages voice: takeover semantics stay as
    # today (newest engaging connection wins).
    takeover_socket = _TwoPhaseWebSocket([_LEASE_SYNC_MESSAGE], [])
    takeover_task = asyncio.create_task(
        websocket_router.websocket_endpoint(takeover_socket, "Lan")
    )
    await _drain_until(
        lambda: [name for name, _payload in manager.calls].count("begin") == 2
    )

    # The superseded socket's next message is stale-closed before dispatch:
    # its PCM never reaches the manager and it cannot re-claim the identity.
    stale_socket.release.set()
    await stale_task
    takeover_socket.release.set()
    await takeover_task

    assert stale_socket.closed is True
    call_names = [name for name, _payload in manager.calls]
    assert call_names.count("begin") == 2
    assert call_names.count("stream_data") == 1
    assert {
        "code": "CHARACTER_SWITCHING_TERMINAL",
        "details": {"name": "Lan"},
    } in manager.statuses


@pytest.mark.asyncio
async def test_binary_pcm_frame_claims_voice_connection_before_dispatch(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    websocket = _EventWebSocket([])
    websocket.events.insert(
        0,
        {
            "type": "websocket.receive",
            "bytes": struct.pack("<4sI2h", b"NEKO", 16_000, 1, -1),
        },
    )
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    call_names = [name for name, _payload in manager.calls]
    assert call_names.index("begin") < call_names.index("stream_data")
    assert [
        payload for name, payload in manager.calls if name == "stream_data"
    ] == [
        {
            "action": "stream_data",
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1, -1],
        }
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
