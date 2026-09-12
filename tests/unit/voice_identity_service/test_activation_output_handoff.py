from __future__ import annotations

import asyncio

import pytest

from main_logic.voice_identity_service.activation_runtime import (
    VoiceSessionActivationRuntime,
)
from main_logic.voice_identity_service.activation_scoring import (
    ActivationScoreResult,
    ActivationScoreStatus,
)
from main_logic.voice_input.activation import (
    ActivationGeneration,
    ActivationState,
    AudioFrame,
    OutputCommit,
    VoiceActivationController,
)


class Scorer:
    profile_generation = "profile"
    scorer_generation = 1

    def __init__(self):
        self.prepares = self.scores = self.closes = 0

    async def prepare(self):
        self.prepares += 1
        return ActivationScoreStatus.READY

    async def score(self, identity, pcm, *, sample_rate_hz):
        self.scores += 1
        return ActivationScoreResult(identity, ActivationScoreStatus.READY, 0.9)

    async def close(self):
        self.closes += 1


GEN = ActivationGeneration("conversation", 1, 1, 1, 1, "core_chat")


def frame(seq, at=None):
    return AudioFrame(
        seq,
        seq * 1600,
        (seq + 1) * 1600,
        seq * 0.1 if at is None else at,
        16000,
        b"\0" * 3200,
        GEN,
    )


async def settle():
    for _ in range(25):
        await asyncio.sleep(0)


async def active(output):
    clock = [1.5]
    scorer = Scorer()
    runtime = VoiceSessionActivationRuntime(
        GEN,
        scorer,
        output,
        controller=VoiceActivationController(clock=lambda: clock[0]),
    )
    await runtime.prepare()
    for seq in range(15):
        await runtime.feed(frame(seq), voice_activity=True)
    await settle()
    assert runtime.state is ActivationState.ACTIVE
    return runtime, clock, scorer


