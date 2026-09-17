from __future__ import annotations
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus
from main_logic.asr_client.endpointing.detector_runtime import SmartTurnLease
from main_logic.voice_turn.contracts import VoiceTurnToken

from tests.support.activation_handoff_fakes import (
    _until,
)

from tests.unit.activation._scenarios import (
    _harness,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_revocation_while_settling_cannot_restore_old_authority(route):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.pcm.copy()

        async def held_send(_value, **_kwargs):
            entered.set()
            await release.wait()
            return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

        transport = (
            h.manager.session.stream_audio
            if route == "native"
            else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_send
        await h.feed(3_000)
        await entered.wait()
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            h.manager.require_voice_session_activation(activation_generation="revoked")
            release.set()
            assert await asyncio.wait_for(begin, timeout=2.0) is False
            await h.feed(3_001)
            assert h.manager._voice_session_activation_runtime is None
            assert h.manager._voice_session_activation_degraded
            assert h.pcm == original
            target.stream_audio.assert_not_awaited()
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_abort_before_old_close_restores_source_once_and_stale_ticket_is_harmless(
    route,
):
    async with _harness(route) as h:
        original = h.pcm.copy()
        source = h.manager.session
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        queued = await h.feed(3_000)
        await h.manager._abort_voice_activation_handoff(ticket, reason="prepare_failed")
        await _until(lambda: h.pcm == original + [queued])
        assert h.manager.session is source
        assert h.activation.state is ActivationState.ACTIVE
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        next_ticket = await h.manager._begin_voice_activation_handoff(target)
        assert next_ticket is not None and next_ticket is not False
        await h.manager._abort_voice_activation_handoff(
            ticket, reason="late_old_cleanup"
        )
        assert h.manager._voice_activation_handoff_is_current(next_ticket)
        assert h.activation.output_paused
        await h.promote(next_ticket, target)
        assert h.pcm.count(queued) == 1


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_cancelled_handoff_with_unknown_inflight_write_cannot_resume_old_grant(
    route,
):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        original = h.pcm.copy()

        async def held_send(_value, **_kwargs):
            entered.set()
            await release.wait()
            return AsrSubmitResult(AsrSubmitStatus.ACCEPTED)

        transport = (
            h.manager.session.stream_audio
            if route == "native"
            else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_send
        await h.feed(3_000)
        await entered.wait()
        begin = asyncio.create_task(
            h.manager._begin_voice_activation_handoff(h.session("target"))
        )
        try:
            await _until(lambda: h.activation.output_paused)
            await h.feed(3_001)
            begin.cancel()
            with pytest.raises(asyncio.CancelledError):
                await begin
            assert h.activation.state is ActivationState.UNAVAILABLE
            assert h.manager._voice_activation_handoff is None
            release.set()
            await _until(lambda: not h.activation.output_inflight)
            await h.feed(3_002)
            assert h.activation.state is ActivationState.UNAVAILABLE
            assert h.pcm == original
            assert transport.await_count == 16
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_revocation_while_commit_awaits_writer_lock_is_not_reported_as_success(
    route,
):
    async with _harness(route) as h:
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        h.manager.session = target
        resume = AsyncMock(wraps=h.activation.resume_output)
        h.activation.resume_output = resume
        await h.activation._lock.acquire()
        commit = asyncio.create_task(h.manager._commit_voice_activation_handoff(ticket))
        try:
            await _until(lambda: resume.await_count == 1)
            assert not commit.done()
            h.manager.require_voice_session_activation(activation_generation="revoked")
            h.activation._lock.release()
            assert await asyncio.wait_for(commit, timeout=2.0) is False
            assert h.manager._voice_session_activation_runtime is None
            assert h.manager._voice_session_activation_degraded
            target.stream_audio.assert_not_awaited()
        finally:
            if h.activation._lock.locked():
                h.activation._lock.release()
            if not commit.done():
                commit.cancel()
            await asyncio.gather(commit, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_deadline_expiring_inside_resume_revokes_claims_before_queued_audio_leaves(
    route,
):
    async with _harness(route) as h:
        target = h.session("target")
        original = h.pcm.copy()
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        await h.feed(3_000)
        resume = h.activation.resume_output

        async def expire_after_resume(owner):
            resumed = await resume(owner)
            ticket.deadline = asyncio.get_running_loop().time() - 0.1
            return resumed

        h.activation.resume_output = expire_after_resume
        h.manager.session = target
        assert not await h.manager._commit_voice_activation_handoff(ticket)
        await _until(lambda: not h.activation.output_inflight)
        assert h.activation.state is ActivationState.UNAVAILABLE
        assert h.pcm == original
        assert h.manager._voice_session_activation_degraded
        target.stream_audio.assert_not_awaited()


@pytest.mark.parametrize("resist_first_cancel", [False, True])
async def test_independent_handoff_abort_detaches_before_bounded_stuck_provider_close(
    resist_first_cancel,
):
    async with _harness("independent") as h:
        entered, release_write = asyncio.Event(), asyncio.Event()
        closing, release_close = asyncio.Event(), asyncio.Event()
        close_finished = asyncio.Event()
        receiver = h.manager._asr_runtime
        remote = h.session("asr-remote")

        async def stuck_close():
            closing.set()
            try:
                await release_close.wait()
            except asyncio.CancelledError:
                if resist_first_cancel:
                    await release_close.wait()
                raise
            finally:
                close_finished.set()

        async def uncertain_write(_value, **_kwargs):
            entered.set()
            await release_write.wait()
            raise RuntimeError("remote outcome unknown")

        remote.close = AsyncMock(side_effect=stuck_close)
        receiver._asr_session = remote
        detector = SimpleNamespace(release_endpointing=AsyncMock())
        lease = SmartTurnLease(
            VoiceTurnToken(h.manager._capture_ingress_token(), 1), detector, 7
        )
        receiver._asr_smart_turn_lease = lease
        audio_generation = receiver._asr_audio_generation
        receiver.submit.side_effect = uncertain_write
        await h.feed(3_000)
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        begin = asyncio.create_task(
            h.manager._begin_voice_activation_handoff(h.session("target"))
        )
        try:
            await _until(lambda: h.activation.output_paused)
            release_write.set()
            await asyncio.wait_for(closing.wait(), timeout=2.0)
            assert receiver._asr_session is None
            assert receiver._asr_audio_generation > audio_generation
            assert h.manager._asr_route_mode == "blocked"
            done, _ = await asyncio.wait({begin}, timeout=1.5)
            assert begin in done, (
                "handoff waited indefinitely for detached provider close"
            )
            assert begin.result() is False
            assert not release_close.is_set()
            assert h.activation.state is ActivationState.UNAVAILABLE
            await _until(lambda: not receiver._asr_owned_cleanup_tasks)
            assert close_finished.is_set()
            assert receiver._asr_smart_turn_lease is None
            assert lease._released
            detector.release_endpointing.assert_awaited_once_with(lease.token, 7)
        finally:
            release_write.set()
            release_close.set()
            await asyncio.wait_for(
                asyncio.gather(begin, return_exceptions=True), timeout=2.0
            )
