import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
from main_logic.asr_client.lifecycle import VoiceLifecycleEvent, VoiceLifecycleState
from main_logic.voice_turn.contracts import AsrFailureEvent, VoiceIngressToken
import main_logic.core as core_module

from tests.support.asr_fakes import (
    _Runtime,
    _selection,
)

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
    _install_replacement_runtime_generation,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_a_live_route_releases_the_pipeline_failure_ingress_latch() -> None:
    """The latch is fail-closed, not permanent: a live route clears it."""

    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    runtime._asr_runtime.abort = AsyncMock()
    runtime._voice_input_audio_pipeline.process = AsyncMock(
        side_effect=RuntimeError("soxr failed")
    )
    token = runtime._capture_ingress_token()
    await runtime._process_microphone_stream_data(
        {"input_type": "audio", "sample_rate_hz": 48_000, "data": [1] * 480},
        ingress_token=token,
    )
    assert runtime._asr_route_mode == "blocked"
    assert runtime._voice_input_pipeline_failure_token is not None

    runtime._set_microphone_route("independent")
    assert runtime._voice_input_pipeline_failure_token is None


async def test_stale_failure_abort_does_not_clear_successor_route_audio() -> None:
    """A restart landing inside the abort keeps its own queued audio.

    `_abort_independent_asr` invalidates the voice PCM sync AFTER awaiting the
    runtime abort. That await is no longer covered by the pipeline transition
    lock, so a session restart can install a newer route inside it -- and the
    old failure would then clear the successor's queued and hot-swap audio and
    drop its microphone input.
    """

    runtime = _Runtime()
    runtime.is_active = True
    runtime._set_microphone_route("independent")
    runtime._independent_asr_provider = "glm"
    assert runtime._begin_voice_input_connection("socket-a") is True
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_synchronized = True
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def blocking_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    runtime._asr_runtime.abort = AsyncMock(side_effect=blocking_abort)
    invalidated: list[str] = []
    runtime._invalidate_voice_pcm_sync = lambda reason: invalidated.append(reason)

    failure = asyncio.create_task(
        runtime._fail_voice_input_pipeline(
            ingress_token=runtime._capture_ingress_token(),
            session_ref=runtime.session,
            audio_epoch=runtime._audio_stream_epoch,
            pipeline_ref=runtime._voice_input_audio_pipeline,
        )
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    # A newer route operation claims the route while the abort is in flight.
    object.__setattr__(
        runtime,
        "_asr_route_operation_generation",
        runtime._asr_route_operation_generation + 1,
    )
    release_abort.set()
    await asyncio.wait_for(failure, 1)

    assert invalidated == [], (
        "the successor route owns the voice PCM sync now; this failure must "
        "not clear it"
    )
    runtime.send_status.assert_not_awaited()


async def test_stale_runtime_ready_result_cannot_replace_new_route(
    monkeypatch,
) -> None:
    start_entered = asyncio.Event()
    release_start = asyncio.Event()

    class BlockingBridge:
        def __init__(self) -> None:
            self.session_epoch = 0
            self.audio_generation = 0

        def capture_ingress_token(
            self,
            *,
            connection_id,
            lease_generation,
            route_generation,
        ):
            from main_logic.voice_turn.contracts import VoiceIngressToken

            return VoiceIngressToken(
                self.session_epoch,
                connection_id,
                lease_generation,
                route_generation,
                self.audio_generation,
            )

        async def close(self) -> None:
            self.session_epoch += 1
            self.audio_generation += 1

        async def start(self, **_kwargs) -> AsrStartResult:
            source_epoch = self.session_epoch
            start_entered.set()
            await release_start.wait()
            return AsrStartResult(
                AsrStartStatus.READY,
                provider="old-provider",
                session_epoch=source_epoch,
            )

    runtime = _Runtime()
    bridge = BlockingBridge()
    object.__setattr__(runtime, "_asr_runtime", bridge)
    runtime.core_api_type = "old-core"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(start_entered.wait(), 1)
    runtime._begin_asr_route_operation()
    bridge.session_epoch += 1
    runtime.core_api_type = "new-core"
    runtime._independent_asr_provider = "new-provider"
    runtime._independent_asr_route_key = "new-core"
    runtime._set_microphone_route("independent")
    release_start.set()
    await asyncio.wait_for(starting, 1)

    assert runtime._independent_asr_provider == "new-provider"
    assert runtime._independent_asr_route_key == "new-core"
    assert runtime._asr_route_mode == "independent"


async def test_runtime_failure_from_a_live_route_still_revokes_the_lease() -> None:
    # Codex P2. The chokepoint refactor passed the PRE-transition identity tuple
    # as still_current, but that tuple carries _asr_route_mode (and
    # _microphone_route_generation, inside the ingress token) while the handler
    # sets the route to "blocked" two lines earlier. The predicate was therefore
    # false on ENTRY -- against the handler's own step -- so
    # _fail_closed_voice_route returned before revoking, leaving the recording
    # socket holding a live hardware microphone on a dead route. Only reachable
    # from a LIVE route, which is exactly the real runtime-failure case; an
    # already-blocked route happened to compare equal and masked it.
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._voice_lease_connection_id = "socket-a"
    runtime._voice_input_websocket = object()

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    assert runtime._asr_route_mode == "blocked"
    assert runtime._voice_lease_connection_id == ""


async def test_old_abort_release_cannot_close_replacement_session() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    old_lifecycle = runtime._asr_lifecycle
    old_detector = runtime._asr_detector
    release_started = asyncio.Event()
    release_old_lease = asyncio.Event()

    class BlockingLease:
        async def release(self) -> None:
            release_started.set()
            await release_old_lease.wait()

    runtime._asr_smart_turn_lease = BlockingLease()
    abort_task = asyncio.create_task(runtime._asr_runtime.abort("test_abort"))
    await asyncio.wait_for(release_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release_old_lease.set()
    await asyncio.wait_for(abort_task, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_lifecycle is not old_lifecycle
    assert runtime._asr_detector is not old_detector
    old_session.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()


async def test_old_failure_callback_cannot_detach_replacement_runtime() -> None:
    runtime = _Runtime()
    old_session = SimpleNamespace(is_ready=True, close=AsyncMock())
    runtime._asr_session = old_session
    _install_ready_lifecycle(runtime, "qwen")
    old_lifecycle = runtime._asr_lifecycle
    old_detector = runtime._asr_detector
    blocked_started = asyncio.Event()
    release_blocked = asyncio.Event()

    async def block_old_lifecycle(payload: str) -> None:
        status = json.loads(payload)
        if (
            status.get("code") == "ASR_LIFECYCLE_STATE"
            and status.get("details", {}).get("state") == "blocked"
        ):
            blocked_started.set()
            await release_blocked.wait()

    runtime.send_status.side_effect = block_old_lifecycle
    old_epoch = runtime._asr_session_epoch
    failure_task = asyncio.create_task(
        runtime._handle_independent_asr_error(old_epoch, "qwen")
    )
    await asyncio.wait_for(blocked_started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    release_blocked.set()
    await asyncio.wait_for(failure_task, 1)
    await asyncio.sleep(0)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert old_lifecycle.snapshot.state is VoiceLifecycleState.OFF
    old_detector.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    new_detector.close.assert_not_awaited()
    assert runtime._asr_route_mode == "independent"


async def test_stale_connect_failure_cannot_fail_new_generation() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")
    lifecycle = runtime._asr_lifecycle
    assert lifecycle is not None
    lifecycle.transition(VoiceLifecycleEvent.SOFT_WAKE)
    runtime._asr_session = None
    started = asyncio.Event()
    release = asyncio.Event()
    candidate = SimpleNamespace(close=AsyncMock())

    async def connect() -> None:
        started.set()
        await release.wait()
        raise RuntimeError("old candidate failed")

    candidate.connect = AsyncMock(side_effect=connect)
    runtime._asr_session_factory = MagicMock(return_value=candidate)
    runtime._asr_transport_selection = _selection("qwen")
    old_restart = runtime._asr_runtime._ensure_transport_restart_task()
    await asyncio.wait_for(started.wait(), 1)

    new_session, new_lifecycle, new_detector = _install_replacement_runtime_generation(
        runtime, "qwen"
    )
    new_factory = object()
    new_selection = object()
    keep_transport = asyncio.Event()
    new_transport = asyncio.create_task(keep_transport.wait())
    runtime._asr_session_factory = new_factory
    runtime._asr_transport_selection = new_selection
    runtime._asr_transport_task = new_transport
    release.set()
    await asyncio.wait_for(old_restart, 1)

    assert runtime._asr_session is new_session
    assert runtime._asr_lifecycle is new_lifecycle
    assert runtime._asr_detector is new_detector
    assert runtime._asr_session_factory is new_factory
    assert runtime._asr_transport_selection is new_selection
    assert runtime._asr_transport_task is new_transport
    candidate.close.assert_awaited_once_with()
    new_session.close.assert_not_awaited()
    runtime.send_status.assert_not_awaited()
    keep_transport.set()
    await new_transport


async def test_old_native_send_failure_cannot_close_new_session() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    send_entered = asyncio.Event()
    release_send = asyncio.Event()

    async def fail_old_send(_pcm16) -> None:
        send_entered.set()
        await release_send.wait()
        raise RuntimeError("connection closed")

    old_session = type("OldOmni", (), {})()
    old_session.stream_audio = AsyncMock(side_effect=fail_old_send)
    runtime.session = old_session
    old_token = runtime._capture_native_ingress_token()
    old_send = asyncio.create_task(
        runtime._route_microphone_audio(
            b"\x01\x00",
            sample_rate_hz=16_000,
            ingress_token=old_token,
        )
    )
    await asyncio.wait_for(send_entered.wait(), 1)

    new_session = type("NewOmni", (), {})()
    new_session.stream_audio = AsyncMock()
    runtime.session = new_session
    release_send.set()
    await asyncio.wait_for(old_send, 1)

    assert runtime.session_closed_by_server is False
    assert runtime._omni_mic_audio_bytes == 0
    await runtime._route_microphone_audio(
        b"\x02\x00",
        sample_rate_hz=16_000,
        ingress_token=runtime._capture_native_ingress_token(),
    )
    new_session.stream_audio.assert_awaited_once_with(b"\x02\x00")
    assert runtime._omni_mic_audio_bytes == 2


async def test_failure_event_only_blocks_current_generation() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    current_epoch = runtime._asr_session_epoch

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="old-provider",
            session_epoch=current_epoch - 1,
        )
    )
    assert runtime._asr_route_mode == "independent"

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=current_epoch,
        )
    )
    assert runtime._asr_route_mode == "blocked"


