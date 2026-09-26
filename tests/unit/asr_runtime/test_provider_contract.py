import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
from main_logic.asr_client.lifecycle import VoiceLifecycleState
from main_logic.voice_turn.contracts import SpeechActivityEvent
import main_logic.core as core_module

from tests.support.core_asr_harness import (
    _ReadyDetector,
    _install_ready_lifecycle,
)

from tests.support.asr_fakes import (
    _Runtime,
    _selection,
)

from tests.unit.asr_runtime._scenarios import (
    _start_runtime_with_callback_candidates,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def _start_bridge_and_capture_builder_call(monkeypatch, runtime):
    import main_logic.asr_client.runtime as runtime_module

    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    builder = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        builder,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "independent"
    return builder.call_args.kwargs


def _install_failing_restart_candidates(
    runtime: _Runtime,
    provider: str,
    *,
    failure_count: int,
) -> list[SimpleNamespace]:
    runtime._asr_session = SimpleNamespace(is_ready=False, close=AsyncMock())
    _install_ready_lifecycle(runtime, provider)
    candidates: list[SimpleNamespace] = []

    def build_candidate(_selection):
        candidate = SimpleNamespace(
            is_ready=True,
            connect=AsyncMock(
                side_effect=RuntimeError("private restart connect detail")
            ),
            close=AsyncMock(),
        )
        candidates.append(candidate)
        assert len(candidates) <= failure_count
        return candidate

    runtime._asr_session_factory = MagicMock(side_effect=build_candidate)
    runtime._asr_transport_selection = _selection(provider)
    return candidates


async def test_runtime_builds_primary_candidate_from_its_single_selection(
    monkeypatch,
) -> None:
    import main_logic.asr_client as asr_client
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    selection = asr_client._AsrSelection(
        provider_key="gemini",
        endpointing_mode="manual",
    )
    resolver = MagicMock(return_value=selection)
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    builder = MagicMock(return_value=asr)

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", resolver)
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        builder,
        raising=False,
    )
    assert not hasattr(runtime_module, "create_asr_session")

    await runtime._start_independent_asr_if_enabled("audio")

    resolver.assert_called_once_with("gemini")
    assert builder.call_args.kwargs["selection"] is selection
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "gemini"
    assert runtime._asr_route_mode == "independent"


