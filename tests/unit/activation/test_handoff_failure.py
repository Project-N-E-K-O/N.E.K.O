from __future__ import annotations
import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus

from tests.support.activation_handoff_fakes import (
    _until,
)

from tests.unit.activation._scenarios import (
    _harness,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_unknown_inflight_delivery_rejects_handoff_without_resending(route):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.pcm.copy()

        async def ambiguous_send(_value, **_kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError("injected uncertain write")

        transport = (
            h.manager.session.stream_audio
            if route == "native"
            else h.manager._asr_runtime.submit
        )
        transport.side_effect = ambiguous_send
        await h.feed(3_000)
        await entered.wait()
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            await h.feed(3_001)
            release.set()
            assert await asyncio.wait_for(begin, timeout=2.0) is False
            assert h.activation.state is ActivationState.UNAVAILABLE
            await h.feed(3_002)
            assert h.pcm == original
            target.stream_audio.assert_not_awaited()
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("capability", [False, None])
async def test_native_unsafe_or_missing_input_boundary_preserves_usable_source(
    capability,
):
    async with _harness("native") as h:
        source = h.manager.session
        source.can_handoff_voice_input = (
            MagicMock(return_value=False) if capability is False else None
        )
        assert (
            await h.manager._begin_voice_activation_handoff(h.session("target"))
            is False
        )
        assert h.manager.session is source
        assert h.activation.state is ActivationState.ACTIVE
        assert not h.activation.output_paused
        short = await h.feed(3_000)
        await _until(lambda: h.pcm[-1] == short)
        assert h.deliveries[-1] == ("source", short)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_irreversible_failure_does_not_restore_source_or_replay_audio(route):
    async with _harness(route) as h:
        original = h.pcm.copy()
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        await h.feed(3_000)
        assert h.manager._mark_voice_activation_handoff_irreversible(ticket)
        await h.manager._abort_voice_activation_handoff(
            ticket, reason="new_session_failed"
        )
        assert h.activation.state is ActivationState.UNAVAILABLE
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        await h.feed(3_001)
        assert h.pcm == original


@pytest.mark.parametrize("route", ["native", "independent"])
@pytest.mark.parametrize("outcome", ["unknown", "cancel"])
async def test_uncertain_remote_write_is_retired_before_disabled_audio_can_reuse_source(
    route, outcome
):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        remote_buffer: list[bytes] = []
        remote_closed = False
        source = h.manager.session
        receiver = h.manager._asr_runtime

        async def close_remote(*_args, **_kwargs):
            nonlocal remote_closed
            remote_closed = True

        source.close = AsyncMock(side_effect=close_remote)
        receiver.abort = AsyncMock(side_effect=close_remote)

        async def uncertain_write(value, **_kwargs):
            # An in-flight write can already own bytes at the receiver even
            # while its local coroutine has not yet learned the outcome.
            assert not remote_closed, "ordinary PCM reused a retired receiver"
            entered.set()
            await release.wait()
            pcm = value if route == "native" else value.pcm16
            remote_buffer.append(pcm)
            if outcome == "unknown":
                raise RuntimeError(
                    "write reached receiver but acknowledgement was lost"
                )
            return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

        transport = source.stream_audio if route == "native" else receiver.submit
        transport.side_effect = uncertain_write
        pending_pcm = await h.feed(3_000)
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            if outcome == "cancel":
                begin.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await begin
                release.set()
            else:
                release.set()
                assert await asyncio.wait_for(begin, timeout=2.0) is False
            await _until(lambda: not h.activation.output_inflight)
            assert remote_buffer == [pending_pcm]
            assert h.activation.state is ActivationState.UNAVAILABLE
            await h.manager.set_voice_session_activation_factory(
                None, activation_generation="user-disabled-protection"
            )
            await h.feed(3_001)
            assert remote_closed
            assert remote_buffer == [pending_pcm]
            assert transport.await_count == 16
            target.stream_audio.assert_not_awaited()
            if route == "native":
                source.close.assert_awaited_once()
                assert h.manager.session_closed_by_server
            else:
                receiver.abort.assert_awaited_once()
                assert h.manager._asr_route_mode == "blocked"
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)
