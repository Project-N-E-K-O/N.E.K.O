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
        # Mirrors the real manager: set in start_session's first prepare phase,
        # ahead of both the session install and the ASR route resolution.
        self.input_mode = "audio"
        self.session = object()
        self._starting_session_count = 0
        self.authorization_result = authorization_result
        self.control_result = control_result
        self.calls: list[tuple[str, object]] = []
        self.statuses: list[dict] = []
        self.cleanup_calls = 0
        # Mirrors the real MicLease mixin: the currently claimed voice
        # connection identity, moved on every engagement claim.
        self._voice_lease_connection_id = ""

    def _begin_voice_input_connection(self, connection_id: str) -> None:
        self.calls.append(("begin", connection_id))
        self._voice_lease_connection_id = connection_id

    async def _revoke_voice_input_connection(self, connection_id: str) -> bool:
        # Mirrors the real mixin: only the current holder may revoke.
        if connection_id != self._voice_lease_connection_id:
            self.calls.append(("revoke_rejected", connection_id))
            return False
        self.calls.append(("revoke", connection_id))
        self._voice_lease_connection_id = ""
        return True

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

    def set_voice_input_resource_optimization_handshake(self, value) -> None:
        self.calls.append(("resource_optimization_handshake", value))

    def start_session(self, *_args, **_kwargs):
        self.calls.append(("start_session", None))

        async def _complete() -> None:
            return None

        return _complete()

    async def stream_data(self, message: dict) -> None:
        self.calls.append(("stream_data", message))

    async def end_session(self, *_args, **_kwargs) -> None:
        self.calls.append(("end_session", None))

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


@pytest.mark.parametrize(
    "sample_rate_hz, samples",
    [(16_000, 1_921), (48_000, 5_761)],
)
def test_binary_audio_frame_rejects_an_oversized_frame_before_pcm_unpack(
    sample_rate_hz: int,
    samples: int,
) -> None:
    # Literal bounds on purpose: deriving them from the constant would make the
    # test pass against any value of it. The real worklet frame is 512 samples
    # at 16 kHz / 480 at 48 kHz, so these are already 4-12x oversized.
    payload = struct.pack("<4sI", b"NEKO", sample_rate_hz) + (b"\x00\x00" * samples)

    with pytest.raises(ValueError, match="VOICE_BINARY_FRAME_INVALID: frame is too large"):
        _decode_binary_audio_frame(payload)


@pytest.mark.parametrize(
    "sample_rate_hz, samples",
    [(16_000, 512), (48_000, 480), (16_000, 1_920), (48_000, 5_760)],
)
def test_binary_audio_frame_accepts_real_and_boundary_frame_sizes(
    sample_rate_hz: int,
    samples: int,
) -> None:
    payload = struct.pack("<4sI", b"NEKO", sample_rate_hz) + (b"\x01\x00" * samples)

    message = _decode_binary_audio_frame(payload)

    assert len(message["data"]) == samples
    assert message["sample_rate_hz"] == sample_rate_hz


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
                "voice_input_resource_optimization_enabled": False,
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
    assert [
        payload
        for name, payload in manager.calls
        if name == "resource_optimization_handshake"
    ] == [False, None]
    call_names = [name for name, _payload in manager.calls]
    assert call_names.index("asr_handshake") < call_names.index("start_session")
    assert (
        call_names.index("resource_optimization_handshake")
        < call_names.index("start_session")
    )


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
# The production wire order of a user-initiated stop: refreshMicLease() emits
# the owner:"none"/engaged:false snapshot first, then the notify gate sends the
# pause. Both halves leave the SAME socket back to back.
_LEASE_RELEASE_MESSAGE = {
    "action": "voice_input_control",
    "event": "lease_sync",
    "lease_generation": 2,
    "owner": "none",
    "hard_muted": False,
    "focus_suppressed": False,
    "engaged": False,
}
_PAUSE_SESSION_MESSAGE = {"action": "pause_session"}


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


_IDLE_LEASE_SYNC_MESSAGE = {
    "action": "voice_input_control",
    "event": "lease_sync",
    "lease_generation": 1,
    "owner": "none",
    "hard_muted": False,
    "focus_suppressed": False,
    "engaged": False,
}
_ENGAGED_LEASE_SYNC_MESSAGE = {
    **_LEASE_SYNC_MESSAGE,
    "lease_generation": 2,
    "engaged": True,
}


