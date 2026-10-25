"""Continuous rejected speech must not exhaust activation or lower its threshold."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_identity_service.activation_scoring import ActivationScoreResult, ActivationScoreStatus
from main_logic.voice_input.activation import (
    ActivationState, AudioFrame, OutputCommit, VoiceActivationConfig, VoiceActivationController,
)
from tests.unit.voice_identity_service.test_activation_runtime import _Scorer, _frame, _generation
from tests.support.activation_harness import _harness, _until


pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


class _RecordingScorer(_Scorer):
    def __init__(self):
        super().__init__(similarity=0.1)
        self.inputs = []
        self.status = ActivationScoreStatus.READY

    async def score(self, identity, pcm16, *, sample_rate_hz):
        self.inputs.append(pcm16)
        self.calls += 1
        return ActivationScoreResult(
            identity, self.status,
            self.similarity if self.status is ActivationScoreStatus.READY else None,
        )


class _Rig:
    def __init__(self):
        self.scorer = _RecordingScorer()
        self.now = 0.0
        self.sequence = 0
        self.sent = []
        self.decisions = []
        self.runtime = VoiceSessionActivationRuntime(
            _generation(), self.scorer, self.output,
            controller=VoiceActivationController(clock=lambda: self.now),
            status_callback=self.decisions.append,
        )

    async def output(self, frame):
        self.sent.append(frame)
        return OutputCommit.TRANSPORT_WRITTEN

    async def feed(self, count, *, voice=True, settle=True):
        for _ in range(count):
            frame = _frame(self.sequence)
            self.sequence += 1
            self.now = frame.captured_end_at
            await self.runtime.feed(frame, voice_activity=voice)
            if settle:
                await self.settle()

    async def settle(self):
        # Wait for owned operations, rather than guessing how many loop ticks
        # inference, its callback, and the output writer require.
        async with asyncio.timeout(2):
            while self.runtime._verification_task or self.runtime._output_task:
                tasks = tuple(task for task in (
                    self.runtime._verification_task, self.runtime._output_task,
                ) if task is not None)
                await asyncio.gather(*tasks)


@asynccontextmanager
async def _rig():
    rig = _Rig()
    await rig.runtime.prepare()
    try:
        yield rig
    finally:
        await rig.runtime.close()


async def test_two_low_scores_rearm_a_fresh_candidate_without_silence():
    async with _rig() as rig:
        await rig.feed(30)
        assert rig.scorer.calls == 2
        assert rig.runtime.state is ActivationState.WAITING
        assert not rig.sent
        rig.scorer.similarity = 0.9
        await rig.feed(14)
        assert rig.scorer.calls == 2  # The new window still needs 1.5 s.
        await rig.feed(1)
        assert rig.runtime.state is ActivationState.ACTIVE
        assert rig.scorer.calls == 3
        assert rig.scorer.inputs[-1] == b"".join(_frame(n).pcm for n in range(30, 45))
        assert [frame.sequence for frame in rig.sent] == list(range(rig.sent[0].sequence, 45))
        assert rig.sent[0].sequence >= 27  # Only the existing bounded pre-roll.


async def test_long_non_owner_speech_has_bounded_repeat_checks_and_no_output():
    async with _rig() as rig:
        await rig.feed(300)
        assert rig.scorer.calls == 20
        assert rig.runtime.state is ActivationState.WAITING
        assert not rig.sent
        assert rig.runtime._controller.buffered_bytes <= 256_000
        assert not rig.runtime.verification_inflight


@pytest.mark.parametrize("outcome", ["mismatch", "insufficient", "owner"])
async def test_evicted_queued_or_replay_audio_rearms_an_exhausted_candidate(outcome):
    async with _rig() as rig:
        entered, release = asyncio.Event(), asyncio.Event()
        score = rig.scorer.score

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await score(*args, **kwargs)

        rig.scorer.score = delayed
        rig.scorer.similarity = 0.9 if outcome == "owner" else 0.1
        if outcome == "insufficient":
            rig.scorer.status = ActivationScoreStatus.INVALID_AUDIO
        try:
            # Sub-threshold pauses retain one candidate. Its first check starts
            # at 7 s; another 1.5 s of speech queues the second check and evicts
            # the candidate's beginning from the existing eight-second cache.
            for _ in range(14):
                await rig.feed(1, settle=False)
                await rig.feed(4, voice=False, settle=False)
            await rig.feed(1, settle=False)
            await asyncio.wait_for(entered.wait(), 2)
            await rig.feed(15, settle=False)
            assert rig.runtime._controller.verification_queued
            release.set()
            await rig.settle()
            assert rig.scorer.calls == 1
            assert rig.decisions[-1].reason == (
                "replay_source_evicted" if outcome == "owner"
                else "queued_candidate_unavailable"
            )
            assert rig.runtime.state is ActivationState.WAITING
            assert not rig.sent  # Eviction never authorizes stale replay.
            rig.scorer.status = ActivationScoreStatus.READY
            rig.scorer.similarity = 0.9
            await rig.feed(14)
            assert rig.scorer.calls == 1  # The fresh candidate still needs 1.5 s.
            await rig.feed(1)
            assert rig.runtime.state is ActivationState.ACTIVE
            assert rig.scorer.calls == 2
            assert rig.scorer.inputs[-1] == b"".join(_frame(n).pcm for n in range(86, 101))
            assert [frame.sequence for frame in rig.sent] == list(range(rig.sent[0].sequence, 101))
            assert rig.sent[0].sequence >= 82  # Existing pre-roll plus one boundary frame.
        finally:
            release.set()


async def test_short_phrases_stay_blocked_and_report_insufficient_speech():
    async with _rig() as rig:
        rig.scorer.similarity = 0.9
        for _ in range(5):
            await rig.feed(10)
            await rig.feed(6, voice=False)
            assert rig.decisions[-1].reason == "speech_too_short"
        assert rig.scorer.calls == 0
        assert not rig.sent
        assert rig.runtime.state is ActivationState.WAITING
        await rig.feed(15)
        assert rig.runtime.state is ActivationState.ACTIVE


@pytest.mark.parametrize("sample_rate", [8_000, 16_000, 48_000])
@pytest.mark.parametrize("voice_frames", [10, 15])
@pytest.mark.parametrize("gap", ["speech", "capture"])
async def test_short_speech_hint_uses_candidate_sample_rate(sample_rate, voice_frames, gap):
    now = 0.0
    scorer = _RecordingScorer()
    decisions, sent = [], []

    async def output(frame):
        sent.append(frame)
        return OutputCommit.TRANSPORT_WRITTEN

    runtime = VoiceSessionActivationRuntime(
        _generation(), scorer, output,
        controller=VoiceActivationController(
            VoiceActivationConfig(sample_rate=sample_rate), clock=lambda: now,
        ),
        status_callback=decisions.append,
    )

    async def feed(sequence, *, voice, capture_offset=0.0):
        nonlocal now
        samples = sample_rate // 10
        frame = AudioFrame(
            sequence=sequence, sample_start=sequence * samples,
            sample_end=(sequence + 1) * samples,
            captured_at=sequence * 0.1 + capture_offset,
            sample_rate=sample_rate, pcm=b"\x01\x00" * samples,
            generation=_generation(),
        )
        now = frame.captured_end_at
        await runtime.feed(frame, voice_activity=voice)
        if runtime._verification_task is not None:
            await runtime._verification_task

    try:
        await runtime.prepare()
        for sequence in range(voice_frames):
            await feed(sequence, voice=True)
        assert scorer.calls == int(voice_frames == 15)
        if gap == "capture":
            await feed(voice_frames, voice=True, capture_offset=0.6)
        else:
            for sequence in range(voice_frames, voice_frames + 6):
                await feed(sequence, voice=False)
        expected = "speech_too_short" if voice_frames < 15 else "owner_not_confirmed"
        assert decisions[-1].reason == expected
        assert runtime.state is ActivationState.WAITING
        assert not sent
    finally:
        await runtime.close()


async def test_mismatch_hint_survives_subsequent_waiting_frames():
    async with _rig() as rig:
        await rig.feed(15)
        assert rig.decisions[-1].reason == "owner_not_confirmed"
        await rig.feed(3)
        assert rig.decisions[-1].reason == "owner_not_confirmed"
        assert rig.decisions[-1].state is ActivationState.WAITING


@pytest.mark.parametrize("tail_frames", [14, 15])
@pytest.mark.parametrize("outcome", ["mismatch", "insufficient", "failed", "closed"])
async def test_pending_second_score_preserves_only_a_live_eligible_tail(tail_frames, outcome):
    async with _rig() as rig:
        await rig.feed(15)
        entered, release = asyncio.Event(), asyncio.Event()
        score = rig.scorer.score
        calls = 0

        async def delayed(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                rig.scorer.status = (
                    ActivationScoreStatus.INVALID_AUDIO if outcome == "insufficient"
                    else ActivationScoreStatus.FAILED if outcome == "failed"
                    else ActivationScoreStatus.READY
                )
                result = await score(*args, **kwargs)
                rig.scorer.status = ActivationScoreStatus.READY
                rig.scorer.similarity = 0.9
                return result
            return await score(*args, **kwargs)

        rig.scorer.score = delayed
        try:
            await rig.feed(15, settle=False)
            await asyncio.wait_for(entered.wait(), 2)
            await rig.feed(tail_frames, settle=False)
            if outcome == "closed":
                await rig.runtime.close()
                release.set()
                assert rig.runtime.state is ActivationState.CLOSED
                assert not rig.sent
                return
            release.set()
            await rig.settle()
            if outcome == "failed":
                assert rig.runtime.state is ActivationState.UNAVAILABLE
                assert rig.scorer.calls == 2
                assert not rig.sent
                return
            if tail_frames == 14:
                assert rig.runtime.state is ActivationState.WAITING
                assert rig.scorer.calls == 2
                assert not rig.sent
                await rig.feed(1)
            assert rig.runtime.state is ActivationState.ACTIVE
            assert rig.scorer.calls == 3
            assert rig.scorer.inputs[-1] == b"".join(_frame(n).pcm for n in range(30, 45))
            assert [frame.sequence for frame in rig.sent] == list(range(rig.sent[0].sequence, 45))
            assert rig.sent[0].sequence >= 26
        finally:
            release.set()


@pytest.mark.parametrize("ending", ["silence", "capture_gap", "expired"])
async def test_pending_tail_is_retired_at_a_real_gap_or_expiry(ending):
    async with _rig() as rig:
        await rig.feed(15)
        entered, release = asyncio.Event(), asyncio.Event()
        score = rig.scorer.score

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await score(*args, **kwargs)

        rig.scorer.score = delayed
        try:
            await rig.feed(15, settle=False)
            await asyncio.wait_for(entered.wait(), 2)
            await rig.feed(15, settle=False)
            if ending == "silence":
                await rig.feed(6, voice=False, settle=False)
            elif ending == "capture_gap":
                frame = _frame(rig.sequence, captured_at=rig.now + 0.6)
                rig.now = frame.captured_end_at
                await rig.runtime.feed(frame, voice_activity=True)
            else:
                rig.now += 31
            release.set()
            await rig.settle()
            assert rig.runtime.state is ActivationState.WAITING
            assert rig.scorer.calls == 2
            assert not rig.sent
        finally:
            release.set()


@pytest.mark.parametrize("successor_frames", [14, 15])
@pytest.mark.parametrize("status", [ActivationScoreStatus.READY, ActivationScoreStatus.INVALID_AUDIO])
async def test_queue_release_checks_a_ready_successor_without_another_input_frame(successor_frames, status):
    async with _rig() as rig:
        entered, release = asyncio.Event(), asyncio.Event()
        score = rig.scorer.score
        started = 0

        async def delayed(*args, **kwargs):
            nonlocal started
            started += 1
            entered.set()
            await release.wait()
            rig.scorer.status = status if started <= 2 else ActivationScoreStatus.READY
            rig.scorer.similarity = 0.1 if started <= 2 else 0.9
            return await score(*args, **kwargs)

        rig.scorer.score = delayed
        try:
            await rig.feed(15, settle=False)
            await asyncio.wait_for(entered.wait(), 2)
            await rig.feed(15, settle=False)
            assert rig.runtime._controller.verification_queued
            await rig.feed(6, voice=False, settle=False)
            await rig.feed(successor_frames, settle=False)
            assert rig.runtime._candidate_start_sequence == 36
            assert not rig.sent
            release.set()
            await rig.settle()
            if successor_frames == 14:
                assert rig.runtime.state is ActivationState.WAITING
                assert rig.scorer.calls == 2
                assert not rig.sent
                await rig.feed(1)
            assert rig.runtime.state is ActivationState.ACTIVE
            assert rig.scorer.calls == 3
            assert rig.scorer.inputs[-1] == b"".join(_frame(n).pcm for n in range(36, 51))
            assert not rig.runtime.verification_inflight
        finally:
            release.set()


async def test_insufficient_score_can_rearm_but_backend_failure_cannot():
    async with _rig() as rig:
        rig.scorer.status = ActivationScoreStatus.INVALID_AUDIO
        await rig.feed(30)
        assert rig.decisions[-1].reason == "verification_insufficient"
        rig.scorer.status = ActivationScoreStatus.READY
        rig.scorer.similarity = 0.9
        await rig.feed(15)
        assert rig.runtime.state is ActivationState.ACTIVE
    async with _rig() as rig:
        rig.scorer.status = ActivationScoreStatus.TIMED_OUT
        await rig.feed(60)
        assert rig.runtime.state is ActivationState.UNAVAILABLE
        assert rig.scorer.calls == 1
        assert not rig.sent


async def test_late_rejection_does_not_clear_a_successor_candidate():
    async with _rig() as rig:
        await rig.feed(15)
        entered, release = asyncio.Event(), asyncio.Event()
        score = rig.scorer.score

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await score(*args, **kwargs)

        rig.scorer.score = delayed
        try:
            await rig.feed(15, settle=False)
            await asyncio.wait_for(entered.wait(), 2)
            await rig.feed(6, voice=False, settle=False)
            await rig.feed(10, settle=False)
            successor_start = rig.runtime._candidate_start_sequence
            release.set()
            await rig.settle()
            assert rig.runtime._candidate_start_sequence == successor_start
            rig.scorer.similarity = 0.9
            await rig.feed(5)
            assert rig.runtime.state is ActivationState.ACTIVE
            assert rig.scorer.inputs[-1] == b"".join(_frame(n).pcm for n in range(36, 51))
        finally:
            release.set()


async def test_verifying_notification_is_not_overwritten_by_a_stale_waiting_frame():
    async with _rig() as rig:
        entered, release = asyncio.Event(), asyncio.Event()
        score = rig.scorer.score

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return await score(*args, **kwargs)

        rig.scorer.score = delayed
        try:
            await rig.feed(15, settle=False)
            await asyncio.wait_for(entered.wait(), 2)
            assert rig.decisions[-1].state is ActivationState.VERIFYING
            release.set()
            await rig.settle()
        finally:
            release.set()


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_continuous_retry_reaches_both_core_audio_routes(route):
    async with _harness(route, active=False) as h:
        scorer = h.factory.scorers[0]
        scorer.similarity = 0.1
        for index in range(29):
            h.clock.value += 0.1
            await h.feed(3000 + index)
            task = h.activation._verification_task
            if task is not None:
                await task
        assert scorer.calls == 2
        assert not h.deliveries
        assert h.activation.state is ActivationState.WAITING
        scorer.similarity = 0.9
        expected = []
        for index in range(15):
            h.clock.value += 0.1
            expected.append(await h.feed(4000 + index))
        await _until(lambda: h.activation.state is ActivationState.ACTIVE)
        await _until(lambda: len(h.pcm) >= len(expected))
        assert h.pcm[-15:] == expected
        assert scorer.calls == 3


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_ready_successor_queue_handoff_reaches_both_core_routes(route):
    async with _harness(route, active=False) as h:
        scorer = h.factory.scorers[0]
        entered, release = asyncio.Event(), asyncio.Event()
        score = scorer.score
        started = 0

        async def delayed(*args, **kwargs):
            nonlocal started
            started += 1
            entered.set()
            await release.wait()
            scorer.similarity = 0.1 if started <= 2 else 0.9
            return await score(*args, **kwargs)

        scorer.score = delayed
        try:
            for index in range(14):
                h.clock.value += 0.1
                await h.feed(3000 + index)
            await asyncio.wait_for(entered.wait(), 2)
            for index in range(15):
                h.clock.value += 0.1
                await h.feed(3100 + index)
            await _until(lambda: h.activation._controller.verification_queued)
            for _ in range(6):
                h.clock.value += 0.1
                await h.feed(0, voice=False)
            expected = []
            for index in range(15):
                h.clock.value += 0.1
                expected.append(await h.feed(4000 + index))
            await _until(lambda: h.activation._candidate_start_sequence == 36
                         and h.activation._candidate_voice_samples == 24_000)
            assert not h.deliveries
            release.set()
            await _until(lambda: h.activation.state is ActivationState.ACTIVE)
            await _until(lambda: len(h.pcm) >= 15)
            assert h.pcm[-15:] == expected
            assert scorer.calls == 3
        finally:
            release.set()
