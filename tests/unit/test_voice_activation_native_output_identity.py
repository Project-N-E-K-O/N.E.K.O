"""A native output result owns only the connection that attempted its write."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from main_logic.voice_input.activation import ActivationState
from tests.unit.test_voice_activation_handoff import _harness, _until


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("outcome", ["written", "unknown", "attribute", "closed_ok", "closed_error", "cancelled"])
async def test_inflight_native_result_cannot_retire_same_object_successor(outcome):
    async with _harness("native") as h:
        source = h.manager.session
        source._connection_generation = 1
        source.close = AsyncMock()
        h.manager.handle_connection_error = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()
        original_pcm = h.pcm.copy()

        async def held_send(pcm):
            entered.set()
            await release.wait()
            if outcome == "unknown":
                raise RuntimeError("send outcome unknown")
            if outcome == "attribute":
                raise AttributeError("old transport disappeared")
            if outcome == "closed_ok":
                raise ConnectionClosedOK(None, None)
            if outcome == "closed_error":
                raise ConnectionClosedError(None, None)
            await source._stream(pcm)

        source.stream_audio.side_effect = held_send
        try:
            await h.feed(3000)
            await _until(entered.is_set)
            writer = h.activation._output_task
            source._connection_generation = 2
            h.manager.session_closed_by_server = False
            idle_marker = (h.activation.generation, 2)
            h.manager._native_activation_idle_reconnect_identity = idle_marker
            if outcome == "cancelled":
                writer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await writer
            else:
                release.set()
                await writer
            cleanup = tuple(h.manager._core_asr_cleanup_tasks)
            if cleanup:
                await asyncio.gather(*cleanup)
            assert h.activation.state is ActivationState.UNAVAILABLE
            source.close.assert_not_awaited()
            h.manager.handle_connection_error.assert_not_awaited()
            assert not h.manager.session_closed_by_server
            assert h.manager._native_activation_idle_reconnect_identity == idle_marker
            assert h.manager._voice_activation_native_retirement is None
            assert len(h.pcm) == len(original_pcm) + (outcome == "written")
            await h.manager.set_voice_session_activation_factory(
                None, activation_generation="explicitly-disabled"
            )
            source.stream_audio.side_effect = source._stream
            before = len(h.pcm)
            await h.feed(4000)
            assert len(h.pcm) == before + 1
        finally:
            release.set()


async def test_queued_native_close_rechecks_session_object_before_running():
    async with _harness("native") as h:
        source = h.manager.session
        source._connection_generation = 1
        source.close = AsyncMock()
        h.manager.handle_connection_error = AsyncMock()
        source.stream_audio.side_effect = RuntimeError("send outcome unknown")
        await h.feed(3000)
        retirement = h.manager._voice_activation_native_retirement[2]
        await asyncio.sleep(0)
        h.manager.session = h.session("successor")
        h.manager.session_closed_by_server = False
        await retirement
        source.close.assert_not_awaited()
        h.manager.handle_connection_error.assert_not_awaited()
        assert not h.manager.session_closed_by_server


async def test_output_failure_without_native_session_does_not_schedule_recovery():
    async with _harness("native", active=False) as h:
        h.manager.session = None
        h.manager.handle_connection_error = AsyncMock()
        await h.feed(3000)
        runtime = h.manager._voice_session_activation_runtime
        h.manager._on_voice_session_activation_status(
            runtime.generation,
            runtime._controller.mark_unavailable(runtime.generation, "output_dropped"),
        )
        cleanup = tuple(h.manager._core_asr_cleanup_tasks)
        if cleanup:
            await asyncio.gather(*cleanup)
        assert h.manager._voice_activation_native_retirement is None
        assert not getattr(h.manager, "session_closed_by_server", False)
        h.manager.handle_connection_error.assert_not_awaited()


async def test_output_overflow_after_reconnect_does_not_retire_unsent_successor():
    async with _harness("native") as h:
        source = h.manager.session
        source._connection_generation = 1
        source.close = AsyncMock()
        h.manager.handle_connection_error = AsyncMock()
        entered, release = asyncio.Event(), asyncio.Event()

        async def held_send(_pcm):
            entered.set()
            await release.wait()
            raise RuntimeError("old write failed")

        source.stream_audio.side_effect = held_send
        try:
            await h.feed(3000)
            await _until(entered.is_set)
            source._connection_generation = 2
            h.manager.session_closed_by_server = False
            for index in range(82):
                h.clock.value += 0.1
                await h.feed(3100 + index)
            assert h.activation.state is ActivationState.UNAVAILABLE
            release.set()
            writer = h.activation._output_task
            if writer is not None:
                await writer
            cleanup = tuple(h.manager._core_asr_cleanup_tasks)
            if cleanup:
                await asyncio.gather(*cleanup)
            source.close.assert_not_awaited()
            h.manager.handle_connection_error.assert_not_awaited()
            assert not h.manager.session_closed_by_server
        finally:
            release.set()