@pytest.mark.asyncio
async def test_idle_auxiliary_socket_lease_sync_does_not_reset_recording(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [_PCM_MESSAGE],
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

    # A second /chat_full window opens mid-recording: its onopen handler
    # force-sends the passive snapshot (owner none, engaged false).
    auxiliary_socket = _EventWebSocket([_IDLE_LEASE_SYNC_MESSAGE])
    await websocket_router.websocket_endpoint(auxiliary_socket, "Lan")

    # Negative validation: the idle snapshot neither claims the voice
    # connection (no identity reset) nor is applied to the lease scope.
    call_names = [name for name, _payload in manager.calls]
    assert call_names.count("begin") == 1
    assert call_names.count("control") == 1
    assert manager.statuses == []

    # The recording socket keeps streaming PCM undisturbed.
    recording_socket.release.set()
    await recording_task
    call_names = [name for name, _payload in manager.calls]
    assert call_names.count("begin") == 1
    assert call_names.count("stream_data") == 2


@pytest.mark.asyncio
async def test_engaged_reconnect_lease_sync_still_claims_immediately(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    first_socket = _EventWebSocket([_ENGAGED_LEASE_SYNC_MESSAGE, _PCM_MESSAGE])
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=first_socket,
    )

    await websocket_router.websocket_endpoint(first_socket, "Lan")
    # Mid-recording reconnect: the replacement socket's first lease_sync is
    # stamped engaged: true (the window is still recording) and must claim
    # the new connection identity immediately, exactly as before.
    reconnect_socket = _EventWebSocket([_ENGAGED_LEASE_SYNC_MESSAGE])
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
async def test_idle_socket_that_starts_recording_claims_and_takes_over(
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

    # The auxiliary window opens idle (passive snapshot: no claim), then the
    # user starts recording there: refreshMicLease sends an engaged owner
    # sync, which claims the identity and takes the recording over.
    takeover_socket = _TwoPhaseWebSocket(
        [_IDLE_LEASE_SYNC_MESSAGE, _ENGAGED_LEASE_SYNC_MESSAGE],
        [],
    )
    takeover_task = asyncio.create_task(
        websocket_router.websocket_endpoint(takeover_socket, "Lan")
    )
    await _drain_until(
        lambda: [name for name, _payload in manager.calls].count("begin") == 2
    )

    recording_socket.release.set()
    await recording_task
    takeover_socket.release.set()
    await takeover_task

    controls = [payload for name, payload in manager.calls if name == "control"]
    # The idle snapshot was dropped: only the recording socket's sync and
    # the takeover socket's engaged sync were dispatched.
    assert len(controls) == 2
    assert controls[1]["generation"] == 2


@pytest.mark.asyncio
async def test_legacy_lease_sync_without_engaged_field_keeps_claiming(
    monkeypatch,
) -> None:
    # Older frontends never send `engaged`; even an owner-none snapshot must
    # keep the historical claim-on-first-control behavior (the gate is the
    # explicit engaged: false stamp, not the snapshot content).
    legacy_idle_sync = {
        "action": "voice_input_control",
        "event": "lease_sync",
        "lease_generation": 1,
        "owner": "none",
        "hard_muted": False,
        "focus_suppressed": False,
    }
    manager = _ProtocolManager()
    websocket = _EventWebSocket([legacy_idle_sync])
    _install_protocol_endpoint(
        monkeypatch,
        manager=manager,
        websocket=websocket,
    )

    await websocket_router.websocket_endpoint(websocket, "Lan")

    assert [name for name, _payload in manager.calls] == ["begin", "control"]


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
async def test_recording_survives_second_text_socket_and_its_text_message(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    superseded_pcm = {
        "action": "stream_data",
        "input_type": "audio",
        "sample_rate_hz": 16_000,
        "data": [2, -2],
        "avatar_position": {"x": 1},
    }
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [superseded_pcm, _LEASE_SYNC_MESSAGE],
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

    # A chat window opens mid-recording, takes over the global session_id
    # AND actually uses it: text session start plus a text message.
    chat_socket = _TwoPhaseWebSocket(
        [
            {"action": "start_session", "input_type": "text"},
            {
                "action": "stream_data",
                "input_type": "text",
                "data": "hello",
            },
            {"action": "ping"},
        ],
        [],
    )
    chat_task = asyncio.create_task(
        websocket_router.websocket_endpoint(chat_socket, "Lan")
    )
    await _drain_until(
        lambda: any(
            json.loads(payload) == {"type": "pong"}
            for payload in chat_socket.sent_text
        )
    )

    # Negative validation: the superseded voice dispatch must not touch
    # non-voice manager state even when the frame carries avatar_position
    # (the normal dispatch path would overwrite this sentinel).
    sentinel = object()
    manager._avatar_position = sentinel

    recording_socket.release.set()
    await recording_task

    # The recording socket kept streaming: its post-takeover PCM and lease
    # control both dispatched, and it was never stale-closed.
    assert recording_socket.closed is False
    stream_payloads = [
        payload for name, payload in manager.calls if name == "stream_data"
    ]
    assert superseded_pcm in stream_payloads
    assert [name for name, _payload in manager.calls].count("control") == 2
    assert manager._avatar_position is sentinel
    assert {
        "code": "CHARACTER_SWITCHING_TERMINAL",
        "details": {"name": "Lan"},
    } not in manager.statuses

    # The chat window's text takeover worked unchanged: text session started
    # and its text message dispatched, without ever claiming voice.
    assert "start_session" in [name for name, _payload in manager.calls]
    assert any(
        payload.get("input_type") == "text" for payload in stream_payloads
    )
    assert [name for name, _payload in manager.calls].count("begin") == 1

    chat_socket.release.set()
    await chat_task
    assert manager.cleanup_calls == 1


@pytest.mark.asyncio
async def test_recording_survives_chat_window_open_and_close(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [_PCM_MESSAGE],
    )
    session_ids, _route_external_calls = _install_protocol_endpoint(
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

    # The chat window opens AND closes again: its disconnect must NOT run the
    # manager-wide teardown (which would end the session/ASR the recording
    # runs on). Instead the global identity is handed back to the still-open
    # voice socket and teardown is deferred to that socket's own disconnect.
    chat_socket = _EventWebSocket([{"action": "ping"}])
    await websocket_router.websocket_endpoint(chat_socket, "Lan")
    # Negative validation: no end_session/websocket-clearing cleanup ran.
    assert manager.cleanup_calls == 0
    # Manager websocket sanity: statuses/responses now reach the recorder.
    assert manager.websocket is recording_socket
    # The recorder is the current socket again, so its later disconnect
    # performs the full teardown instead of leaking the session.
    assert "Lan" in session_ids

    recording_socket.release.set()
    await recording_task

    assert recording_socket.closed is False
    assert [
        payload for name, payload in manager.calls if name == "stream_data"
    ] == [_PCM_MESSAGE, _PCM_MESSAGE]
    # The voice-owning socket's own disconnect still tears down fully.
    assert manager.cleanup_calls == 1
    assert "Lan" not in session_ids


@pytest.mark.asyncio
async def test_chat_close_hands_identity_back_and_recording_keeps_dispatching(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [_PCM_MESSAGE, _PCM_MESSAGE],
    )
    session_ids, _route_external_calls = _install_protocol_endpoint(
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

    # The chat window takes over the global session_id, actually uses it for
    # text, then disconnects while the recording is still live.
    chat_socket = _EventWebSocket(
        [
            {"action": "start_session", "input_type": "text"},
            {"action": "ping"},
        ]
    )
    await websocket_router.websocket_endpoint(chat_socket, "Lan")

    # Negative validation: the manager-wide teardown (end_session + websocket
    # clearing) must NOT have run — the recorder's session/ASR survive.
    assert manager.cleanup_calls == 0
    assert manager.websocket is recording_socket
    assert "Lan" in session_ids

    recording_socket.release.set()
    await recording_task

    # PCM kept dispatching after the chat window closed and the recorder was
    # never stale-closed.
    assert recording_socket.closed is False
    assert [
        payload for name, payload in manager.calls if name == "stream_data"
    ] == [_PCM_MESSAGE, _PCM_MESSAGE, _PCM_MESSAGE]
    # The voice-owning socket's own disconnect performs the full teardown.
    assert manager.cleanup_calls == 1
    assert "Lan" not in session_ids


@pytest.mark.asyncio
async def test_chat_close_after_recorder_left_still_tears_down(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [_PCM_MESSAGE],
    )
    session_ids, _route_external_calls = _install_protocol_endpoint(
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

    chat_socket = _TwoPhaseWebSocket([{"action": "ping"}], [])
    chat_task = asyncio.create_task(
        websocket_router.websocket_endpoint(chat_socket, "Lan")
    )
    await _drain_until(lambda: bool(chat_socket.sent_text))

    # The recorder leaves FIRST (while superseded): no teardown yet — the
    # chat window still owns the manager going forward.
    recording_socket.release.set()
    await recording_task
    assert manager.cleanup_calls == 0
    # Codex P2: teardown is correctly skipped, but the dead socket's voice
    # lease must still be revoked. Left armed it holds the realtime dispatch
    # pause its turn took (no final can arrive to release it), which parks the
    # arbiter worker before dequeuing and silently hangs every later response
    # on the session the chat window is still using.
    revoked = [name for name, _payload in manager.calls if name == "revoke"]
    assert revoked == ["revoke"]
    assert manager._voice_lease_connection_id == ""

    # The chat window's later disconnect must not defer to the departed
    # recorder: the stale voice registration is gone, so the full manager
    # teardown runs and the session cannot leak.
    chat_socket.release.set()
    await chat_task
    assert manager.cleanup_calls == 1
    assert "Lan" not in session_ids
    assert manager.websocket is chat_socket


@pytest.mark.asyncio
async def test_superseded_voice_socket_control_rejection_goes_to_own_socket(
    monkeypatch,
) -> None:
    manager = _ProtocolManager(control_result=False)
    recording_socket = _TwoPhaseWebSocket(
        [_PCM_MESSAGE],
        [_LEASE_SYNC_MESSAGE],
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

    chat_socket = _TwoPhaseWebSocket([{"action": "ping"}], [])
    chat_task = asyncio.create_task(
        websocket_router.websocket_endpoint(chat_socket, "Lan")
    )
    await _drain_until(lambda: bool(chat_socket.sent_text))

    recording_socket.release.set()
    await recording_task

    # The rejection is delivered on the superseded socket itself, not via
    # manager.send_status (whose websocket the chat window now owns).
    rejected = {
        "code": "VOICE_INPUT_CONTROL_REJECTED",
        "details": {"reason": "invalid_or_stale_control"},
    }
    assert rejected not in manager.statuses
    own_socket_statuses = [
        json.loads(json.loads(payload)["message"])
        for payload in recording_socket.sent_text
        if json.loads(payload).get("type") == "status"
    ]
    assert rejected in own_socket_statuses

    chat_socket.release.set()
    await chat_task


@pytest.mark.asyncio
async def test_superseded_voice_socket_non_voice_message_is_still_closed(
    monkeypatch,
) -> None:
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [{"action": "start_session", "input_type": "audio"}],
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

    chat_socket = _TwoPhaseWebSocket([{"action": "ping"}], [])
    chat_task = asyncio.create_task(
        websocket_router.websocket_endpoint(chat_socket, "Lan")
    )
    await _drain_until(lambda: bool(chat_socket.sent_text))

    recording_socket.release.set()
    await recording_task

    # Voice ownership only shields voice-path messages: an audio-mode
    # start_session stays on newest-socket-wins and stale-closes the socket
    # before any authorization or session start.
    assert recording_socket.closed is True
    assert "authorize" not in [name for name, _payload in manager.calls]
    assert "start_session" not in [name for name, _payload in manager.calls]
    assert {
        "code": "CHARACTER_SWITCHING_TERMINAL",
        "details": {"name": "Lan"},
    } in manager.statuses

    chat_socket.release.set()
    await chat_task


@pytest.mark.asyncio
async def test_superseded_recorder_pause_ends_the_session_without_a_stale_close(
    monkeypatch,
) -> None:
    # Codex P2. stopRecording() emits the lease release and THEN pause_session
    # from the same socket. Only the first half was voice-path, so the pause
    # fell through to the global-identity check and stale-closed the recorder:
    # the user stopped the microphone and got a character-switch teardown plus
    # a 3s reconnect that re-steals the identity, while the provider session
    # stayed alive because end_session never ran.
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        # Both halves of the real stopRecording(), in wire order: the lease
        # release does NOT clear _voice_lease_connection_id (only a disconnect
        # or a blocked-route revoke does), so the socket still owns voice when
        # the pause lands right behind it.
        [_LEASE_RELEASE_MESSAGE, _PAUSE_SESSION_MESSAGE],
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

    chat_socket = _TwoPhaseWebSocket([{"action": "ping"}], [])
    chat_task = asyncio.create_task(
        websocket_router.websocket_endpoint(chat_socket, "Lan")
    )
    await _drain_until(lambda: bool(chat_socket.sent_text))

    recording_socket.release.set()
    await recording_task
    await _drain_until(
        lambda: "end_session" in [name for name, _payload in manager.calls]
    )

    # The stop reaches the session it owns, and the socket is never told it
    # lost a character switch it was not part of.
    assert manager.active_session_is_idle is True
    assert recording_socket.closed is False
    assert {
        "code": "CHARACTER_SWITCHING_TERMINAL",
        "details": {"name": "Lan"},
    } not in manager.statuses

    call_names = [name for name, _payload in manager.calls]
    # The lease release still applies -- that is how the backend learns the
    # audio route is free -- and the pause must reach its own branch, never the
    # stream_data tail of _dispatch_voice_message_while_superseded. Reordering
    # that branch below the game-route check would feed a pause_session to
    # stream_data() silently; only the PCM frame may reach it.
    assert call_names.count("control") == 2
    assert ("stream_data", _PAUSE_SESSION_MESSAGE) not in manager.calls
    assert call_names.count("stream_data") == 1

    chat_socket.release.set()
    await chat_task


@pytest.mark.asyncio
async def test_superseded_recorder_pause_does_not_end_a_newer_text_session(
    monkeypatch,
) -> None:
    # Codex P2. Holding the lease does not prove the live session is still ours.
    # A newer socket's text start installs self.session well BEFORE
    # _start_independent_asr_if_enabled revokes this lease, so a pause arriving
    # inside that window still satisfies _owns_voice_connection() and used to
    # fire an UNGATED end_session() against the text session just installed --
    # the exact CHARACTER_LEFT teardown 7b56afa9 removed from the frontend.
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [_LEASE_RELEASE_MESSAGE, _PAUSE_SESSION_MESSAGE],
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

    chat_socket = _TwoPhaseWebSocket([{"action": "ping"}], [])
    chat_task = asyncio.create_task(
        websocket_router.websocket_endpoint(chat_socket, "Lan")
    )
    await _drain_until(lambda: bool(chat_socket.sent_text))

    # The newer window's text start is in flight: input_mode has flipped and its
    # session is installed, but the lease revoke has not landed yet.
    manager.input_mode = "text"
    manager.session = object()

    recording_socket.release.set()
    await recording_task

    call_names = [name for name, _payload in manager.calls]
    assert "end_session" not in call_names
    # Still not a character switch -- the recorder keeps its socket either way.
    assert recording_socket.closed is False
    assert {
        "code": "CHARACTER_SWITCHING_TERMINAL",
        "details": {"name": "Lan"},
    } not in manager.statuses

    chat_socket.release.set()
    await chat_task


@pytest.mark.asyncio
async def test_pause_from_a_socket_that_lost_voice_is_still_a_character_switch(
    monkeypatch,
) -> None:
    # The pause exemption is scoped to "this socket still holds voice". Once a
    # NEWER socket has claimed the identity, newest-wins applies again and the
    # old socket's pause is an ordinary stale action -- otherwise classifying
    # pause_session as voice-path would quietly keep every superseded socket
    # alive, and let it end a session that is no longer its own.
    manager = _ProtocolManager()
    recording_socket = _TwoPhaseWebSocket(
        [_LEASE_SYNC_MESSAGE, _PCM_MESSAGE],
        [_PAUSE_SESSION_MESSAGE],
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

    # A newer socket ENGAGES voice, moving _voice_lease_connection_id.
    takeover_socket = _TwoPhaseWebSocket([_LEASE_SYNC_MESSAGE], [])
    takeover_task = asyncio.create_task(
        websocket_router.websocket_endpoint(takeover_socket, "Lan")
    )
    await _drain_until(
        lambda: [name for name, _payload in manager.calls].count("begin") == 2
    )

    recording_socket.release.set()
    await recording_task

    assert recording_socket.closed is True
    assert "end_session" not in [name for name, _payload in manager.calls]
    assert {
        "code": "CHARACTER_SWITCHING_TERMINAL",
        "details": {"name": "Lan"},
    } in manager.statuses

    takeover_socket.release.set()
    await takeover_task


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