async def test_runtime_failure_still_fences_a_competing_newer_operation() -> None:
    # Re-basing the identity must not weaken the fence it exists for: a NEWER
    # route operation landing during this handler's own transition still has to
    # stop the revoke, because _revoke_voice_input_connection calls
    # _invalidate_asr_start() and would cancel that newer start.
    runtime = _Runtime()
    runtime._set_microphone_route("independent")
    runtime._voice_lease_connection_id = "socket-a"
    original_set_route = runtime._set_microphone_route

    def _set_route_then_supersede(mode: str) -> None:
        original_set_route(mode)
        runtime._begin_asr_route_operation()

    runtime._set_microphone_route = _set_route_then_supersede

    await runtime._handle_core_asr_failure(
        AsrFailureEvent(
            code="ASR_INDEPENDENT_FAILED",
            provider="current-provider",
            session_epoch=runtime._asr_session_epoch,
        )
    )

    assert runtime._voice_lease_connection_id == "socket-a"


async def test_stale_start_abort_does_not_clobber_newer_start_placeholder(
    monkeypatch,
) -> None:
    """A stale start parked in its abort must not clear the blocked
    placeholder a newer start installed meanwhile: clearing it would make
    the newer start's fence fail before it even reaches the native
    fallback, leaving the route blocked with no failure status."""

    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime._voice_lease_connection_id = "conn-A"
    runtime._voice_lease_generation = 0

    a_start_parked = asyncio.Event()
    release_a_start = asyncio.Event()
    a_abort_parked = asyncio.Event()
    release_a_abort = asyncio.Event()
    b_settings_parked = asyncio.Event()
    release_b_settings = asyncio.Event()
    start_calls: list[dict] = []

    async def fake_runtime_start(**kwargs):
        start_calls.append(kwargs)
        if len(start_calls) == 1:
            a_start_parked.set()
            await release_a_start.wait()
            return AsrStartResult(
                AsrStartStatus.FAILED,
                failure_code="ASR_START_STALE",
                session_epoch=runtime._asr_session_epoch,
            )
        return AsrStartResult(
            AsrStartStatus.READY,
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )

    async def fake_abort(reason):
        a_abort_parked.set()
        await release_a_abort.wait()

    runtime._asr_runtime.start = fake_runtime_start
    runtime._asr_runtime.abort = fake_abort
    runtime._asr_runtime.close = AsyncMock()

    settings_calls = 0

    async def load_settings(**_kwargs):
        nonlocal settings_calls
        settings_calls += 1
        if settings_calls == 2:
            b_settings_parked.set()
            await release_b_settings.wait()
        return {"independentAsrEnabled": True}

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        load_settings,
    )

    task_a = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(a_start_parked.wait(), 1)
    runtime._begin_voice_input_connection("conn-B")
    release_a_start.set()
    await asyncio.wait_for(a_abort_parked.wait(), 1)

    task_b = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(b_settings_parked.wait(), 1)
    assert runtime._independent_asr_route_key == "qwen"

    release_a_abort.set()
    await asyncio.wait_for(task_a, 1)
    assert runtime._independent_asr_route_key == "qwen"

    release_b_settings.set()
    await asyncio.wait_for(task_b, 1)

    assert len(start_calls) == 2
    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "qwen"