@pytest.mark.asyncio
async def test_three_swaps_preserve_objects_order_and_idle_deadline():
    sent = []

    async def output(item):
        sent.append(item.sequence)
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, clock, scorer = await active(output)
    try:
        timer = runtime._idle_task
        for seq in range(15, 18):
            owner = object()
            assert await runtime.pause_output(
                owner, deadline=asyncio.get_running_loop().time() + 1
            )
            await runtime.feed(frame(seq, at=20 + seq - 15), voice_activity=True)
            await settle()
            assert sent[-1] == seq - 1
            assert runtime.pending_output_bytes == 3200
            assert not await runtime.resume_output(object())
            assert await runtime.resume_output(owner)
            await settle()
            assert sent[-1] == seq
            assert runtime._idle_task is timer
        assert scorer.prepares == scorer.scores == 1
        assert scorer.closes == 0
        assert runtime.last_voice_at == pytest.approx(22.1)
        assert (await runtime.tick(now=52.099)).state is ActivationState.ACTIVE
        assert (await runtime.tick(now=52.1)).state is ActivationState.WAITING
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "commit",
    [OutputCommit.NOT_SENT, OutputCommit.TRANSPORT_WRITTEN, OutputCommit.UNKNOWN],
)
async def test_pause_waits_for_inflight_result_without_claiming_next(commit):
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    async def output(item):
        sent.append(item.sequence)
        if item.sequence == 15 and sent.count(15) == 1:
            entered.set()
            await release.wait()
            return commit
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        await runtime.feed(frame(15), voice_activity=True)
        await entered.wait()
        owner = object()
        pause = asyncio.create_task(
            runtime.pause_output(owner, deadline=asyncio.get_running_loop().time() + 1)
        )
        await settle()
        assert not pause.done()
        await runtime.feed(frame(16), voice_activity=True)
        release.set()
        assert await pause is (commit is not OutputCommit.UNKNOWN)
        assert sent == list(range(16))
        if commit is OutputCommit.UNKNOWN:
            assert runtime.state is ActivationState.UNAVAILABLE
            assert not await runtime.resume_output(owner)
        else:
            assert runtime.pending_output_bytes == (
                6400 if commit is OutputCommit.NOT_SENT else 3200
            )
            assert await runtime.resume_output(owner)
            await settle()
            assert sent[-1] == 16
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_capture_watermark_prevents_late_processing_from_expiring_owner():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, clock, _ = await active(output)
    pending = [30.0]
    runtime.set_capture_progress_provider(lambda: pending[0])
    try:
        assert (await runtime.tick(now=32.0)).state is ActivationState.ACTIVE
        clock[0] = 32.0
        await runtime.feed(frame(15, 30.0), voice_activity=True)
        pending[0] = None
        assert (await runtime.tick(now=32.0)).state is ActivationState.ACTIVE
        assert runtime.last_voice_at == pytest.approx(30.1)
        assert (await runtime.tick(now=60.1)).state is ActivationState.WAITING
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_stuck_capture_watermark_fails_in_five_seconds():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    runtime.set_capture_progress_provider(lambda: 30.0)
    try:
        assert (await runtime.tick(now=32.0)).state is ActivationState.ACTIVE
        assert (await runtime.tick(now=36.999)).state is ActivationState.ACTIVE
        result = await runtime.tick(now=37.0)
        assert result.state is ActivationState.UNAVAILABLE
        assert result.reason == "capture_progress_timeout"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_cancelled_pause_does_not_cancel_output_or_allow_late_resume_after_failure():
    entered, release = asyncio.Event(), asyncio.Event()
    cancelled = False

    async def output(item):
        nonlocal cancelled
        if item.sequence == 15:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        await runtime.feed(frame(15), voice_activity=True)
        await entered.wait()
        owner = object()
        pause = asyncio.create_task(
            runtime.pause_output(owner, deadline=asyncio.get_running_loop().time() + 1)
        )
        await settle()
        pause.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pause
        assert not cancelled
        assert runtime.output_paused
        await runtime.fail_output(object(), "wrong_owner")
        assert runtime.state is ActivationState.ACTIVE
        await runtime.fail_output(owner, "handoff_cancelled")
        assert runtime.state is ActivationState.UNAVAILABLE
        assert not await runtime.resume_output(owner)
        release.set()
        await settle()
        assert runtime.state is ActivationState.UNAVAILABLE
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_pause_timeout_never_cancels_unknown_send_or_resumes_while_inflight():
    entered, release = asyncio.Event(), asyncio.Event()
    cancelled = False

    async def output(item):
        nonlocal cancelled
        if item.sequence == 15:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        await runtime.feed(frame(15), voice_activity=True)
        await entered.wait()
        owner = object()
        assert not await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time()
        )
        assert not cancelled
        assert runtime.output_inflight
        assert not await runtime.resume_output(owner)
        await runtime.fail_output(owner, "handoff_timeout")
        release.set()
        await settle()
        assert not await runtime.resume_output(owner)
        assert runtime.state is ActivationState.UNAVAILABLE
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_expiry_while_paused_keeps_only_previously_authorized_output():
    sent = []

    async def output(item):
        sent.append(item.sequence)
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, clock, _ = await active(output)
    try:
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 1
        )
        await runtime.feed(frame(15, 25), voice_activity=False)
        assert runtime.last_voice_at == pytest.approx(1.5)
        clock[0] = 31.5
        assert (await runtime.tick()).state is ActivationState.WAITING
        await runtime.feed(frame(16, 32), voice_activity=True)
        assert runtime.state is ActivationState.WAITING
        assert await runtime.resume_output(owner)
        await settle()
        assert sent == list(range(16))
        assert runtime.state is ActivationState.WAITING
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_close_retires_pause_owner_and_prevents_late_resume():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, scorer = await active(output)
    owner = object()
    assert await runtime.pause_output(
        owner, deadline=asyncio.get_running_loop().time() + 1
    )
    await runtime.close()
    assert not await runtime.resume_output(owner)
    await runtime.fail_output(owner, "late_failure")
    assert runtime.state is ActivationState.CLOSED
    assert scorer.closes == 1


