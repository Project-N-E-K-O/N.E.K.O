import ast
import asyncio
import inspect
import time
from unittest.mock import AsyncMock, MagicMock, call
import pytest
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.core import LLMSessionManager
from main_logic.core.asr_runtime import AsrRuntimeMixin, _HotSwapAudioFrame
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
import main_logic.core as core_module

from tests.support.asr_fakes import (
    _Runtime,
    _selection,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_native_route_installs_future_verifier_but_reports_unsupported() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime._asr_runtime.set_speaker_verifier_factory = AsyncMock(return_value=True)
    factory = MagicMock()

    result = await runtime.set_speaker_verifier_factory(
        factory,
        activation_generation="profile-generation",
    )

    assert result is VoiceIdentityActivationResult.UNSUPPORTED_ASR_ROUTE
    assert runtime._speaker_shadow_factory is factory


async def test_native_route_is_sufficient_to_authorize_omni_audio() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.session.stream_audio = AsyncMock()

    consumed = await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    )

    assert consumed is True
    assert runtime._asr_route_mode == "native"
    runtime.session.stream_audio.assert_awaited_once()
    assert not hasattr(runtime._asr_runtime, "_asr_required")


async def test_close_failure_keeps_the_requested_blocked_route() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock(side_effect=RuntimeError("close failed"))
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    await runtime._close_independent_asr(next_route_mode="blocked")

    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_route_mode")
    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )


async def test_close_requires_callers_to_declare_the_next_route() -> None:
    parameter = inspect.signature(AsrRuntimeMixin._close_independent_asr).parameters[
        "next_route_mode"
    ]

    assert parameter.default is inspect.Parameter.empty


