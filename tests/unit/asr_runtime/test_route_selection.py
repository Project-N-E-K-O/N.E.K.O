from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.asr_client import _AsrSelection
from main_logic.asr_client import runtime as runtime_module
from main_logic.asr_client._registry_meta import AsrProviderAvailability
from main_logic.asr_client.runtime import AsrStartResult, AsrStartStatus
import main_logic.core as core_module
from utils import preferences

from tests.support.asr_fakes import (
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


async def test_runtime_state_initializes_and_backfills_phase4a_fields() -> None:
    runtime = _Runtime()

    assert runtime._voice_input_resource_optimization_handshake_override is None
    assert runtime._voice_input_resource_optimization_session_value is None
    assert runtime._core_asr_preview_turn_token is None
    assert runtime._voice_input_external_suppressions == set()

    del runtime._voice_input_resource_optimization_handshake_override
    del runtime._voice_input_resource_optimization_session_value
    del runtime._core_asr_preview_turn_token
    del runtime._voice_input_external_suppressions
    runtime._ensure_asr_runtime_state()

    assert runtime._voice_input_resource_optimization_handshake_override is None
    assert runtime._voice_input_resource_optimization_session_value is None
    assert runtime._core_asr_preview_turn_token is None
    assert runtime._voice_input_external_suppressions == set()


async def test_independent_asr_setting_is_persisted_as_a_boolean() -> None:
    assert "independentAsrEnabled" in preferences._ALLOWED_CONVERSATION_SETTINGS
    assert (
        "voiceInputResourceOptimizationEnabled"
        in preferences._ALLOWED_CONVERSATION_SETTINGS
    )
    assert (
        "voice_input_resource_optimization_enabled"
        not in preferences._ALLOWED_CONVERSATION_SETTINGS
    )


async def test_start_session_handshake_true_overrides_persisted_disabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_independent_asr_handshake(True)
    await runtime._start_independent_asr_if_enabled("audio")

    # The handshake beats the stale persisted value: the independent runtime
    # start is attempted instead of the native fallback.
    start_mock.assert_awaited_once()
    assert runtime._asr_route_mode != "native"


async def test_start_session_handshake_false_overrides_persisted_enabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_independent_asr_handshake(False)
    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_not_awaited()
    assert runtime._asr_route_mode == "native"


async def test_resource_optimization_handshake_false_overrides_persisted_enabled(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
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
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_voice_input_resource_optimization_handshake(False)
    await runtime._start_independent_asr_if_enabled("audio")

    assert start_mock.await_args.kwargs["resource_optimization_enabled"] is False
    assert runtime._speaker_shadow_factory is None
    assert "speaker_shadow_factory" not in start_mock.await_args.kwargs


@pytest.mark.parametrize("malformed", ["false", 0, 1, [False], {"enabled": False}])
async def test_resource_optimization_handshake_malformed_falls_back_to_persisted(
    monkeypatch,
    malformed,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
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
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_voice_input_resource_optimization_handshake(malformed)
    await runtime._start_independent_asr_if_enabled("audio")

    assert start_mock.await_args.kwargs["resource_optimization_enabled"] is True


async def test_start_session_handshake_missing_falls_back_to_persisted(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    # An absent field (forwarded as None by the router) clears any override a
    # previous session left behind, restoring the persisted-setting behavior.
    runtime.set_independent_asr_handshake(True)
    runtime.set_independent_asr_handshake(None)
    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_awaited_once()


async def test_missing_independent_asr_setting_defaults_disabled(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={}),
    )
    start_mock = AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_not_awaited()
    assert runtime._asr_route_mode == "native"


@pytest.mark.parametrize("malformed", ["true", 1, 0, [True], {"enabled": True}])
async def test_start_session_handshake_malformed_value_is_ignored(
    monkeypatch,
    malformed,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    # Strict bool typing: truthy non-bool values never enable the route.
    runtime.set_independent_asr_handshake(malformed)
    await runtime._start_independent_asr_if_enabled("audio")

    start_mock.assert_not_awaited()
    assert runtime._asr_route_mode == "native"


async def test_disabled_or_text_session_never_creates_provider(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    factory = MagicMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": False}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        factory,
    )

    await runtime._start_independent_asr_if_enabled("audio")
    await runtime._start_independent_asr_if_enabled("text")

    factory.assert_not_called()
    assert runtime._asr_route_mode == "blocked"
    assert not hasattr(runtime._asr_runtime, "_asr_route_mode")


@pytest.mark.parametrize(
    ("persisted_enabled", "handshake_enabled"),
    [
        (True, None),
        (False, None),
        (False, True),
        (True, False),
    ],
)
async def test_free_core_always_uses_native_asr_regardless_of_toggle(
    monkeypatch,
    persisted_enabled: bool,
    handshake_enabled: bool | None,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "free"
    runtime.session.stream_audio = AsyncMock()
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": persisted_enabled}),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)
    runtime.set_independent_asr_handshake(handshake_enabled)

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    assert runtime._independent_asr_route_key == "free"
    assert runtime._independent_asr_provider is None
    start_mock.assert_not_awaited()
    assert "ASR_INDEPENDENT_DISABLED" in runtime.send_status.await_args.args[0]
    assert "ASR_INDEPENDENT_UNAVAILABLE" not in runtime.send_status.await_args.args[0]

    assert await runtime._route_microphone_audio(
        b"\x01\x00" * 160,
        sample_rate_hz=16_000,
    ) is True
    runtime.session.stream_audio.assert_awaited_once_with(b"\x01\x00" * 160)


async def test_free_core_uses_native_asr_when_preferences_are_unreadable(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "free"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(side_effect=OSError("preferences unavailable")),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)
    runtime.set_independent_asr_handshake(True)

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    start_mock.assert_not_awaited()
    assert "ASR_INDEPENDENT_DISABLED" in runtime.send_status.await_args.args[0]


async def test_free_core_ignores_persisted_local_asr_preference(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "free"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "independentAsrProviderPreference": "faster_whisper",
            }
        ),
    )
    start_mock = AsyncMock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_route_mode == "native"
    start_mock.assert_not_awaited()
    assert "ASR_INDEPENDENT_DISABLED" in runtime.send_status.await_args.args[0]


async def test_local_asr_preference_reaches_resolver_and_reports_missing_dependency(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "independentAsrProviderPreference": "faster_whisper",
            }
        ),
    )
    resolver = MagicMock(
        return_value=_AsrSelection(
            provider_key="faster_whisper",
            endpointing_mode="manual",
            availability=AsrProviderAvailability.MISSING_DEPENDENCY,
        )
    )
    builder = MagicMock()
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", resolver)
    monkeypatch.setattr(runtime_module, "_create_asr_session_from_selection", builder)

    await runtime._start_independent_asr_if_enabled("audio")

    resolver.assert_called_once_with("gemini", provider_preference="faster_whisper")
    builder.assert_not_called()
    assert runtime._asr_route_mode == "blocked"
    statuses = [call.args[0] for call in runtime.send_status.await_args_list]
    assert any("ASR_INDEPENDENT_DEPENDENCY_MISSING" in status for status in statuses)
    assert not any("ASR_INDEPENDENT_UNAVAILABLE" in status for status in statuses)


async def test_auto_preference_resolves_exactly_like_no_preference(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "independentAsrProviderPreference": "auto",
            }
        ),
    )
    resolver = MagicMock(side_effect=RuntimeError("stop after resolve"))
    monkeypatch.setattr(runtime_module, "_resolve_asr_selection", resolver)

    await runtime._start_independent_asr_if_enabled("audio")

    resolver.assert_called_once_with("gemini")