@pytest.mark.asyncio
async def test_replay_completion_obeys_pending_capture_watermark():
    entered, release = asyncio.Event(), asyncio.Event()
    clock = [1.5]
    pending = [30.0]

    async def output(item):
        if item.sequence == 14:
            entered.set()
            await release.wait()
        return OutputCommit.TRANSPORT_WRITTEN

    runtime = VoiceSessionActivationRuntime(
        GEN,
        Scorer(),
        output,
        controller=VoiceActivationController(clock=lambda: clock[0]),
    )
    await runtime.prepare()
    try:
        for seq in range(15):
            await runtime.feed(frame(seq), voice_activity=True)
        await entered.wait()
        assert runtime.state is ActivationState.REPLAYING
        runtime.set_capture_progress_provider(lambda: pending[0])
        clock[0] = 32
        release.set()
        await settle()
        assert runtime.state is ActivationState.ACTIVE
        await runtime.feed(frame(15, 30), voice_activity=True)
        pending[0] = None
        assert (await runtime.tick()).state is ActivationState.ACTIVE
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_cancelled_writer_is_unknown_and_cannot_be_resumed():
    entered = asyncio.Event()

    async def output(item):
        if item.sequence == 15:
            entered.set()
            await asyncio.Future()
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        await runtime.feed(frame(15), voice_activity=True)
        await entered.wait()
        owner = object()
        pause = asyncio.create_task(
            runtime.pause_output(owner, deadline=asyncio.get_running_loop().time() + 1)
        )
        await settle()
        runtime._output_task.cancel()
        assert not await pause
        assert runtime.state is ActivationState.UNAVAILABLE
        assert not await runtime.resume_output(owner)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_watermark_after_deadline_does_not_extend_activation():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    runtime.set_capture_progress_provider(lambda: 31.5)
    try:
        assert (await runtime.tick(now=32)).state is ActivationState.WAITING
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_late_model_ready_cannot_recover_failed_handoff_ticket():
    started, release = asyncio.Event(), asyncio.Event()

    class PreparingScorer(Scorer):
        async def prepare(self):
            started.set()
            await release.wait()
            return ActivationScoreStatus.READY

    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime = VoiceSessionActivationRuntime(GEN, PreparingScorer(), output)
    preparing = asyncio.create_task(runtime.prepare())
    try:
        await started.wait()
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 1
        )
        await runtime.fail_output(owner, "handoff_failed")
        release.set()
        assert (await preparing).state is ActivationState.UNAVAILABLE
        assert not await runtime.resume_output(owner)
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
async def test_verification_finishing_during_pause_still_requires_complete_replay():
    entered, release = asyncio.Event(), asyncio.Event()
    sent = []

    class SlowScorer(Scorer):
        async def score(self, identity, pcm, *, sample_rate_hz):
            entered.set()
            await release.wait()
            return await super().score(identity, pcm, sample_rate_hz=sample_rate_hz)

    async def output(item):
        sent.append(item.sequence)
        return OutputCommit.TRANSPORT_WRITTEN

    scorer = SlowScorer()
    runtime = VoiceSessionActivationRuntime(
        GEN, scorer, output, controller=VoiceActivationController(clock=lambda: 1.5)
    )
    await runtime.prepare()
    try:
        for seq in range(15):
            await runtime.feed(frame(seq), voice_activity=True)
        await entered.wait()
        assert runtime.verification_inflight
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 1
        )
        assert runtime.state is ActivationState.VERIFYING
        release.set()
        await settle()
        assert runtime.state is ActivationState.REPLAYING
        assert runtime.pending_output_bytes == 15 * 3200
        assert sent == []
        assert await runtime.resume_output(owner)
        await settle()
        assert runtime.state is ActivationState.ACTIVE
        assert sent == list(range(15))
        assert scorer.scores == 1
    finally:
        release.set()
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [float("nan"), float("inf"), "bad"])
async def test_invalid_capture_progress_fails_closed(pending):
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    runtime.set_capture_progress_provider(lambda: pending)
    try:
        result = await runtime.tick(now=32)
        assert result.state is ActivationState.UNAVAILABLE
        assert result.reason == "capture_progress_invalid"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_resumed_owner_can_fail_until_core_finishes_commit():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 1
        )
        assert await runtime.resume_output(owner)
        await runtime.fail_output(owner, "handoff_deadline_after_resume")
        assert runtime.state is ActivationState.UNAVAILABLE
        assert runtime.output_paused
        assert not await runtime.resume_output(owner)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_completed_resume_releases_ticket_and_rejects_stale_failure():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 1
        )
        assert await runtime.resume_output(owner)
        assert not runtime.complete_output_handoff(object())
        assert runtime.complete_output_handoff(owner)
        assert runtime._output_resumed_owner is None
        assert not runtime.complete_output_handoff(owner)
        await runtime.fail_output(owner, "stale_failure")
        assert runtime.state is ActivationState.ACTIVE
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_new_pause_supersedes_previous_resumed_owner():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        old_owner, next_owner = object(), object()
        assert await runtime.pause_output(
            old_owner, deadline=asyncio.get_running_loop().time() + 1
        )
        assert await runtime.resume_output(old_owner)
        assert await runtime.pause_output(
            next_owner, deadline=asyncio.get_running_loop().time() + 1
        )
        await runtime.fail_output(old_owner, "late_old_failure")
        assert runtime.state is ActivationState.ACTIVE
        assert runtime.output_paused
        assert not runtime.complete_output_handoff(old_owner)
        assert await runtime.resume_output(next_owner)
        await runtime.close()
        assert runtime._output_resumed_owner is None
        assert runtime._output_pause_owner is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry_trigger", ["frame", "tick"])
