"""Explicit nonretryable connection failures stop initial and warm startup."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import main_logic.asr_client.runtime as runtime_module
import main_logic.core as core_module
from main_logic.asr_client.provider_policy import resolve_provider_policy
from tests.support.asr_fakes import _Runtime, _selection
from tests.support.core_asr_harness import _ReadyDetector, _install_ready_lifecycle

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.parametrize("path", ["initial", "warm"])
@pytest.mark.parametrize("code", [
    "ASR_CREDENTIALS_REJECTED", "ASR_QWEN_SETUP_FAILED", "ASR_UNKNOWN_FUTURE_ERROR",
    "ASR_QWEN_CONNECTION_FAILED",
])
async def test_explicit_connect_code_controls_retry_before_any_input(monkeypatch, path, code):
    owner = _Runtime()
    selection = _selection("qwen", "provider")
    policy = replace(
        resolve_provider_policy("qwen", "provider"), connect_max_attempts=3,
        connect_retry_base_seconds=0.001, connect_retry_cap_seconds=0.001,
    )
    candidates = []

    def make_candidate(*_args, **_kwargs):
        candidate = SimpleNamespace(
            is_ready=True, last_failure_code=code,
            connect=AsyncMock(side_effect=RuntimeError("opaque diagnostic")),
            close=AsyncMock(), stream_audio=AsyncMock(),
        )
        candidates.append(candidate)
        return candidate

    try:
        if path == "initial":
            owner.core_api_type = "qwen"
            monkeypatch.setattr(core_module, "aload_global_conversation_settings", AsyncMock(
                return_value={"independentAsrEnabled": True},
            ))
            monkeypatch.setattr(runtime_module, "_resolve_asr_selection", MagicMock(return_value=selection))
            monkeypatch.setattr(runtime_module, "resolve_provider_policy", lambda *_args: policy)
            monkeypatch.setattr(runtime_module, "_create_asr_session_from_selection", make_candidate)
            monkeypatch.setattr(runtime_module, "DetectorRuntime", MagicMock(return_value=_ReadyDetector()))
            await owner._start_independent_asr_if_enabled("audio")
        else:
            owner._asr_session = SimpleNamespace(is_ready=False, close=AsyncMock())
            _install_ready_lifecycle(owner, "qwen")
            owner._asr_lifecycle.provider_policy = policy
            owner._asr_session_factory = make_candidate
            owner._asr_transport_selection = selection
            await owner._restart_transport(max_attempts=3)
        expected = 3 if code == "ASR_QWEN_CONNECTION_FAILED" else 1
        assert len(candidates) == expected
        for candidate in candidates:
            candidate.connect.assert_awaited_once()
            candidate.close.assert_awaited_once()
            candidate.stream_audio.assert_not_awaited()
        assert owner._asr_session is None
    finally:
        await owner._asr_runtime.close()
