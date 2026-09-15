import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
from main_logic.asr_client.lifecycle import VoiceLifecycleState
import main_logic.core as core_module

from tests.support.core_asr_harness import (
    _install_ready_lifecycle,
    _start_and_seal_turn,
)

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_external_voice_suppression_reasons_are_independent() -> None:
    runtime = _Runtime()
    runtime._invalidate_voice_pcm_sync = MagicMock()
    runtime._abort_independent_asr = AsyncMock()

    await runtime.set_voice_input_suppressed("enrollment", suppressed=True)
    await runtime.set_voice_input_suppressed("maintenance", suppressed=True)
    await runtime.set_voice_input_suppressed("enrollment", suppressed=False)

    assert runtime._voice_input_accepts_pcm() is False

    await runtime.set_voice_input_suppressed("maintenance", suppressed=False)

    assert runtime._voice_input_accepts_pcm() is True
    assert runtime._abort_independent_asr.await_count == 2


async def test_game_final_cannot_cross_lease_back_to_core(monkeypatch) -> None:
    runtime = _Runtime()
    route_transcript = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.get_active_game_route_identity",
        lambda _name: ("game", "session-a"),
    )
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.route_external_voice_transcript",
        route_transcript,
    )
    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=False,
        focus_suppressed=False,
    )
    _install_ready_lifecycle(runtime, "qwen")
    epoch = runtime._asr_session_epoch
    await _start_and_seal_turn(runtime, "qwen")

    await runtime._handle_voice_input_control(
        "lease_sync",
        2,
        owner="core",
        hard_muted=False,
        focus_suppressed=False,
    )
    await runtime._handle_independent_asr_final("stale", epoch, "qwen")
    await runtime._wait_asr_transcript_dispatch_idle()

    route_transcript.assert_not_awaited()
    runtime.handle_input_transcript.assert_not_awaited()
    runtime.session.create_response.assert_not_awaited()
    assert runtime._omni_mic_audio_bytes == 0


async def test_hard_mute_overrides_game_consumer(monkeypatch) -> None:
    runtime = _Runtime()
    monkeypatch.setattr(
        "main_logic.voice_input.consumers.game.is_game_route_active",
        lambda _name: True,
    )

    await runtime._handle_voice_input_control(
        "lease_sync",
        1,
        owner="game",
        hard_muted=True,
        focus_suppressed=False,
    )

    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._voice_input_suppression_reasons == {"hard_mute"}
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize(
    ("previous_owner", "owner", "reason", "barrier_method"),
    [
        ("core", "game", "game_takeover", "suspend"),
        ("game", "core", "game_release", "abort"),
        ("core", "none", "connection_closed", "abort"),
    ],
)
async def test_voice_lease_advances_runtime_barrier_before_waiting_for_registry(
    previous_owner: str,
    owner: str,
    reason: str,
    barrier_method: str,
) -> None:
    runtime = _Runtime()
    runtime._voice_lease_owner = previous_owner
    order: list[str] = []
    runtime._invalidate_voice_pcm_sync = MagicMock(
        side_effect=lambda _reason: order.append("invalidate")
    )
    runtime._asr_runtime.suspend = AsyncMock(
        side_effect=lambda _reason: order.append("suspend")
    )
    runtime._asr_runtime.abort = AsyncMock(
        side_effect=lambda _reason: order.append("abort")
    )
    runtime._asr_runtime.resume = AsyncMock()
    runtime._voice_input_registry.wait_idle = AsyncMock(
        side_effect=lambda: order.append("wait_idle")
    )

    await runtime._apply_voice_lease_state(
        owner=owner,
        hard_muted=False,
        focus_suppressed=False,
        reason=reason,
        force_abort=True,
    )

    assert order == ["invalidate", barrier_method, "wait_idle"]


