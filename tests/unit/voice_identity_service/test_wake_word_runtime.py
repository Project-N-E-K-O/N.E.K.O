import asyncio

import pytest

from main_logic.voice_identity_service.activation_runtime import (
    VoiceSessionActivationRuntime,
    VoiceSessionActivationRuntimeConfig,
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
    WakeWordDetection,
)


GENERATION = ActivationGeneration("wake", 1, 2, 3, 4, "microphone")


def frame(sequence, captured_at=None):
    return AudioFrame(
        sequence,
        sequence * 1600,
        (sequence + 1) * 1600,
        sequence / 10 if captured_at is None else captured_at,
        16000,
        bytes([sequence % 251]) * 3200,
        GENERATION,
    )


class Scorer:
    profile_generation = "profile"
    scorer_generation = 1

    def __init__(self):
        self.calls = []
        self.results = asyncio.Queue()

    async def prepare(self):
        return ActivationScoreStatus.READY

    async def score(self, identity, pcm, *, sample_rate_hz):
        self.calls.append(identity)
        status, similarity = await self.results.get()
        return ActivationScoreResult(identity, status, similarity)

    async def close(self):
        pass


class Detector:
    def __init__(self, hit_at=None, *, fail_prepare=False, fail_feed=False):
        self.hit_at = hit_at
        self.fail_prepare = fail_prepare
        self.fail_feed = fail_feed
        self.frames = []
        self.closed = False
        self.release = None

    async def prepare(self):
        if self.fail_prepare:
            raise RuntimeError("model missing")

    async def feed(self, audio, epoch):
        self.frames.append((audio, epoch))
        if self.release is not None:
            await self.release.wait()
        if self.fail_feed:
            raise RuntimeError("decoder failed")
        if audio.sequence == self.hit_at:
            return WakeWordDetection(
                "keyword", audio.generation, epoch, audio.sample_start, audio.sample_end
            )

    async def close(self):
        self.closed = True


async def settle():
    for _ in range(40):
        await asyncio.sleep(0)


def runtime(detector, *, clock=lambda: 1.5, config=None):
    sent, statuses = [], []
    scorer = Scorer()

    async def output(audio):
        sent.append(audio)
        return OutputCommit.TRANSPORT_WRITTEN

    instance = VoiceSessionActivationRuntime(
        GENERATION,
        scorer,
        output,
        controller=VoiceActivationController(clock=clock),
        wake_detector=detector,
        config=config,
        status_callback=statuses.append,
    )
    return instance, scorer, sent, statuses


@pytest.mark.asyncio
async def test_keyword_receives_silence_short_audio_and_preserves_following_instruction():
    detector = Detector(hit_at=2)
    instance, scorer, sent, statuses = runtime(detector)
    await instance.prepare()
    for i in range(10):
        await instance.feed(frame(i), voice_activity=False)
    await settle()
    assert [audio.sequence for audio, _ in detector.frames] == [0, 1, 2]
    assert sent == [frame(i) for i in range(10)]
    assert scorer.calls == []
    assert instance.state is ActivationState.ACTIVE
    assert sum(item.reason == "wake_word_detected" for item in statuses) == 1
    await instance.close()
    assert detector.closed


@pytest.mark.asyncio
async def test_non_keyword_short_audio_stays_waiting():
    detector = Detector()
    instance, scorer, sent, _ = runtime(detector)
    await instance.prepare()
    for i in range(5):
        await instance.feed(frame(i), voice_activity=True)
    await settle()
    assert len(detector.frames) == 5
    assert scorer.calls == []
    assert sent == []
    assert instance.state is ActivationState.WAITING
    await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["prepare", "feed"])
async def test_detector_failures_report_unavailable(failure):
    detector = Detector(fail_prepare=failure == "prepare", fail_feed=failure == "feed")
    instance, _, sent, statuses = runtime(detector)
    await instance.prepare()
    await instance.feed(frame(0), voice_activity=False)
    await settle()
    assert instance.state is ActivationState.UNAVAILABLE
    assert any(
        item.reason
        == f"wake_word_{'prepare' if failure == 'prepare' else 'runtime'}_failed"
        for item in statuses
    )
    assert sent == []
    await instance.close()


