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

import main_logic.core as core_module
from main_logic.asr_client.runtime import _provider_failure_reason
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


async def _start_with_session(monkeypatch, session) -> tuple[_Runtime, list[dict]]:
    import main_logic.asr_client.runtime as runtime_module

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
    monkeypatch.setattr(runtime_module.asyncio, "sleep", AsyncMock())

    await runtime._start_independent_asr_if_enabled("audio")
    assert runtime._asr_session is session
    return runtime, callbacks


def _session(*, warming_up: bool):
    session = type("Provider", (), {})()
    session.connect = AsyncMock()
    session.close = AsyncMock()
    session.provider_warmup_pending = warming_up
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
    assert _provider_failure_reason(message) == reason


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
