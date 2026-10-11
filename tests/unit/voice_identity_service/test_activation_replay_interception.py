"""Replay provenance preserves capture facts and still requires real interception."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
import time

import pytest

from main_logic.voice_identity_service.activation_runtime import VoiceSessionActivationRuntime
from main_logic.voice_identity_service.interception_runtime import PrewireInterceptionFactory
from main_logic.voice_input.activation import (
    ActivationGeneration, ActivationState, AudioFrame, OutputCommit, OutputOrigin,
    VoiceActivationController, WakeWordDetection, WakeWordBatchResult,
)
from main_logic.voice_input.activation.wiring import VoiceSessionActivationRouteContext
from tests.unit.asr_runtime.test_active_session_interception import _Runtime as CoreHarness
from tests.unit.voice_identity_service.test_activation_runtime import _Scorer as ActivationScorer
from tests.unit.voice_identity_service.test_interception_runtime import _config, _Scorer, _Classifier, _Tse

pytestmark = [pytest.mark.asyncio, pytest.mark.unit_fast]

GENERATION = ActivationGeneration("session", 1, 1, 1, 1, "core")


def _frame(core, *, age=0.0, origin=OutputOrigin.LIVE, samples=2000, sequence=0):
    captured = time.time() - age
    context = VoiceSessionActivationRouteContext(1.0, True, None, core._capture_ingress_token(), captured)
    return AudioFrame(sequence, sequence * samples, (sequence + 1) * samples,
                      captured, 16000, b"\x02\x00" * samples, GENERATION, context,
                      output_origin=origin)


async def _core(worker=None, **config_changes):
    core = CoreHarness()
    factory = PrewireInterceptionFactory(
        replace(_config(), **config_changes), score_backend=_Scorer(), classifier=_Classifier(),
        tse_factory=lambda _: worker or _Tse(),
    )
    assert await core.set_active_session_interception_factory(factory)
    return core, factory


async def _close(core):
    await core.set_active_session_interception_factory(None, interception_required=False)


@pytest.mark.parametrize("origin,age,kept", [
    (OutputOrigin.LIVE, 0.0, True),
    (OutputOrigin.REPLAY, 2.5, True),
    (OutputOrigin.REPLAY, 3.3, True),
    (OutputOrigin.LIVE, 2.5, False),
    (OutputOrigin.LIVE, 3.3, False),
])
async def test_old_replay_passes_real_gate_while_old_live_fails_closed(origin, age, kept):
    core, factory = await _core()
    frame = _frame(core, origin=origin, age=age)
    original = (frame.captured_at, frame.sample_start, frame.sample_end, frame.context)
    try:
        result = await core._intercept_active_session_frame(frame, GENERATION, frame.context)
        assert bool(result) is kept
        runtime = factory._runtimes[0]
        if kept:
            assert len(result) == 1600
            assert runtime._settled_sample == 800
            assert runtime._tse.started
        else:
            assert runtime._output_revoked
            assert runtime._tse is None
        assert (frame.captured_at, frame.sample_start, frame.sample_end, frame.context) == original
        assert frame.context.captured_at == frame.captured_at
        with pytest.raises(FrozenInstanceError):
            frame.output_origin = OutputOrigin.LIVE
    finally:
        await _close(core)


class _BufferedTse(_Tse):
    async def push(self, pcm, *, start_sample):
        return []


async def test_new_replay_frame_does_not_renew_old_unfinished_prefix():
    core, factory = await _core(_BufferedTse())
    try:
        first = _frame(core, age=3.3, origin=OutputOrigin.REPLAY)
        assert await core._intercept_active_session_frame(first, GENERATION, first.context) is None
        runtime = factory._runtimes[0]
        first_deadline = runtime._capture_deadline
        second = _frame(core, age=2.5, origin=OutputOrigin.REPLAY, samples=400, sequence=5)
        assert await core._intercept_active_session_frame(second, GENERATION, second.context) is None
        assert runtime._capture_deadline == first_deadline
        runtime._arm_deadline(asyncio.get_running_loop().time())
        await asyncio.wait_for(runtime._deadline_task, 0.5)
        assert runtime._output_revoked
        assert not (await runtime.finish()).pcm16
    finally:
        await _close(core)


class _BlockedTse(_Tse):
    def __init__(self):
        super().__init__()
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def push(self, pcm, *, start_sample):
        self.entered.set()
        await self.release.wait()
        return await super().push(pcm, start_sample=start_sample)

    async def close(self, *, timeout=1.0):
        self.release.set()
        return True


@pytest.mark.parametrize("trigger", ["deadline", "cancel", "revoke"])
async def test_replay_stalled_model_never_returns_pcm_after_deadline_or_revoke(trigger):
    worker = _BlockedTse()
    core, factory = await _core(worker, scoring_deadline_seconds=.02 if trigger == "deadline" else 1,
                                scoring_close_timeout_seconds=.05)
    frame = _frame(core, origin=OutputOrigin.REPLAY, age=3.3)
    task = asyncio.create_task(core._intercept_active_session_frame(frame, GENERATION, frame.context))
    try:
        await asyncio.wait_for(worker.entered.wait(), .5)
        if trigger == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif trigger == "revoke":
            core.require_active_session_interception()
            worker.release.set()
            assert await asyncio.wait_for(task, .5) is None
        else:
            assert await asyncio.wait_for(task, .5) is None
        assert factory._runtimes[0]._output_revoked
    finally:
        worker.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await _close(core)


async def test_replay_origin_cannot_bypass_generation_precondition():
    core, factory = await _core()
    core._capture_voice_session_activation_generation = lambda: GENERATION
    core._voice_session_activation_degraded = False
    frame = _frame(core, age=3.3, origin=OutputOrigin.REPLAY)
    frame = replace(frame, generation=replace(GENERATION, route=2))
    try:
        assert await core._route_voice_session_activation_output(frame, GENERATION) is OutputCommit.NOT_SENT
        assert factory._runtimes == []
    finally:
        await _close(core)


async def test_authorized_activation_replays_old_prefix_through_real_core_gate():
    core, factory = await _core()
    now = time.time()
    delivered, emitted = [], []

    async def output(frame):
        emitted.append(frame)
        pcm = await core._intercept_active_session_frame(frame, GENERATION, frame.context)
        if pcm is not None:
            delivered.append(pcm)
        return OutputCommit.LOCAL_ACCEPTED

    activation = VoiceSessionActivationRuntime(
        GENERATION, ActivationScorer(), output,
        controller=VoiceActivationController(clock=lambda: now),
    )
    original = [
        AudioFrame(index, index * 1600, (index + 1) * 1600,
                   now - 3.3 + index / 10, 16000, b"\x02\x00" * 1600,
                   GENERATION, VoiceSessionActivationRouteContext(
                       1.0, True, None, core._capture_ingress_token(), now - 3.3 + index / 10,
                   ))
        for index in range(15)
    ]
    try:
        await activation.prepare()
        for frame in original:
            await activation.feed(frame, voice_activity=True)
        # Scoring and the writer are real asynchronous owners in this chain.
        for _ in range(200):
            await asyncio.sleep(0)
            if activation._output_task is not None:
                break
        assert activation._output_task is not None
        await asyncio.wait_for(asyncio.shield(activation._output_task), 2)
        assert len(emitted) == len(original)
        assert delivered
        assert all(frame.output_origin is OutputOrigin.REPLAY for frame in emitted)
        assert all(replace(frame, output_origin=OutputOrigin.LIVE) == source
                   for frame, source in zip(emitted, original, strict=True))
        assert factory._runtimes[0]._settled_sample > 0
        assert not factory._runtimes[0]._output_revoked
    finally:
        await activation.close()
        await _close(core)


async def test_replay_provenance_still_requires_owner_identity():
    core = CoreHarness()
    factory = PrewireInterceptionFactory(
        _config(), score_backend=_Scorer(), classifier=_Classifier(), tse_factory=lambda _: _Tse(),
    )
    assert await core.set_active_session_interception_factory(factory)
    frame = replace(_frame(core, origin=OutputOrigin.REPLAY, age=3.3), pcm=b"\x00\x00" * 2000)
    try:
        assert await core._intercept_active_session_frame(frame, GENERATION, frame.context) is None
        assert factory._runtimes[0]._settled_sample == 800
        assert not factory._runtimes[0]._output_revoked
    finally:
        await _close(core)


async def test_output_origin_is_a_typed_immutable_contract():
    core = CoreHarness()
    frame = _frame(core)
    assert frame.output_origin is OutputOrigin.LIVE
    for invalid in ("replay", None, True):
        with pytest.raises(ValueError, match="OUTPUT_ORIGIN_INVALID"):
            replace(frame, output_origin=invalid)


@pytest.mark.parametrize("activation_kind", ["speaker", "wake", "bypass"])
async def test_single_activation_writer_attaches_lease_origin_without_mutating_capture(activation_kind):
    sent = []
    now = time.time()

    async def output(frame):
        sent.append(frame)
        return OutputCommit.TRANSPORT_WRITTEN

    class Detector:
        inference_timeout_seconds = 1.0

        async def prepare(self):
            return None

        async def feed_batch(self, frames, epoch):
            frame = frames[-1]
            return WakeWordBatchResult(len(frames), WakeWordDetection(
                "keyword", frame.generation, epoch, frame.sample_start, frame.sample_end,
            ))

        async def close(self):
            return None

    activation = VoiceSessionActivationRuntime(
        GENERATION, ActivationScorer(), output,
        controller=VoiceActivationController(clock=lambda: now),
        enabled=activation_kind != "bypass",
        wake_detector=Detector() if activation_kind == "wake" else None,
    )
    frames = []
    try:
        await activation.prepare()
        for sequence in range(15):
            frame = AudioFrame(sequence, sequence * 1600, (sequence + 1) * 1600,
                               now - 1.5 + sequence / 10, 16000, b"\x02\x00" * 1600,
                               GENERATION, {"sequence": sequence})
            frames.append(frame)
            await activation.feed(frame, voice_activity=activation_kind != "wake")
        # Join the actual writer/scoring owners instead of guessing timing.
        for _ in range(100):
            await asyncio.sleep(0)
            if activation_kind == "bypass" or activation.state is ActivationState.ACTIVE:
                if sent:
                    break
        expected_origin = OutputOrigin.BYPASS if activation_kind == "bypass" else OutputOrigin.REPLAY
        assert sent
        assert sent[0].output_origin is expected_origin
        for emitted in sent:
            source = frames[emitted.sequence]
            assert replace(emitted, output_origin=OutputOrigin.LIVE) == source
            assert source.output_origin is OutputOrigin.LIVE
        if activation_kind != "bypass":
            live = AudioFrame(15, 24000, 25600, now, 16000, b"\x02\x00" * 1600, GENERATION)
            await activation.feed(live, voice_activity=True)
            for _ in range(100):
                await asyncio.sleep(0)
                if sent[-1].sequence == 15:
                    break
            assert sent[-1].sequence == 15
            assert sent[-1].output_origin is OutputOrigin.LIVE
    finally:
        await activation.close()
