import asyncio
import time
from types import MethodType
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client.transcript import TranscriptEnvelope
from main_logic.core.turn import TurnMixin
from main_logic.voice_input import VoiceInputDispatchResult
from tests.unit.test_audio_stream_queue import (
    _make_transcript_dispatch_manager,
    _transcript_event,
)
from tests.unit.test_voice_input_registry import _consumer, _register_chat, _turn
from main_logic.voice_input import VoiceInputRegistry
from main_logic.voice_turn.contracts import VoiceTranscriptEvent

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]


@pytest.mark.parametrize("result,expected", [
    (None, VoiceInputDispatchResult.DELIVERED),
    (True, VoiceInputDispatchResult.DELIVERED),
    (False, VoiceInputDispatchResult.REJECTED),
])
async def test_registry_explicit_rejection_preserves_legacy_none(result, expected):
    registry = VoiceInputRegistry()
    consumer = _consumer()
    consumer.on_final = AsyncMock(return_value=result)
    registration = _register_chat(registry, consumer)
    registry.activate(registration.handle)
    token = _turn()
    assert registry.begin_utterance(token)
    assert await registry.prepare_utterance(token)
    event = VoiceTranscriptEvent(turn_token=token, provider="qwen", text="hello")
    assert await registry.dispatch_final(event) is expected
    assert await registry.dispatch_final(event) is VoiceInputDispatchResult.REJECTED
    consumer.on_final.assert_awaited_once()


async def prepared_manager(text):
    manager = _make_transcript_dispatch_manager()
    event = _transcript_event(manager, text)
    assert manager._voice_input_registry.begin_utterance(event.turn_token)
    assert await manager._voice_input_registry.prepare_utterance(event.turn_token)
    return manager, event


async def test_core_final_acceptance_waits_for_actual_submit():
    manager, event = await prepared_manager("hello")
    submitted = asyncio.Event()
    release = asyncio.Event()

    async def submit(*_args, **_kwargs):
        submitted.set()
        await release.wait()

    manager.session.submit_external_voice_turn.side_effect = submit
    task = asyncio.create_task(manager._dispatch_voice_input_final(event))
    await asyncio.wait_for(submitted.wait(), 1)
    assert not task.done()
    release.set()
    assert await task is True


async def test_real_echo_rejection_returns_false_through_registry(monkeypatch):
    import main_logic.core as core_facade
    monkeypatch.setattr(core_facade, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    text = "今天阳光很好，我们一起去公园散步，看看花草树木吧。"
    manager, event = await prepared_manager(text)
    manager.handle_input_transcript = MethodType(TurnMixin.handle_input_transcript, manager)
    manager._takeover_input_dispatcher = None
    manager._recent_ai_voice_echo_text = text
    manager._recent_ai_voice_echo_at = time.time()
    assert manager._should_suppress_dirty_voice_transcript(text)
    assert await manager._dispatch_voice_input_final(event) is False
    manager.session.submit_external_voice_turn.assert_not_awaited()


async def test_real_echo_rejection_does_not_replenish_runtime_recovery_budget(monkeypatch):
    import main_logic.core as core_facade
    monkeypatch.setattr(core_facade, "HIDE_DIRTY_VOICE_TRANSCRIPTS", True)
    text = "今天阳光很好，我们一起去公园散步，看看花草树木吧。"
    manager, event = await prepared_manager(text)
    manager.handle_input_transcript = MethodType(TurnMixin.handle_input_transcript, manager)
    manager._takeover_input_dispatcher = None
    manager._recent_ai_voice_echo_text = text
    manager._recent_ai_voice_echo_at = time.time()
    runtime = manager._asr_runtime
    runtime._asr_recovery_budget.begin(time.monotonic())
    assert runtime._asr_recovery_budget.claim_attempt(time.monotonic())
    await runtime._dispatch_asr_transcript_envelope(TranscriptEnvelope(
        turn_token=event.turn_token, provider="qwen", text=text,
    ))
    assert runtime._asr_recovery_budget.attempts_used == 1
    manager.session.submit_external_voice_turn.assert_not_awaited()


@pytest.mark.parametrize("recovering", [False, True])
async def test_submitted_final_replenishes_budget_only_outside_fault_operation(recovering):
    manager, event = await prepared_manager("hello")
    runtime = manager._asr_runtime
    runtime._asr_recovery_budget.begin(time.monotonic())
    assert runtime._asr_recovery_budget.claim_attempt(time.monotonic())
    if recovering:
        runtime._asr_recovery = object()
    await runtime._dispatch_asr_transcript_envelope(TranscriptEnvelope(
        turn_token=event.turn_token, provider="qwen", text=event.text,
    ))
    manager.session.submit_external_voice_turn.assert_awaited_once()
    assert runtime._asr_recovery_budget.attempts_used == (1 if recovering else 0)
    runtime._asr_recovery = None