async def test_expired_replay_preserves_old_authorized_pcm_but_rejects_new_short(
    expiry_trigger,
):
    clock = [1.5]
    writable = False
    sent = []

    async def output(item):
        if not writable:
            return OutputCommit.NOT_SENT
        sent.append(item.sequence)
        return OutputCommit.TRANSPORT_WRITTEN

    runtime = VoiceSessionActivationRuntime(
        GEN,
        Scorer(),
        output,
        controller=VoiceActivationController(clock=lambda: clock[0]),
    )
    try:
        await runtime.prepare()
        for seq in range(15):
            await runtime.feed(frame(seq), voice_activity=True)
        await settle()
        assert runtime.state is ActivationState.REPLAYING
        assert runtime.idle_deadline == pytest.approx(31.5)
        clock[0] = 31.0
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 5
        )
        clock[0] = 32.0
        if expiry_trigger == "tick":
            assert (await runtime.tick()).state is ActivationState.WAITING
        await runtime.feed(frame(15, 32.0), voice_activity=True)
        assert runtime.state is ActivationState.WAITING
        assert runtime.pending_output_bytes == 15 * 3200
        writable = True
        assert await runtime.resume_output(owner)
        await settle()
        assert sent == list(range(15))
        assert runtime.state is ActivationState.WAITING
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_paused_queue_capacity_remains_bounded_without_dropping_middle_audio():
    sent = []

    async def output(item):
        sent.append(item.sequence)
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    try:
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 5
        )
        for seq in range(15, 95):
            await runtime.feed(frame(seq), voice_activity=True)
        assert runtime.pending_output_bytes == 256000
        assert runtime.state is ActivationState.ACTIVE
        result = await runtime.feed(frame(95), voice_activity=True)
        assert result.state is ActivationState.UNAVAILABLE
        assert result.reason == "output_backlog_overflow"
        assert runtime.pending_output_bytes == 0
        assert not await runtime.resume_output(owner)
        assert sent == list(range(15))
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_silent_capture_progress_does_not_reset_the_five_second_guard():
    async def output(_):
        return OutputCommit.TRANSPORT_WRITTEN

    runtime, _, _ = await active(output)
    pending = [30.0]
    runtime.set_capture_progress_provider(lambda: pending[0])
    try:
        assert (await runtime.tick(now=32)).state is ActivationState.ACTIVE
        await runtime.feed(frame(15, 30.0), voice_activity=False)
        pending[0] = 30.5
        assert (await runtime.tick(now=34)).state is ActivationState.ACTIVE
        await runtime.feed(frame(16, 30.5), voice_activity=False)
        pending[0] = 31.0
        assert (await runtime.tick(now=37)).state is ActivationState.UNAVAILABLE
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_expired_replay_tail_cannot_be_reused_as_new_activation_preroll():
    clock = [1.5]
    writable = False
    sent = []

    async def output(item):
        if not writable:
            return OutputCommit.NOT_SENT
        sent.append(item.sequence)
        return OutputCommit.TRANSPORT_WRITTEN

    scorer = Scorer()
    runtime = VoiceSessionActivationRuntime(
        GEN,
        scorer,
        output,
        controller=VoiceActivationController(clock=lambda: clock[0]),
    )
    try:
        await runtime.prepare()
        for seq in range(15):
            await runtime.feed(frame(seq), voice_activity=True)
        await settle()
        owner = object()
        assert await runtime.pause_output(
            owner, deadline=asyncio.get_running_loop().time() + 5
        )
        clock[0] = 31.4
        await runtime.feed(frame(15, 31.4), voice_activity=False)
        assert runtime.state is ActivationState.REPLAYING
        for seq in range(16, 31):
            clock[0] = 31.5 + (seq - 16) * 0.1
            await runtime.feed(frame(seq, clock[0]), voice_activity=True)
        await settle()
        assert runtime.state is ActivationState.REPLAYING
        assert scorer.scores == 2
        writable = True
        assert await runtime.resume_output(owner)
        await settle()
        assert sent == list(range(31))
        assert runtime.state is ActivationState.ACTIVE
    finally:
        await runtime.close()
