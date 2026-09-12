"""Source overflow owns one failure notice without reaching a successor."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_turn.contracts import PreserveUnsentPrefix
from tests.unit.test_core_independent_asr import _Runtime, _install_ready_lifecycle

pytestmark = pytest.mark.asyncio


def _source(*, write_attempted=False):
    manager = _Runtime()
    _install_ready_lifecycle(manager, "qwen")
    receiver = manager._asr_runtime
    receiver._asr_session = SimpleNamespace(
        is_ready=True, transport_write_attempted=write_attempted, close=AsyncMock(),
    )
    generation = object()
    manager._voice_session_activation_runtime = object()
    prefix = PreserveUnsentPrefix(
        ingress=manager._capture_ingress_token(), batch_id="test-batch", start_sequence=1,
    )
    receiver._asr_protected_prefix = prefix
    receiver._asr_lifecycle.protect_unsent_prefix()
    manager._voice_activation_delivery_prefix = (generation, 1, prefix)
    return manager, receiver, generation


async def _settle(manager):
    while manager._core_asr_cleanup_tasks:
        await asyncio.gather(*tuple(manager._core_asr_cleanup_tasks))


@pytest.mark.parametrize("attempted,code", [
    (False, "ASR_INPUT_DELIVERY_FAILED"),
    (True, "ASR_INPUT_DELIVERY_UNCERTAIN"),
])
async def test_source_overflow_emits_exactly_one_notice_after_own_abort(attempted, code):
    manager, receiver, generation = _source(write_attempted=attempted)
    audio_generation = receiver._asr_audio_generation
    manager._revoke_voice_activation_prefix(generation, "output_queue_overflow")
    manager._revoke_voice_activation_prefix(generation, "output_queue_overflow")
    await _settle(manager)
    statuses = [json.loads(call.args[0]) for call in manager.send_status.await_args_list]
    assert sum(status.get("code") == code for status in statuses) == 1
    assert receiver._asr_audio_generation == audio_generation + 1
    assert receiver._asr_session is None


async def test_old_abort_cleanup_cannot_notify_or_close_successor():
    manager, receiver, generation = _source()
    close_started = asyncio.Event()
    release = asyncio.Event()

    async def close_old():
        close_started.set()
        await release.wait()

    receiver._asr_session.close.side_effect = close_old
    manager._revoke_voice_activation_prefix(generation, "output_queue_overflow")
    await close_started.wait()
    successor = SimpleNamespace(is_ready=True, close=AsyncMock())
    receiver._asr_session = successor
    manager._voice_lease_generation += 1
    manager._voice_session_activation_runtime = object()
    release.set()
    await _settle(manager)
    statuses = [json.loads(call.args[0]) for call in manager.send_status.await_args_list]
    assert not any(status.get("code", "").startswith("ASR_INPUT_DELIVERY")
                   for status in statuses)
    assert receiver._asr_session is successor
    successor.close.assert_not_awaited()


@pytest.mark.parametrize("revoke_kind", ["permission", "microphone", "microphone_without_factory"])
async def test_permission_revoke_then_new_authority_reopens_only_for_new_phrase(revoke_kind):
    from tests.unit.test_voice_activation_cold_prefix import _cold_harness, _feed, _until
    from tests.unit.test_voice_activation_handoff import _Factory
    from main_logic.asr_client import VoiceIdentityActivationResult

    async with _cold_harness() as h:
        for marker in range(1, 16):
            await _feed(h, marker)
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        if revoke_kind == "permission":
            h.manager.require_voice_session_activation(activation_generation="revoked")
        else:
            await h.manager.set_voice_input_suppressed("microphone_stopped", suppressed=True)
            await h.manager.set_voice_input_suppressed("microphone_stopped", suppressed=False)
        assert h.lifecycle.pending_connect_bytes == 0
        h.release.set()
        if revoke_kind != "microphone_without_factory":
            replacement = _Factory(h.clock)
            result = await h.manager.set_voice_session_activation_factory(
                replacement, activation_generation="profile",
            )
            assert result is VoiceIdentityActivationResult.READY
        expected = []
        for marker in range(100, 115):
            expected.append(await _feed(h, marker))
        await _until(lambda: sum(map(len, h.deliveries)) == 48000)
        assert b"".join(h.deliveries) == b"".join(expected)
        statuses = [json.loads(call.args[0]) for call in h.manager.send_status.await_args_list]
        assert not any(status.get("code", "").startswith("ASR_INPUT_DELIVERY")
                       for status in statuses)


async def test_setter_does_not_ignore_external_audio_generation_after_cleanup():
    from tests.unit.test_voice_activation_cold_prefix import _cold_harness, _feed, _until
    from tests.unit.test_voice_activation_handoff import _Factory
    from main_logic.asr_client import VoiceIdentityActivationResult

    async with _cold_harness() as h:
        for marker in range(1, 16):
            await _feed(h, marker)
        await _until(lambda: h.lifecycle.pending_connect_bytes == 48000)
        h.manager.require_voice_session_activation(activation_generation="revoked")
        await _settle(h.manager)
        receiver = h.manager._asr_runtime
        completion = asyncio.get_running_loop().create_future()
        h.manager._voice_activation_prefix_cleanup = (receiver, completion)
        replacement = _Factory(h.clock)
        setter = asyncio.create_task(h.manager.set_voice_session_activation_factory(
            replacement, activation_generation="profile",
        ))
        await asyncio.sleep(0)
        completion.set_result(receiver._capture_runtime_identity())
        # Another authoritative abort runs after cleanup's receipt was produced.
        receiver._asr_audio_generation += 1
        receiver._asr_start_generation += 1
        assert await setter is VoiceIdentityActivationResult.RUNTIME_DEGRADED
        assert h.manager._voice_session_activation_degraded
