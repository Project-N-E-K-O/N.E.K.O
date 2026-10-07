"""Use the real Soniox worker/session and shipped policy before any audio write."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.asr_client.runtime as runtime_module
import main_logic.core as core_module
from main_logic.asr_client import _AsrSelection
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.asr_client.recovery import FailureSource, RecoveryDisposition, classify_failure
from main_logic.asr_client.workers import soniox
from tests.support.asr_fakes import _Runtime
from tests.support.core_asr_harness import _ReadyDetector, _install_ready_lifecycle

pytestmark = pytest.mark.runtime


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["initial", "warm"])
@pytest.mark.parametrize("status,code,attempts", [
    (503, "ASR_SONIOX_RETRYABLE", 3),
    (408, "ASR_SONIOX_RETRYABLE", 3),
    (500, "ASR_SONIOX_RETRYABLE", 3),
    (429, "ASR_RATE_LIMITED", 3),
    (413, "ASR_SONIOX_CONNECTION_LIMIT", 3),
    (401, "ASR_CREDENTIALS_REJECTED", 1),
    (402, "ASR_BUDGET_EXHAUSTED", 1),
    (403, "ASR_CREDENTIALS_EXPIRED", 1),
])
async def test_soniox_handshake_retry_uses_real_worker_policy_and_retires_candidates(
    monkeypatch, path, status, code, attempts,
):
    owner = _Runtime()
    selection = _AsrSelection(
        "soniox", "provider", _worker_fn=partial(soniox.soniox_asr_worker, region="us"),
        _api_key="controlled-key",
    )
    policy = resolve_provider_policy("soniox", "provider")
    assert policy.connect_max_attempts == 3
    failure = RuntimeError("controlled handshake failure")
    failure.response = SimpleNamespace(status_code=status)
    handshake = AsyncMock(side_effect=failure)
    monkeypatch.setattr(soniox.websockets, "connect", handshake)
    candidates = []
    factory = runtime_module._create_asr_session_from_selection

    def make_candidate(*args, **kwargs):
        candidate = factory(*args, **kwargs)
        candidates.append(candidate)
        return candidate

    try:
        if path == "initial":
            owner.core_api_type = "qwen"
            monkeypatch.setattr(core_module, "aload_global_conversation_settings", AsyncMock(
                return_value={"independentAsrEnabled": True},
            ))
            monkeypatch.setattr(runtime_module, "_resolve_asr_selection", MagicMock(return_value=selection))
            monkeypatch.setattr(runtime_module, "_create_asr_session_from_selection", make_candidate)
            monkeypatch.setattr(runtime_module, "DetectorRuntime", MagicMock(return_value=_ReadyDetector()))
            await owner._start_independent_asr_if_enabled("audio")
        else:
            owner._asr_session = SimpleNamespace(is_ready=False, close=AsyncMock())
            _install_ready_lifecycle(owner, "soniox")
            owner._asr_lifecycle.provider_policy = policy
            owner._asr_session_factory = lambda _selection: make_candidate(
                "qwen", selection=selection, on_input_transcript=AsyncMock(),
                on_connection_error=AsyncMock(), external_endpointing_runtime=True,
            )
            owner._asr_transport_selection = selection
            await owner._restart_transport()
        assert handshake.await_count == attempts
        assert len(candidates) == attempts
        assert all(candidate.last_failure_code == code for candidate in candidates)
        assert all(candidate._provider_wire_audio_bytes == 0 for candidate in candidates)
        assert all(candidate._worker_task is None or candidate._worker_task.done() for candidate in candidates)
        assert owner._asr_session is None
    finally:
        await owner._asr_runtime.close()


@pytest.mark.parametrize("code", [
    "ASR_SONIOX_RETRYABLE", "ASR_SONIOX_CONNECTION_LIMIT", "ASR_RATE_LIMITED", "ASR_CONNECT_TIMEOUT",
])
@pytest.mark.parametrize("source", list(FailureSource))
def test_connect_only_retry_never_grants_active_transport_recovery(code, source):
    expected = RecoveryDisposition.RETRY_CONNECT if source is FailureSource.CONNECT else RecoveryDisposition.STOP
    assert classify_failure(code, source=source) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["initial", "warm"])
async def test_soniox_ready_timeout_obeys_connect_attempt_limit(monkeypatch, path):
    from main_logic.asr_client import _infra

    monkeypatch.setattr(_infra, "_READY_TIMEOUT_SECONDS", 0.01)
    owner = _Runtime()
    candidates = []

    async def never_ready(requests, _responses, _api_key, _config):
        while True:
            request = await requests.get()
            try:
                if request.kind == "shutdown":
                    return
            finally:
                requests.task_done()

    selection = _AsrSelection("soniox", "provider", _worker_fn=never_ready, _api_key="controlled-key")
    policy = resolve_provider_policy("soniox", "provider")
    factory = runtime_module._create_asr_session_from_selection

    def make_candidate(*args, **kwargs):
        candidate = factory(*args, **kwargs)
        candidates.append(candidate)
        return candidate

    try:
        if path == "initial":
            owner.core_api_type = "qwen"
            monkeypatch.setattr(core_module, "aload_global_conversation_settings", AsyncMock(
                return_value={"independentAsrEnabled": True},
            ))
            monkeypatch.setattr(runtime_module, "_resolve_asr_selection", MagicMock(return_value=selection))
            monkeypatch.setattr(runtime_module, "_create_asr_session_from_selection", make_candidate)
            monkeypatch.setattr(runtime_module, "DetectorRuntime", MagicMock(return_value=_ReadyDetector()))
            await owner._start_independent_asr_if_enabled("audio")
        else:
            owner._asr_session = SimpleNamespace(is_ready=False, close=AsyncMock())
            _install_ready_lifecycle(owner, "soniox")
            owner._asr_lifecycle.provider_policy = policy
            owner._asr_session_factory = lambda _selection: make_candidate(
                "qwen", selection=selection, on_input_transcript=AsyncMock(),
                on_connection_error=AsyncMock(), external_endpointing_runtime=True,
            )
            owner._asr_transport_selection = selection
            await owner._restart_transport()
        assert len(candidates) == policy.connect_max_attempts == 3
        assert all(candidate.last_failure_code == "ASR_CONNECT_TIMEOUT" for candidate in candidates)
        assert all(candidate._provider_wire_audio_bytes == 0 for candidate in candidates)
        assert owner._asr_session is None
    finally:
        await owner._asr_runtime.close()
