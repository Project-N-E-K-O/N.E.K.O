"""Capture timing remains local while the Core transport is replaced."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.audio_input import ProcessedVoiceFrame
from tests.unit.test_voice_activation_handoff import _harness, _until

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_unavailable_authority_pcm_cannot_wait_for_later_reopening(
    route,
):
    async with _harness(route, active=False) as h:
        # Hold the consumer so enqueue-time authority and later processing
        # authority differ. Routing and activation remain production methods.
        h.manager._ensure_audio_stream_worker = lambda: None
        h.manager.require_voice_session_activation(activation_generation="revoked")
        await h.manager._enqueue_audio_stream_data({"data": [2800] * 160})
        await h.manager.set_voice_session_activation_factory(
            h.factory, activation_generation="profile",
        )
        h.manager._voice_input_audio_pipeline.process = AsyncMock(
            return_value=ProcessedVoiceFrame(b"\x01\x00" * 160, 16000, .9, True)
        )

        async def drain():
            while not h.manager._audio_stream_queue.empty():
                frame = h.manager._audio_stream_queue.get_nowait()
                try:
                    await h.manager._process_microphone_stream_data(
                        frame.message, ingress_token=frame.token,
                        audio_stream_epoch=frame.audio_stream_epoch,
                        ingress_sequence=frame.ingress_sequence,
                        received_at=frame.received_at, captured_at=frame.captured_at,
                    )
                finally:
                    h.manager._audio_stream_queue.task_done()
                    h.manager._complete_hot_swap_ingress_sequence(frame.ingress_sequence)

        await drain()
        h.manager._voice_input_audio_pipeline.process.assert_not_awaited()
        assert len(h.factory.runtimes) == 1
        assert not h.manager._voice_activation_pending_capture
        assert not h.pcm
        await h.manager._enqueue_audio_stream_data({"data": [2801] * 160})
        await drain()
        h.manager._voice_input_audio_pipeline.process.assert_awaited_once()
        assert len(h.factory.runtimes) == 2
        assert h.manager._voice_session_activation_sequence == 1


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_dsp_await_survives_a_fully_committed_authorized_handoff(route):
    async with _harness(route) as h:
        h.manager.is_active = True
        entered, release = asyncio.Event(), asyncio.Event()
        pcm = (2700).to_bytes(2, "little", signed=True) * 160

        async def process(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return ProcessedVoiceFrame(pcm, 16000, 0.9, True)

        h.manager._voice_input_audio_pipeline.process = AsyncMock(side_effect=process)
        process_task = asyncio.create_task(
            h.manager._process_microphone_stream_data(
                {"data": [2700] * 160, "sample_rate_hz": 16000},
                ingress_token=h.manager._capture_ingress_token(),
                received_at=h.clock.value,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            target = h.session("target")
            ticket = await h.manager._begin_voice_activation_handoff(target)
            assert ticket is not None and ticket is not False
            await h.promote(ticket, target)
            assert h.manager._voice_activation_handoff is None
            release.set()
            await process_task
            await _until(lambda: h.pcm.count(pcm) == 1)
            assert h.activation.state is ActivationState.ACTIVE
            assert not h.manager.hot_swap_audio_cache
            assert not h.manager._voice_activation_pending_capture
            assert len(h.factory.runtimes) == 1
        finally:
            release.set()
            await asyncio.gather(process_task, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_idle_tick_cannot_overtake_predeadline_input_waiting_in_dsp(route):
    async with _harness(route) as h:
        h.manager.is_active = True
        deadline = h.activation.idle_deadline
        assert deadline is not None
        entered, release = asyncio.Event(), asyncio.Event()
        pcm = (2701).to_bytes(2, "little", signed=True) * 160

        async def process(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return ProcessedVoiceFrame(pcm, 16000, 0.9, True)

        h.manager._voice_input_audio_pipeline.process = AsyncMock(side_effect=process)
        process_task = asyncio.create_task(
            h.manager._process_microphone_stream_data(
                {"data": [2701] * 160, "sample_rate_hz": 16000},
                ingress_token=h.manager._capture_ingress_token(),
                received_at=deadline - 0.1,
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 2)
            h.clock.value = deadline + 1
            assert (await h.activation.tick()).state is ActivationState.ACTIVE
            ticket = await h.manager._begin_voice_activation_handoff(
                h.session("target")
            )
            assert ticket is not None and ticket is not False
            await h.promote(ticket, ticket.target_session)
            release.set()
            await process_task
            await _until(lambda: h.pcm.count(pcm) == 1)
            assert (await h.activation.tick()).state is ActivationState.ACTIVE
            assert h.activation.last_voice_at == pytest.approx(deadline - 0.09)
        finally:
            release.set()
            await asyncio.gather(process_task, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_predeadline_input_registered_in_raw_queue_defers_expiry(route):
    async with _harness(route) as h:
        deadline = h.activation.idle_deadline
        assert deadline is not None
        h.manager._ensure_audio_stream_worker = lambda: None
        await h.manager._enqueue_audio_stream_data({"data": [2702] * 160})
        queued = h.manager._audio_stream_queue.get_nowait()
        try:
            # Freeze the capture clock of this already registered input. The
            # production tick reads the same map before DSP has started.
            h.manager._voice_activation_pending_capture[queued.ingress_sequence] = (
                deadline - 0.1
            )
            h.clock.value = deadline + 1
            assert (await h.activation.tick()).state is ActivationState.ACTIVE
            h.manager._complete_hot_swap_ingress_sequence(queued.ingress_sequence)
            assert (await h.activation.tick()).state is ActivationState.WAITING
        finally:
            h.manager._audio_stream_queue.task_done()
            h.manager._complete_hot_swap_ingress_sequence(queued.ingress_sequence)