async def test_new_websocket_connection_resets_mic_lease_generation_once() -> None:
    runtime = _Runtime()
    runtime._voice_lease_generation = 12

    assert runtime._begin_voice_input_connection("socket-a") is True
    assert runtime._voice_lease_generation == -1
    assert runtime._voice_lease_control_seen is False
    assert runtime._voice_input_accepts_pcm() is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_lease_control_seen is True
    assert runtime._voice_input_accepts_pcm() is False

    assert runtime._begin_voice_input_connection("socket-a") is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )

    assert runtime._begin_voice_input_connection("socket-b") is True
    assert runtime._voice_lease_control_seen is False
    assert runtime._voice_input_accepts_pcm() is False
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    assert runtime._voice_input_accepts_pcm() is True


async def test_legacy_audio_session_authorization_is_one_shot() -> None:
    runtime = _Runtime()
    runtime._asr_runtime.abort = AsyncMock()

    assert runtime._begin_voice_input_connection("legacy-socket") is True
    assert await runtime._ensure_voice_input_session_authorized("legacy-socket") is True
    assert runtime._voice_lease_generation == 0
    assert runtime._voice_lease_synchronized is True
    assert runtime._voice_lease_owner == "core"
    assert runtime._voice_lease_hard_muted is False
    assert runtime._voice_lease_focus_suppressed is False
    assert runtime._voice_input_accepts_pcm() is True
    runtime._asr_runtime.abort.assert_awaited_once_with("legacy_session_start")

    runtime._asr_runtime.abort.reset_mock()
    assert await runtime._ensure_voice_input_session_authorized("legacy-socket") is True
    assert runtime._voice_lease_generation == 0
    runtime._asr_runtime.abort.assert_not_awaited()


async def test_explicit_owner_none_cannot_be_overridden_by_legacy_authorization() -> (
    None
):
    runtime = _Runtime()
    runtime._asr_runtime.abort = AsyncMock()

    assert runtime._begin_voice_input_connection("explicit-socket") is True
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    runtime._asr_runtime.abort.reset_mock()

    assert (
        await runtime._ensure_voice_input_session_authorized("explicit-socket") is True
    )
    assert runtime._voice_lease_generation == 1
    assert runtime._voice_lease_owner == "none"
    assert runtime._voice_input_accepts_pcm() is False
    runtime._asr_runtime.abort.assert_not_awaited()


@pytest.mark.parametrize(
    ("event", "generation"),
    [
        ("invalid-control", 0),
        ("lease_sync", -1),
    ],
)
async def test_rejected_explicit_control_permanently_disables_legacy_fallback(
    event: str,
    generation: int,
) -> None:
    runtime = _Runtime()
    runtime._asr_runtime.abort = AsyncMock()

    assert runtime._begin_voice_input_connection("explicit-socket") is True
    assert (
        await runtime._handle_voice_input_control(
            event,
            generation,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
        is False
    )
    assert runtime._voice_lease_control_seen is True
    assert (
        await runtime._ensure_voice_input_session_authorized("explicit-socket") is False
    )
    await runtime._enqueue_audio_stream_data(
        {
            "input_type": "audio",
            "sample_rate_hz": 16_000,
            "data": [1] * 160,
        }
    )

    assert runtime._voice_lease_synchronized is False
    assert runtime._voice_lease_owner == "none"
    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._audio_stream_queue.empty()
    runtime._asr_runtime.abort.assert_not_awaited()


async def test_legacy_authorization_loses_race_to_new_connection_identity() -> None:
    runtime = _Runtime()
    abort_started = asyncio.Event()
    release_abort = asyncio.Event()

    async def _block_old_abort(_reason: str) -> None:
        abort_started.set()
        await release_abort.wait()

    old_abort = AsyncMock(side_effect=_block_old_abort)
    runtime._asr_runtime.abort = old_abort
    assert runtime._begin_voice_input_connection("socket-a") is True

    authorize_task = asyncio.create_task(
        runtime._ensure_voice_input_session_authorized("socket-a")
    )
    await asyncio.wait_for(abort_started.wait(), 1)

    assert runtime._begin_voice_input_connection("socket-b") is True
    runtime._asr_runtime.abort = AsyncMock()
    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="none",
            hard_muted=False,
            focus_suppressed=False,
        )
        is True
    )
    release_abort.set()

    assert await authorize_task is False
    assert runtime._voice_lease_connection_id == "socket-b"
    assert runtime._voice_lease_generation == 1
    assert runtime._voice_lease_control_seen is True
    assert runtime._voice_lease_owner == "none"
    assert runtime._voice_input_accepts_pcm() is False
    old_abort.assert_awaited_once_with("legacy_session_start")


