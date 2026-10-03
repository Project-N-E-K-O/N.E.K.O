"""Exercise real candidate accumulation; diagnostics must not alter admission."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_identity_service import activation_runtime as module
from main_logic.voice_identity_service.activation_scoring import ActivationScoreStatus
from main_logic.voice_input.activation import (
    ActivationGeneration, AudioFrame, VoiceActivationController,
)


def frame(sequence, generation, captured_at=None):
    return AudioFrame(
        sequence, sequence * 1600, (sequence + 1) * 1600,
        sequence * 0.1 if captured_at is None else captured_at,
        16000, b"\x12\x34" * 1600, generation, {"private": "not-for-logs"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("gap_kind", ["speech_gap", "capture_gap"])
async def test_reports_candidate_reset_without_logging_context(monkeypatch, caplog, gap_kind):
    clock = SimpleNamespace(value=10.0)
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.value))
    generation = ActivationGeneration("private-session", 1, 2, 3, 4, "core_chat")
    scorer = SimpleNamespace(
        prepare=AsyncMock(return_value=ActivationScoreStatus.READY), close=AsyncMock(),
    )
    runtime = module.VoiceSessionActivationRuntime(
        generation, scorer, AsyncMock(),
        controller=VoiceActivationController(clock=lambda: 0.0),
    )
    caplog.set_level(logging.INFO, logger=module.__name__)
    await runtime.prepare()
    try:
        for sequence in range(3):
            await runtime.feed(frame(sequence, generation), voice_activity=True)
        if gap_kind == "speech_gap":
            for sequence in range(3, 9):
                await runtime.feed(frame(sequence, generation), voice_activity=False)
            expected_ms = 0
            next_sequence = 9
        else:
            await runtime.feed(frame(3, generation, captured_at=1.0), voice_activity=True)
            expected_ms = 100
            next_sequence = 4
        assert len(caplog.records) == 1
        clock.value += 2.1
        await runtime.feed(frame(next_sequence, generation, captured_at=1.1), voice_activity=False)
        stats = caplog.records[-1].args[-1]
        assert stats["reset_" + gap_kind] == 1
        assert stats["cleared_voice_samples"] == 4800
        assert stats["candidate_voice_ms"] == expected_ms
        assert stats["verification_requests"] == 0
        assert runtime._candidate_voice_samples == expected_ms * 16
        assert "not-for-logs" not in caplog.text
        assert "private-session" not in caplog.text
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_checkpoint_is_logged_immediately_without_changing_request(monkeypatch, caplog):
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: 10.0))
    generation = ActivationGeneration("session", 1, 1, 1, 1, "core_chat")
    started = asyncio.Event()

    async def score(*args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    scorer = SimpleNamespace(
        prepare=AsyncMock(return_value=ActivationScoreStatus.READY),
        close=AsyncMock(), score=score, profile_generation="profile", scorer_generation=1,
    )
    runtime = module.VoiceSessionActivationRuntime(
        generation, scorer, AsyncMock(),
        controller=VoiceActivationController(clock=lambda: 0.0),
    )
    caplog.set_level(logging.INFO, logger=module.__name__)
    await runtime.prepare()
    try:
        for sequence in range(15):
            await runtime.feed(frame(sequence, generation), voice_activity=True)
        await asyncio.wait_for(started.wait(), 1)
        assert len(caplog.records) == 2
        stats = caplog.records[-1].args[-1]
        assert stats["candidate_voice_ms"] == 1500
        assert stats["verification_requests"] == 1
        assert runtime.verification_inflight
    finally:
        await runtime.close()