def _stale_start_mock() -> AsyncMock:
    return AsyncMock(
        return_value=AsrStartResult(
            status=AsrStartStatus.FAILED,
            failure_code="ASR_START_STALE",
        )
    )


@pytest.mark.parametrize(
    ("persisted", "handshake", "expected"),
    [
        # The settings POST has not landed yet: the handshake wins both ways.
        ("auto", "faster_whisper", "faster_whisper"),
        ("faster_whisper", "auto", "auto"),
        # A malformed handshake is treated as "auto", never as a provider.
        ("faster_whisper", "qwen", "auto"),
        ("faster_whisper", 1, "auto"),
        # Absent field (older frontend / non-authoritative window): persisted.
        ("faster_whisper", None, "faster_whisper"),
        (None, None, "auto"),
    ],
)
async def test_provider_preference_handshake_overrides_persisted_setting(
    monkeypatch,
    persisted,
    handshake,
    expected,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    settings = {"independentAsrEnabled": True}
    if persisted is not None:
        settings["independentAsrProviderPreference"] = persisted
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value=settings),
    )
    start_mock = _stale_start_mock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    runtime.set_independent_asr_provider_preference_handshake(handshake)
    await runtime._start_independent_asr_if_enabled("audio")

    assert start_mock.await_args.kwargs["provider_preference"] == expected


async def test_provider_preference_snapshot_beats_the_shared_field_and_sticks(
    monkeypatch,
) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "independentAsrProviderPreference": "auto",
            }
        ),
    )
    start_mock = _stale_start_mock()
    monkeypatch.setattr(runtime._asr_runtime, "start", start_mock)

    # A later request overwrote the shared field; this start's own snapshot
    # (carried down from its start_session) still decides.
    runtime.set_independent_asr_provider_preference_handshake("auto")
    await runtime._start_independent_asr_if_enabled(
        "audio",
        provider_preference_override="faster_whisper",
    )
    assert start_mock.await_args.kwargs["provider_preference"] == "faster_whisper"

    # Internal re-entry (hot swap / device change) has no request of its own
    # and reuses the accepted session choice, not the overwritten shared field.
    await runtime._start_independent_asr_if_enabled("audio")
    assert start_mock.await_args.kwargs["provider_preference"] == "faster_whisper"