async def test_start_forwards_core_user_language_to_session_builder(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.user_language = "ja"

    kwargs = await _start_bridge_and_capture_builder_call(monkeypatch, runtime)

    assert kwargs["user_language"] == "ja"


async def test_start_without_user_language_builds_session_without_hint(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    assert getattr(runtime, "user_language", None) is None

    kwargs = await _start_bridge_and_capture_builder_call(monkeypatch, runtime)

    assert kwargs["user_language"] is None


async def test_explicit_intl_soniox_is_selected_before_audio(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    asr = type("Asr", (), {})()
    asr.connect = AsyncMock()
    asr.close = AsyncMock()
    factory = MagicMock(return_value=asr)
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("soniox", "provider")),
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "soniox"
    assert runtime._asr_received_audio is False


async def test_soniox_connect_retries_exhausted_blocks_without_provider_fallback(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.session.stream_audio = AsyncMock()
    soniox_selection = _selection("soniox", "provider")
    forbidden_core_resolver = MagicMock(
        side_effect=AssertionError("Soniox recovery must not resolve another provider")
    )
    sleep = AsyncMock()
    sessions = []
    for attempt in range(3):
        session = type("Soniox", (), {})()
        session.connect = AsyncMock(
            side_effect=RuntimeError(f"private provider detail {attempt}")
        )
        session.close = AsyncMock()
        sessions.append(session)
    built_selections = []

    def create_candidate(_core_type, *, selection, **_kwargs):
        built_selections.append(selection)
        assert selection is soniox_selection
        return sessions[len(built_selections) - 1]

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=soniox_selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_core_follow_selection",
        forbidden_core_resolver,
        raising=False,
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        create_candidate,
    )
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)

    await runtime._start_independent_asr_if_enabled("audio")

    consumed = await runtime._route_microphone_audio(
        b"\x00\x00",
        sample_rate_hz=16_000,
    )
    for session in sessions:
        session.connect.assert_awaited_once_with()
        session.close.assert_awaited_once_with()
    forbidden_core_resolver.assert_not_called()
    assert built_selections == [soniox_selection] * 3
    assert [call.args for call in sleep.await_args_list] == [(0.25,), (0.5,)]
    assert runtime._asr_session is None
    assert runtime._asr_provider is None
    assert runtime._asr_route_mode == "blocked"
    assert consumed is True
    runtime.session.stream_audio.assert_not_awaited()
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert {
        "code": "ASR_INDEPENDENT_PROVIDER_UNAVAILABLE",
        "details": {
            "provider": "soniox",
            "session_epoch": runtime._asr_session_epoch,
        },
    } in statuses
    assert statuses[-1] == {
        "code": "VOICE_INPUT_RECOVERY_FAILED",
        "details": {
            "reason": "ASR_INDEPENDENT_FAILED",
            "session_epoch": runtime._asr_session_epoch,
            "lease_generation": runtime._voice_lease_generation,
        },
    }
    assert "private provider detail" not in str(runtime.send_status.await_args_list)


async def test_adopted_start_activity_callback_survives_idle_audio_generation_bump(
    monkeypatch,
) -> None:
    runtime, sessions, callbacks, detector = (
        await _start_runtime_with_callback_candidates(
            monkeypatch,
            candidate_count=1,
        )
    )
    component = runtime._asr_runtime
    original_audio_generation = component._asr_audio_generation
    current_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = current_ingress

    await component._handle_audio_ingress_backpressure(current_ingress)

    assert component._asr_audio_generation == original_audio_generation + 1
    assert component._asr_session is sessions[0]
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    detector.reset.assert_awaited_once_with()

    updated_ingress = runtime._capture_ingress_token()
    component._asr_current_ingress_token = updated_ingress
    on_activity = callbacks[0]["on_speech_activity"]
    assert callable(on_activity)
    await on_activity(SpeechActivityEvent.SPEECH_STARTED)

    assert component._asr_current_ingress_token == updated_ingress
    assert component._asr_lifecycle.snapshot.state is VoiceLifecycleState.ACTIVE
    sessions[0].close.assert_not_awaited()


async def test_restart_default_attempts_follow_single_attempt_policy(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    sleep = AsyncMock()
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    candidates = _install_failing_restart_candidates(runtime, "qwen", failure_count=1)
    assert runtime._asr_lifecycle.provider_policy.connect_max_attempts == 1

    await runtime._restart_transport()
    while runtime._asr_runtime._asr_close_tasks:
        await asyncio.gather(
            *tuple(runtime._asr_runtime._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(candidates) == 1
    candidates[0].connect.assert_awaited_once_with()
    candidates[0].close.assert_awaited_once_with()
    sleep.assert_not_awaited()
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert any(
        status["code"] == "ASR_INDEPENDENT_FAILED" for status in statuses
    )
    assert statuses[-1]["code"] == "VOICE_INPUT_RECOVERY_FAILED"
    assert "private restart connect detail" not in str(
        runtime.send_status.await_args_list
    )


async def test_restart_default_attempts_follow_soniox_policy_ladder(
    monkeypatch,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    sleep = AsyncMock()
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    candidates = _install_failing_restart_candidates(
        runtime, "soniox", failure_count=3
    )
    assert runtime._asr_lifecycle.provider_policy.connect_max_attempts == 3

    await runtime._restart_transport()
    while runtime._asr_runtime._asr_close_tasks:
        await asyncio.gather(
            *tuple(runtime._asr_runtime._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(candidates) == 3
    for candidate in candidates:
        candidate.connect.assert_awaited_once_with()
        candidate.close.assert_awaited_once_with()
    assert [call.args for call in sleep.await_args_list] == [(0.25,), (0.5,)]
    statuses = [
        json.loads(call.args[0]) for call in runtime.send_status.await_args_list
    ]
    assert any(
        status["code"] == "ASR_INDEPENDENT_FAILED" for status in statuses
    )
    assert statuses[-1]["code"] == "VOICE_INPUT_RECOVERY_FAILED"


async def test_restart_explicit_attempt_override_beats_policy(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    sleep = AsyncMock()
    monkeypatch.setattr(runtime_module.asyncio, "sleep", sleep)
    candidates = _install_failing_restart_candidates(
        runtime, "soniox", failure_count=1
    )

    await runtime._restart_transport(max_attempts=1)
    while runtime._asr_runtime._asr_close_tasks:
        await asyncio.gather(
            *tuple(runtime._asr_runtime._asr_close_tasks),
            return_exceptions=True,
        )

    assert len(candidates) == 1
    candidates[0].connect.assert_awaited_once_with()
    sleep.assert_not_awaited()


async def test_restart_rejects_non_positive_attempt_override() -> None:
    runtime = _Runtime()

    with pytest.raises(ValueError, match="max_attempts must be positive"):
        await runtime._restart_transport(max_attempts=0)


async def test_connect_budget_stops_a_connect_it_cannot_finish(
    monkeypatch,
) -> None:
    # The other half: independent ASR IS wanted, so the decision would connect --
    # and a verdict produced after the frontend's deadline is worse than none,
    # because the client's timeout tears down the session that did start. Leave
    # the route on the blocked placeholder, which is what the caller would have
    # re-acked without re-deciding at all.
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled(
        "audio",
        handshake_override=True,
        connect_budget_seconds=0.0,
    )

    assert runtime._asr_route_mode == "blocked"
    start_mock.assert_not_awaited()


async def test_connect_budget_is_opt_in(monkeypatch) -> None:
    # Every other caller (hot-swap, device change, the ordinary start) passes no
    # budget and must keep connecting exactly as before.
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    async def _ready(**_kwargs):
        # Epoch read at call time: the teardown that precedes the connect bumps
        # it, and a result stamped with the pre-call value reads as stale.
        return AsrStartResult(
            status=AsrStartStatus.READY,
            provider="qwen",
            session_epoch=runtime._capture_ingress_token().session_epoch,
        )

    start_mock = AsyncMock(side_effect=_ready)
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled("audio", handshake_override=True)

    assert runtime._asr_route_mode == "independent"
    start_mock.assert_awaited_once()


async def test_provider_restart_reuses_accepted_session_optimization(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voiceInputResourceOptimizationEnabled": True,
            }
        ),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.READY,
            provider="qwen",
            session_epoch=0,
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled(
        "audio",
        resource_optimization_override=False,
    )
    assert runtime._voice_input_resource_optimization_session_value is False

    # A losing/deduplicated request may overwrite the shared handshake, but a
    # provider-changing restart still belongs to the already accepted session.
    runtime.set_voice_input_resource_optimization_handshake(True)
    runtime.core_api_type = "openai"
    await runtime._reconcile_independent_asr_after_core_change()

    assert start_mock.await_count == 2
    assert all(
        call.kwargs["resource_optimization_enabled"] is False
        for call in start_mock.await_args_list
    )


async def test_start_resolves_selection_off_event_loop(monkeypatch) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    selection = _selection("qwen", "provider")
    resolver_threads: list[threading.Thread] = []

    def resolver(core_type: str):
        assert core_type == "qwen"
        resolver_threads.append(threading.current_thread())
        return selection

    session = SimpleNamespace(
        is_ready=True,
        connect=AsyncMock(),
        close=AsyncMock(),
    )
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", resolver)
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        lambda _core_type, **_kwargs: session,
    )
    detector_factory = MagicMock(return_value=_ReadyDetector())
    monkeypatch.setattr(runtime_module, "DetectorRuntime", detector_factory)

    result = await runtime._asr_runtime.start(
        route_key="qwen",
        resource_optimization_enabled=False,
    )

    assert result.status is AsrStartStatus.READY
    assert len(resolver_threads) == 1
    assert resolver_threads[0] is not threading.main_thread()
    assert (
        detector_factory.call_args.kwargs["resource_optimization_enabled"] is False
    )
    assert detector_factory.call_args.kwargs["speaker_shadow"] is None
