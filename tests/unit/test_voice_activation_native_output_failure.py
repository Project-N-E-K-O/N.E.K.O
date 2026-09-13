"""Local output failure retires native input without replay or successor cleanup."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input.activation import ActivationState
from tests.unit.test_voice_activation_handoff import _harness, _until


pytestmark = pytest.mark.asyncio


async def test_native_partial_output_overflow_blocks_bypass_until_connection_replaced():
    async with _harness("native") as h:
        source = h.manager.session
        close_entered, close_release = asyncio.Event(), asyncio.Event()
        send_release = asyncio.Event()

        async def close():
            close_entered.set()
            await close_release.wait()

        source.close = AsyncMock(side_effect=close)
        h.manager.handle_connection_error = AsyncMock()
        sends = 0

        async def send(pcm):
            nonlocal sends
            sends += 1
            if sends > 1:
                await send_release.wait()
            h.deliveries.append(("source", pcm))

        source.stream_audio.side_effect = send
        try:
            for index in range(83):
                h.clock.value += 0.1
                await h.feed(3000 + index)
            await _until(close_entered.is_set)
            assert h.activation.state is ActivationState.UNAVAILABLE
            assert len(h.pcm) == 16  # initial activation plus one new prefix frame
            assert h.manager.session_closed_by_server
            assert h.manager._native_activation_idle_reconnect_identity is None
            await h.manager.set_voice_session_activation_factory(
                None, activation_generation="explicitly-disabled"
            )
            await h.feed(4000)
            assert sends == 2  # even bypass cannot append to the damaged source
            assert source.close.await_count == 1
            close_release.set()
            await _until(lambda: h.manager.handle_connection_error.await_count == 1)
            assert h.manager.handle_connection_error.await_args.kwargs["expected_session"] is source
        finally:
            close_release.set()
            send_release.set()


async def test_native_unknown_write_retires_connection_once_without_replay():
    async with _harness("native") as h:
        source = h.manager.session
        source.close = AsyncMock()
        h.manager.handle_connection_error = AsyncMock()
        source.stream_audio.reset_mock()
        source.stream_audio.side_effect = RuntimeError("send outcome unknown")
        await h.feed(3000)
        await _until(lambda: source.close.await_count == 1)
        await _until(lambda: h.manager.handle_connection_error.await_count == 1)
        assert h.activation.state is ActivationState.UNAVAILABLE
        assert h.manager.session_closed_by_server
        await h.feed(3001)
        assert source.stream_audio.await_count == 1
        assert source.close.await_count == 1


@pytest.mark.parametrize("same_object", [False, True])
async def test_late_native_close_does_not_recover_or_block_successor(same_object):
    async with _harness("native") as h:
        source = h.manager.session
        source._connection_generation = 1
        entered, release = asyncio.Event(), asyncio.Event()

        async def close():
            entered.set()
            await release.wait()

        source.close = AsyncMock(side_effect=close)
        h.manager.handle_connection_error = AsyncMock()
        source.stream_audio.side_effect = RuntimeError("unknown")
        try:
            await h.feed(3000)
            await _until(entered.is_set)
            if same_object:
                source._connection_generation = 2
                source.stream_audio.side_effect = source._stream
            else:
                h.manager.session = h.session("successor")
            h.manager.session_closed_by_server = False
            release.set()
            await asyncio.wait_for(h.manager._voice_activation_native_retirement[2], 0.5)
            h.manager.handle_connection_error.assert_not_awaited()
            assert not h.manager.session_closed_by_server
            await h.manager.set_voice_session_activation_factory(None, activation_generation="disabled")
            before = len(h.pcm)
            await h.feed(4000)
            assert len(h.pcm) == before + 1
        finally:
            release.set()


async def test_native_retirement_is_bounded_when_close_ignores_cancellation():
    async with _harness("native") as h:
        source = h.manager.session
        entered, release = asyncio.Event(), asyncio.Event()

        async def close():
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    pass

        source.close = AsyncMock(side_effect=close)
        h.manager.handle_connection_error = AsyncMock()
        source.stream_audio.side_effect = RuntimeError("unknown")
        try:
            await h.feed(3000)
            await _until(entered.is_set)
            retirement = h.manager._voice_activation_native_retirement[2]
            await asyncio.wait_for(asyncio.shield(retirement), 1.5)
            await _until(lambda: h.manager.handle_connection_error.await_count == 1)
            assert h.manager.session_closed_by_server
            assert any(not task.done() for task in h.manager._core_asr_cleanup_tasks)
        finally:
            release.set()


async def test_native_close_queued_before_reconnect_cannot_close_new_generation():
    async with _harness("native") as h:
        source = h.manager.session
        source._connection_generation = 1
        source.close = AsyncMock()
        h.manager.handle_connection_error = AsyncMock()
        retirement = h.manager._retire_native_voice_activation_session(
            source, connection_generation=1
        )
        # Run the retirement until it queues close; take over before close runs.
        await asyncio.sleep(0)
        source._connection_generation = 2
        h.manager.session_closed_by_server = False
        await retirement
        source.close.assert_not_awaited()
        h.manager.handle_connection_error.assert_not_awaited()
        assert not h.manager.session_closed_by_server


async def test_verifier_unavailable_before_output_does_not_close_native_connection():
    async with _harness("native", active=False) as h:
        source = h.manager.session
        source.close = AsyncMock()
        h.manager._on_voice_session_activation_status(
            h.activation.generation,
            h.activation._controller.mark_unavailable(h.activation.generation, "model_unavailable"),
        )
        await asyncio.sleep(0)
        source.close.assert_not_awaited()
