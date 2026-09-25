import asyncio
from dataclasses import FrozenInstanceError

import pytest

from main_logic.asr_client.endpointing.admission_policy import resolve_admission_policy
from main_logic.asr_client.runtime import AsrStartStatus, IndependentAsrRuntime
from tests.unit.test_asr_client import (
    _RuntimeStartCandidate,
    _patch_runtime_start,
    _runtime_callbacks,
)


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_explicit_policy_snapshot(mode):
    environment = {"NEKO_ASR_ADMISSION": "1", "NEKO_ASR_SHORT_SPEECH": mode}
    policy = resolve_admission_policy(environment)
    environment["NEKO_ASR_SHORT_SPEECH"] = "off"
    assert policy.mode == mode
    assert (policy.config is not None) == (mode == "enforce")
    assert (policy.shadow_config is not None) == (mode == "shadow")
    with pytest.raises(FrozenInstanceError):
        policy.mode = "off"


def test_default_is_legacy_and_invalid_opt_in_is_not_silent():
    assert resolve_admission_policy({}).mode == "off"
    assert not resolve_admission_policy({}).enabled
    for environment in (
        {"NEKO_ASR_SHORT_SPEECH": "enforce"},
        {"NEKO_ASR_ADMISSION": "1", "NEKO_ASR_SHORT_SPEECH": "invalid"},
    ):
        with pytest.raises(ValueError):
            resolve_admission_policy(environment)


@pytest.mark.asyncio
async def test_start_freezes_policy_before_connection_await(monkeypatch):
    monkeypatch.setenv("NEKO_ASR_ADMISSION", "1")
    monkeypatch.setenv("NEKO_ASR_SHORT_SPEECH", "shadow")
    release = asyncio.Event()
    first = _RuntimeStartCandidate(connect_gate=release)
    _patch_runtime_start(monkeypatch, [first, _RuntimeStartCandidate()])
    runtime = IndependentAsrRuntime(_runtime_callbacks())
    task = asyncio.create_task(
        runtime.start(route_key="qwen", resource_optimization_enabled=True)
    )
    try:
        await asyncio.wait_for(first.connect_started.wait(), 5)
        monkeypatch.setenv("NEKO_ASR_SHORT_SPEECH", "enforce")
        release.set()
        assert (await task).status is AsrStartStatus.READY
        gate = runtime._asr_detector._gate
        assert not gate._admission_config.experimental_short_speech
        assert gate._admission_shadow_config.experimental_short_speech
        await runtime.close()
        assert (
            await runtime.start(route_key="qwen", resource_optimization_enabled=True)
        ).status is AsrStartStatus.READY
        gate = runtime._asr_detector._gate
        assert gate._admission_config.experimental_short_speech
        assert gate._admission_shadow_config is None
    finally:
        release.set()
        await task
        await runtime.close()
