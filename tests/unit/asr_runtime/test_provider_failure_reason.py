"""The provider's own failure code and warm-up state reach the client.

A provider session reports failures as ``"<ASR_CODE>: <message>"``. The runtime
keeps routing on its generic codes but forwards the provider code as an opaque
``reason`` so the client can explain e.g. a local model that failed to load.
A provider that is still preparing when it connects is announced as
``ASR_INDEPENDENT_PREPARING``.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.asr_client.runtime as runtime_module
import main_logic.core as core_module
from tests.support.asr_fakes import _Runtime, _selection

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


def _sent_statuses(runtime: _Runtime) -> list[dict]:
    statuses = []
    for sent in runtime.send_status.await_args_list:
        try:
            statuses.append(json.loads(sent.args[0]))
        except (IndexError, TypeError, ValueError):
            continue
    return statuses


async def _start_with_session(
    monkeypatch, session, *, instant_sleep: bool = True
) -> tuple[_Runtime, list[dict]]:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    selection = _selection("soniox", "provider")
    callbacks: list[dict] = []

    def build_candidate(_core_type, *, selection, **kwargs):
        callbacks.append(kwargs)
        return session

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module, "_resolve_asr_selection", MagicMock(return_value=selection)
    )
    monkeypatch.setattr(
        runtime_module, "_create_asr_session_from_selection", build_candidate
    )
    if instant_sleep:
        monkeypatch.setattr(runtime_module.asyncio, "sleep", AsyncMock())

    await runtime._start_independent_asr_if_enabled("audio")
    assert runtime._asr_session is session
    return runtime, callbacks


def _session(*, warming_up: bool, reason: str = ""):
    session = type("Provider", (), {})()
    session.connect = AsyncMock()
    session.close = AsyncMock()
    session.provider_warmup_snapshot = (warming_up, None)
    session.provider_warmup_reason = reason
    return session


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("ASR_LOCAL_MODEL_LOAD_FAILED: model could not be downloaded", "ASR_LOCAL_MODEL_LOAD_FAILED"),
        ("ASR_LOCAL_DECODE_BACKLOG: behind", "ASR_LOCAL_DECODE_BACKLOG"),
        ("ASR_WORKER_FAILED: worker closed unexpectedly", ""),
        ("no code here", ""),
        ("", ""),
    ],
)
async def test_provider_failure_reason_is_the_leading_code(message, reason) -> None:
    assert runtime_module._provider_failure_reason(message) == reason


async def test_provider_failure_code_is_forwarded_as_reason(monkeypatch) -> None:
    runtime, callbacks = await _start_with_session(
        monkeypatch, _session(warming_up=False)
    )

    await callbacks[0]["on_connection_error"](
        "ASR_LOCAL_MODEL_LOAD_FAILED: faster-whisper model could not be downloaded"
    )
    await asyncio.sleep(0)

    assert runtime._asr_route_mode == "blocked"
    failures = [
        status for status in _sent_statuses(runtime)
        if status.get("code") == "ASR_INDEPENDENT_FAILED"
    ]
    assert failures
    assert all(
        status["details"].get("reason") == "ASR_LOCAL_MODEL_LOAD_FAILED"
        for status in failures
    )
    # Only the code travels, never the provider's message text.
    assert "could not be downloaded" not in str(runtime.send_status.await_args_list)


async def test_generic_worker_failure_carries_no_reason(monkeypatch) -> None:
    runtime, callbacks = await _start_with_session(
        monkeypatch, _session(warming_up=False)
    )

    await callbacks[0]["on_connection_error"]("ASR_WORKER_FAILED: worker closed")
    await asyncio.sleep(0)

    failures = [
        status for status in _sent_statuses(runtime)
        if status.get("code") == "ASR_INDEPENDENT_FAILED"
    ]
    assert failures
    assert all("reason" not in status["details"] for status in failures)


@pytest.mark.parametrize("warming_up", [True, False])
async def test_connecting_while_the_provider_prepares_is_announced(
    monkeypatch, warming_up
) -> None:
    runtime, _callbacks = await _start_with_session(
        monkeypatch, _session(warming_up=warming_up)
    )

    codes = [status.get("code") for status in _sent_statuses(runtime)]
    assert "ASR_INDEPENDENT_READY" in codes
    assert ("ASR_INDEPENDENT_PREPARING" in codes) is warming_up
    if warming_up:
        assert codes.index("ASR_INDEPENDENT_PREPARING") > codes.index(
            "ASR_INDEPENDENT_READY"
        )


async def test_failure_right_after_ready_keeps_its_code_through_connect() -> None:
    # The worker reports ready and fails before the caller has adopted the
    # session (e.g. a broken local install that fails at once). connect()
    # must surface the worker's code, not a generic one.
    from main_logic.asr_client._infra import (
        AsrSessionConfig,
        _AsrWorkerEvent,
        _RealtimeAsrSessionImpl,
    )

    async def failing_after_ready(request_queue, response_queue, _api_key, _config):
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=0,
                error_code="ASR_LOCAL_MODEL_LOAD_FAILED",
                error_message="faster-whisper model could not be loaded",
            )
        )
        while True:
            request = await request_queue.get()
            request_queue.task_done()
            if request.kind == "shutdown":
                await response_queue.put(
                    _AsrWorkerEvent(kind="closed", generation=request.generation)
                )
                return

    session = _RealtimeAsrSessionImpl(
        worker_fn=failing_after_ready,
        api_key="",
        config=AsrSessionConfig(endpointing_mode="manual"),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    with pytest.raises(RuntimeError) as excinfo:
        await session.connect()
    assert str(excinfo.value).startswith("ASR_LOCAL_MODEL_LOAD_FAILED:")
    await session.close()


async def test_start_failure_carries_the_provider_code_as_reason(monkeypatch) -> None:
    runtime = _Runtime()
    runtime.core_api_type = "gemini"
    session = type("Provider", (), {})()
    session.connect = AsyncMock(
        side_effect=RuntimeError(
            "ASR_LOCAL_MODEL_LOAD_FAILED: faster-whisper model could not be loaded"
        )
    )
    session.close = AsyncMock()

    monkeypatch.setattr(
        core_module,
        "aload_global_conversation_settings",
        AsyncMock(return_value={"independentAsrEnabled": True}),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        MagicMock(return_value=_selection("soniox", "provider")),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        lambda _core_type, *, selection, **kwargs: session,
    )
    monkeypatch.setattr(runtime_module.asyncio, "sleep", AsyncMock())

    await runtime._start_independent_asr_if_enabled("audio")

    failures = [
        status for status in _sent_statuses(runtime)
        if status.get("code", "").startswith("ASR_INDEPENDENT_")
        and status.get("code") != "ASR_INDEPENDENT_READY"
    ]
    assert failures
    assert all(
        status["details"].get("reason") == "ASR_LOCAL_MODEL_LOAD_FAILED"
        for status in failures
    )


async def test_blocked_lifecycle_carries_the_failure_reason(monkeypatch) -> None:
    # A recording window may drop the later FAILED status on its lease check,
    # so BLOCKED itself must say why.
    runtime, callbacks = await _start_with_session(
        monkeypatch, _session(warming_up=False)
    )
    await callbacks[0]["on_connection_error"](
        "ASR_LOCAL_MODEL_LOAD_FAILED: faster-whisper model could not be loaded"
    )
    await asyncio.sleep(0)
    blocked = [
        status for status in _sent_statuses(runtime)
        if status.get("code") == "ASR_LIFECYCLE_STATE"
        and status["details"].get("state") == "blocked"
    ]
    assert blocked
    assert blocked[-1]["details"].get("reason") == "ASR_LOCAL_MODEL_LOAD_FAILED"


async def test_runtime_failure_code_becomes_the_blocked_reason() -> None:
    runtime = _Runtime()
    asr = type("Asr", (), {})()
    asr.close = AsyncMock()
    runtime._asr_session = asr
    runtime._asr_route_mode = "independent"
    runtime._asr_provider = "faster_whisper"
    runtime._asr_lifecycle = None
    epoch = runtime._asr_session_epoch
    await runtime._handle_independent_asr_error(
        epoch, "faster_whisper", status_code="ASR_PROVIDER_WARMUP_TIMEOUT"
    )
    await asyncio.sleep(0)
    blocked = [
        status for status in _sent_statuses(runtime)
        if status.get("code") == "ASR_LIFECYCLE_STATE"
        and status["details"].get("state") == "blocked"
    ]
    assert blocked
    assert blocked[-1]["details"].get("reason") == "ASR_PROVIDER_WARMUP_TIMEOUT"


async def test_preparing_says_why_and_prepared_follows_when_ready(monkeypatch) -> None:
    monkeypatch.setattr(runtime_module, "_PROVIDER_WARMUP_POLL_SECONDS", 0.01)
    session = _session(warming_up=True, reason="ASR_LOCAL_MODEL_RELOADING")
    # Real sleeps: an instant one would also fire the idle-transport expiry
    # at once and close the session before it could get ready.
    runtime, _callbacks = await _start_with_session(
        monkeypatch, session, instant_sleep=False
    )

    preparing = [
        status for status in _sent_statuses(runtime)
        if status.get("code") == "ASR_INDEPENDENT_PREPARING"
    ]
    assert preparing
    assert preparing[-1]["details"].get("reason") == "ASR_LOCAL_MODEL_RELOADING"
    assert "ASR_INDEPENDENT_PREPARED" not in [
        status.get("code") for status in _sent_statuses(runtime)
    ]

    # The runtime keeps watching and tells the client once the model is ready.
    session.provider_warmup_snapshot = (False, None)
    task = runtime._asr_warmup_watch_task
    assert task is not None
    await asyncio.wait_for(task, 2)
    codes = [status.get("code") for status in _sent_statuses(runtime)]
    assert codes.count("ASR_INDEPENDENT_PREPARED") == 1


async def test_worker_that_queued_its_error_and_exited_keeps_the_code() -> None:
    # The real worker records and enqueues its model-load error and returns
    # at once; connect() may find it already done with the error unread.
    from main_logic.asr_client._infra import (
        AsrSessionConfig,
        _AsrWorkerEvent,
        _RealtimeAsrSessionImpl,
    )
    from main_logic.asr_client.worker_failure import record_worker_failure

    async def fail_and_exit(request_queue, response_queue, _api_key, _config):
        await response_queue.put(_AsrWorkerEvent(kind="ready", generation=0))
        record_worker_failure(
            request_queue,
            "ASR_LOCAL_MODEL_LOAD_FAILED",
            "faster-whisper model could not be loaded",
        )
        await response_queue.put(
            _AsrWorkerEvent(
                kind="error",
                generation=0,
                error_code="ASR_LOCAL_MODEL_LOAD_FAILED",
                error_message="faster-whisper model could not be loaded",
            )
        )

    session = _RealtimeAsrSessionImpl(
        worker_fn=fail_and_exit,
        api_key="",
        config=AsrSessionConfig(endpointing_mode="manual"),
        on_input_transcript=AsyncMock(),
        on_connection_error=AsyncMock(),
    )
    with pytest.raises(RuntimeError) as excinfo:
        await session.connect()
    assert str(excinfo.value).startswith("ASR_LOCAL_MODEL_LOAD_FAILED:")
    await session.close()
