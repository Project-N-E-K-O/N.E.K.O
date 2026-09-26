import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from main_logic.asr_client.lifecycle import VoiceLifecycleState
import main_logic.core as core_module

from tests.support.asr_fakes import (
    _Runtime,
    _selection,
)

from tests.support.core_asr_harness import (
    _ReadyDetector,
)


async def _start_runtime_with_callback_candidates(
    monkeypatch,
    *,
    candidate_count: int = 2,
):
    import main_logic.asr_client.runtime as runtime_module

    runtime = _Runtime()
    runtime.core_api_type = "qwen"
    selection = _selection("qwen", "provider")
    selection_ref = selection
    detector = _ReadyDetector()
    callbacks: list[dict[str, object]] = []
    sessions = [
        SimpleNamespace(
            is_ready=True,
            connect=AsyncMock(),
            close=AsyncMock(),
        )
        for _ in range(candidate_count)
    ]

    def create_candidate(_core_type, *, selection: object, **kwargs):
        assert selection is selection_ref
        callbacks.append(kwargs)
        return sessions[len(callbacks) - 1]

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(
            return_value={
                "independentAsrEnabled": True,
                "voiceInputResourceOptimizationEnabled": False,
            }
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=selection),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        create_candidate,
    )
    monkeypatch.setattr(
        runtime_module,
        "DetectorRuntime",
        MagicMock(return_value=detector),
    )

    await runtime._start_independent_asr_if_enabled("audio")

    assert runtime._asr_session is sessions[0]
    assert runtime._asr_lifecycle.snapshot.state is VoiceLifecycleState.LOCAL_LISTEN
    assert runtime._asr_detector is detector
    assert runtime._asr_route_mode == "independent"
    return runtime, sessions, callbacks, detector


async def _wait_for_activation_output(
    call_count,
    *,
    expected_count: int,
) -> None:
    try:
        async with asyncio.timeout(1.0):
            while call_count() < expected_count:
                await asyncio.sleep(0)
    except TimeoutError as exc:
        raise AssertionError(
            "activation replay did not drain: "
            f"expected {expected_count}, received {call_count()}"
        ) from exc