async def test_start_uses_current_core_route_only_after_provider_ready(
    monkeypatch,
) -> None:
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
        MagicMock(return_value=_selection("gemini")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    asr.connect.assert_awaited_once_with()
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "gemini"
    assert runtime._asr_route_mode == "independent"
    assert factory.call_args.args == ("gemini",)
    assert factory.call_args.kwargs["selection"].provider_key == "gemini"


@pytest.mark.parametrize("core_type", ["qwen", "qwen_intl"])
async def test_qwen_core_starts_independent_asr_with_external_turn_support(
    monkeypatch,
    core_type: str,
) -> None:
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = core_type
    runtime.session.set_visual_delivery_mode = MagicMock()
    runtime.session.block_raw_visual_delivery = MagicMock()
    asr = type("Asr", (), {})()

    async def connect_after_visual_fail_closed() -> None:
        delivered_modes = [
            getattr(call.args[0], "value", call.args[0])
            for call in runtime.session.set_visual_delivery_mode.call_args_list
        ]
        assert "external_description" not in delivered_modes
        runtime.session.block_raw_visual_delivery.assert_called()

    asr.connect = AsyncMock(side_effect=connect_after_visual_fail_closed)
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
        MagicMock(return_value=_selection("qwen")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")

    factory.assert_called_once()
    asr.connect.assert_awaited_once_with()
    assert runtime._asr_route_mode == "independent"
    assert runtime._asr_session is asr
    assert runtime._asr_provider == "qwen"


async def test_native_to_blocked_fences_raw_frames_during_route_reconciliation() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    replacement_session = type("ReplacementOmni", (), {})()
    replacement_session.set_visual_delivery_mode = MagicMock()
    replacement_session.block_raw_visual_delivery = MagicMock()
    runtime.session = replacement_session

    runtime._set_microphone_route("blocked")

    replacement_session.set_visual_delivery_mode.assert_called_once_with("native")
    replacement_session.block_raw_visual_delivery.assert_called_once_with()


async def test_disabled_native_route_key_prevents_same_core_reconcile(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    runtime.input_mode = "audio"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    assert runtime._independent_asr_route_key == "gemini"
    runtime.is_active = True
    runtime.is_hot_swap_imminent = True
    runtime.session.stream_audio = AsyncMock()
    old_token = runtime._capture_ingress_token()
    assert runtime.hot_swap_audio_cache.append(
        _HotSwapAudioFrame(
            pcm16=b"\x01\x00" * 160,
            token=old_token,
            audio_stream_epoch=runtime._audio_stream_epoch,
        )
    )
    runtime._set_microphone_route("blocked")
    runtime._set_microphone_route("native")
    runtime._start_independent_asr_if_enabled = AsyncMock()
    await runtime._reconcile_independent_asr_after_core_change()
    runtime._start_independent_asr_if_enabled.assert_not_awaited()
    await runtime._flush_hot_swap_audio_cache()
    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00" * 160)
    assert runtime._omni_mic_audio_bytes == 320


async def test_connect_budget_does_not_block_a_free_native_route(
    monkeypatch,
) -> None:
    # Codex P2. The budget bounds the PROVIDER CONNECT, nothing else. A request
    # whose handshake disables independent ASR settles on native without talking
    # to anyone, so refusing it over a connect budget would leave the route on
    # its blocked placeholder and abort a microphone start that had nothing to
    # wait for.
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
        handshake_override=False,
        connect_budget_seconds=0.0,
    )

    assert runtime._asr_route_mode == "native"
    start_mock.assert_not_awaited()


async def test_session_activation_resolves_asr_before_frontend_ack() -> None:
    order: list[str] = []
    manager = LLMSessionManager.__new__(LLMSessionManager)
    manager.lock = asyncio.Lock()
    manager.input_cache_lock = asyncio.Lock()
    manager.is_active = False
    manager._session_turn_count = 0
    manager.session_start_failure_count = 1
    manager.session_start_last_failure_time = 1.0
    manager._memory_error_retry_after = 1.0
    manager._session_start_circuit_open = True
    manager.pending_agent_callbacks = []
    manager._activity_tracker = type(
        "Tracker", (), {"on_voice_mode": lambda self, value: None}
    )()
    manager.is_goodbye_silent = lambda: False
    manager._drain_pending_context_appends_before_ready = AsyncMock()
    manager._flush_pending_input_data = AsyncMock()
    manager._consume_next_session_context_messages = MagicMock()
    manager._start_independent_asr_if_enabled = AsyncMock(
        side_effect=lambda _mode, **_kwargs: order.append("asr")
    )
    manager.send_session_started = AsyncMock(
        side_effect=lambda _mode, **_kwargs: order.append("started")
    )

    stop = asyncio.Event()

    class _Session:
        async def handle_messages(self) -> None:
            await stop.wait()

    manager.session = _Session()

    await LLMSessionManager._start_session_activate(
        manager,
        "audio",
        0,
        time.time(),
    )

    assert order == ["asr", "started"]
    stop.set()
    await manager.message_handler_task


async def test_blocked_route_consumes_audio_without_an_asr_or_omni_send() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "blocked"

    assert (
        await runtime._route_microphone_audio(
            b"\x00\x00",
            sample_rate_hz=16_000,
        )
        is True
    )
    assert runtime._asr_route_mode == "blocked"


async def test_independent_route_without_ready_session_blocks_omni() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {"is_ready": False})()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"

    assert (
        await runtime._route_microphone_audio(b"\x00\x00", sample_rate_hz=16_000)
        is True
    )
    assert runtime._asr_route_mode == "blocked"


async def test_current_native_send_failure_still_closes_route_once() -> None:
    runtime = _Runtime()
    runtime._set_microphone_route("native")
    runtime.session_closed_by_server = False
    runtime.session.stream_audio = AsyncMock(
        side_effect=RuntimeError("connection closed")
    )

    await runtime._route_microphone_audio(
        b"\x01\x00",
        sample_rate_hz=16_000,
        ingress_token=runtime._capture_native_ingress_token(),
    )

    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00")
    assert runtime.session_closed_by_server is True
    assert runtime._omni_mic_audio_bytes == 0


@pytest.mark.parametrize("enabled", [False, True])
async def test_cold_start_with_unclaimed_lease_still_routes(
    monkeypatch, enabled: bool
) -> None:
    """The bundled frontend flips the lease owner to "core" only after
    session_started, so route setup must not require owner=="core": gating
    the start on lease state would leave every cold start blocked."""

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
        AsyncMock(return_value={"independentAsrEnabled": enabled}),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    if enabled:
        assert runtime._asr_route_mode == "independent"
        assert runtime._independent_asr_provider == "qwen"
    else:
        assert runtime._asr_route_mode == "native"


async def test_teardown_routines_share_one_turn_state_reset() -> None:
    import ast
    import inspect as inspect_module

    from main_logic.asr_client import runtime as runtime_module

    source = inspect_module.getsource(runtime_module.IndependentAsrRuntime)
    tree = ast.parse(source)
    class_node = tree.body[0]
    for method_name in (
        "_detach_independent_asr",
        "_abort_transport",
        "_handle_independent_asr_error",
    ):
        method = next(
            node
            for node in class_node.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
            and node.name == method_name
        )
        calls = {
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
        }
        assert "_reset_asr_turn_state" in calls, method_name


@pytest.mark.unit
async def test_native_route_leaves_provider_capability_routing_inside_session() -> None:
    """Core selects the ASR strategy, while session capability keeps legacy behavior."""
    runtime = _Runtime()
    runtime.session._supports_native_image = False
    runtime.session.set_visual_delivery_mode = MagicMock()

    runtime._set_microphone_route("native")

    delivered_mode = runtime.session.set_visual_delivery_mode.call_args.args[0]
    assert getattr(delivered_mode, "value", delivered_mode) == "native"