@pytest.mark.asyncio
async def test_late_old_speaker_failure_does_not_damage_next_round_request():
    now = [1.5]
    detector = Detector(hit_at=15)
    instance, scorer, sent, statuses = runtime(detector, clock=lambda: now[0])
    await instance.prepare()
    for i in range(15):
        await instance.feed(frame(i), voice_activity=True)
    await settle()
    assert len(scorer.calls) == 1
    await instance.feed(frame(15), voice_activity=False)
    await settle()
    assert instance.state is ActivationState.ACTIVE
    now[0] = 32.0
    await instance.tick()
    for i in range(16, 31):
        await instance.feed(frame(i, 32.0 + (i - 16) / 10), voice_activity=True)
    await settle()
    assert instance.state is ActivationState.VERIFYING
    await scorer.results.put((ActivationScoreStatus.FAILED, None))
    await settle()
    assert len(scorer.calls) == 2
    assert instance.state is ActivationState.VERIFYING
    await scorer.results.put((ActivationScoreStatus.READY, 0.9))
    await settle()
    assert instance.state is ActivationState.ACTIVE
    assert [audio.sequence for audio in sent] == list(range(11, 31))
    assert not any(item.reason == "verification_failed" for item in statuses)
    await instance.close()


@pytest.mark.asyncio
async def test_late_keyword_after_close_cannot_send():
    detector = Detector(hit_at=0)
    detector.release = asyncio.Event()
    instance, _, sent, _ = runtime(detector)
    await instance.prepare()
    await instance.feed(frame(0), voice_activity=False)
    await settle()
    await instance.close()
    detector.release.set()
    await settle()
    assert instance.state is ActivationState.CLOSED
    assert sent == []


@pytest.mark.asyncio
async def test_detector_backlog_is_bounded_and_fails_closed():
    detector = Detector()
    detector.release = asyncio.Event()
    instance, _, sent, statuses = runtime(
        detector, config=VoiceSessionActivationRuntimeConfig(wake_queue_bytes=6400)
    )
    await instance.prepare()
    for i in range(4):
        await instance.feed(frame(i), voice_activity=False)
    await settle()
    assert instance.state is ActivationState.UNAVAILABLE
    assert any(item.reason == "wake_word_queue_overflow" for item in statuses)
    assert sent == []
    await instance.close()


@pytest.mark.asyncio
async def test_owner_first_then_old_keyword_in_next_round_is_ignored():
    now = [1.5]
    detector = Detector(hit_at=0)
    detector.release = asyncio.Event()
    instance, scorer, sent, statuses = runtime(detector, clock=lambda: now[0])
    await instance.prepare()
    for i in range(15):
        await instance.feed(frame(i), voice_activity=True)
    await settle()
    await scorer.results.put((ActivationScoreStatus.READY, 0.9))
    await settle()
    assert instance.state is ActivationState.ACTIVE
    now[0] = 32.0
    await instance.tick()
    detector.release.set()
    await settle()
    assert instance.state is ActivationState.WAITING
    assert not any(item.reason == "wake_word_detected" for item in statuses)
    detector.hit_at = 15
    await instance.feed(frame(15, 32.0), voice_activity=False)
    await settle()
    assert instance.state is ActivationState.ACTIVE
    assert [audio.sequence for audio in sent] == list(range(16))
    assert sum(item.reason == "wake_word_detected" for item in statuses) == 1
    await instance.close()


@pytest.mark.asyncio
async def test_keyword_after_current_speaker_failure_cannot_reopen_input():
    detector = Detector(hit_at=0)
    detector.release = asyncio.Event()
    instance, scorer, sent, _ = runtime(detector)
    await instance.prepare()
    for i in range(15):
        await instance.feed(frame(i), voice_activity=True)
    await settle()
    await scorer.results.put((ActivationScoreStatus.FAILED, None))
    await settle()
    assert instance.state is ActivationState.UNAVAILABLE
    detector.release.set()
    await settle()
    assert instance.state is ActivationState.UNAVAILABLE
    assert sent == []
    await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,similarity",
    [
        (ActivationScoreStatus.READY, 0.1),
        (ActivationScoreStatus.INVALID_AUDIO, None),
    ],
)
async def test_keyword_after_normal_speaker_rejection_remains_eligible(
    status, similarity
):
    detector = Detector(hit_at=0)
    detector.release = asyncio.Event()
    instance, scorer, sent, _ = runtime(detector)
    await instance.prepare()
    for i in range(15):
        await instance.feed(frame(i), voice_activity=True)
    await settle()
    await scorer.results.put((status, similarity))
    await settle()
    assert instance.state is ActivationState.WAITING
    detector.release.set()
    await settle()
    assert instance.state is ActivationState.ACTIVE
    assert sent == [frame(i) for i in range(15)]
    await instance.close()