async def test_game_owner_and_hard_mute_remain_simultaneously_authoritative() -> None:
    runtime = _Runtime()
    _install_ready_lifecycle(runtime, "qwen")

    assert (
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="game",
            hard_muted=True,
            focus_suppressed=False,
        )
        is True
    )

    assert runtime._voice_lease_owner == "game"
    assert runtime._voice_lease_hard_muted is True
    assert runtime._voice_input_suppression_reasons == {"game", "hard_mute"}
    assert runtime._voice_input_accepts_pcm() is False
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.SUSPENDED


@pytest.mark.parametrize(
    "transition",
    [
        "hard_mute",
        "focus_suppress",
        "game_takeover",
        "lease_sync",
        "connection_replacement",
    ],
)
async def test_core_start_is_invalidated_by_mic_lease_transition(
    monkeypatch,
    transition: str,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    release_connect = asyncio.Event()

    class Candidate:
        def __init__(self) -> None:
            self.connect_started = asyncio.Event()
            self.is_ready = True
            self.close = AsyncMock()

        async def connect(self) -> None:
            self.connect_started.set()
            await release_connect.wait()

    candidate = Candidate()
    selection = SimpleNamespace(
        provider_key="qwen",
        endpointing_mode="provider",
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        MagicMock(return_value=candidate),
    )
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    runtime._voice_lease_connection_id = "connection"
    runtime._voice_lease_generation = 0

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(candidate.connect_started.wait(), 1)
    if transition == "connection_replacement":
        runtime._begin_voice_input_connection("replacement")
    elif transition == "lease_sync":
        await runtime._handle_voice_input_control(
            "lease_sync",
            1,
            owner="core",
            hard_muted=False,
            focus_suppressed=False,
        )
    else:
        await runtime._handle_voice_input_control(transition, 1)
    release_connect.set()
    await asyncio.wait_for(starting, 1)

    assert runtime._asr_route_mode == "blocked"
    assert runtime._independent_asr_provider is None
    assert "ASR_INDEPENDENT_READY" not in str(runtime.send_status.await_args_list)
    candidate.close.assert_awaited_once_with()


async def test_core_start_survives_benign_lease_transition(monkeypatch) -> None:
    """Owner flip / mute toggle / lease bump during the settings await are
    PCM-gating changes, not route operations; they must not abort the start
    (there is no retry or failure status on that path)."""

    settings_started = asyncio.Event()
    release_settings = asyncio.Event()

    async def load_settings(**_kwargs):
        settings_started.set()
        await release_settings.wait()
        return {"independentAsrEnabled": True}

    runtime = _Runtime()
    runtime._voice_lease_owner = "none"
    runtime._voice_lease_synchronized = True
    runtime.core_api_type = "qwen"

    async def ready_start(**_kwargs) -> AsrStartResult:
        return AsrStartResult(
            AsrStartStatus.READY,
            provider="qwen",
            session_epoch=runtime._asr_session_epoch,
        )

    runtime._asr_runtime.start = AsyncMock(side_effect=ready_start)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        load_settings,
    )

    starting = asyncio.create_task(runtime._start_independent_asr_if_enabled("audio"))
    await asyncio.wait_for(settings_started.wait(), 1)
    runtime._voice_lease_owner = "core"
    runtime._voice_lease_hard_muted = True
    runtime._voice_lease_generation += 1
    release_settings.set()
    await asyncio.wait_for(starting, 1)

    runtime._asr_runtime.start.assert_awaited_once()
    assert runtime._asr_route_mode == "independent"
    assert runtime._independent_asr_provider == "qwen"
