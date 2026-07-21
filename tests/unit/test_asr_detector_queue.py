from __future__ import annotations

import asyncio

import pytest

from main_logic.asr_client.detector import (
    DetectorAudioItem,
    DetectorIngressIdentity,
)
from main_logic.asr_client.detector import DetectorDurationQueue
from main_logic.asr_client.lifecycle import VoiceIngressToken


def _identity(sequence_no: int) -> DetectorIngressIdentity:
    return DetectorIngressIdentity(
        ingress_token=VoiceIngressToken(1, "socket", 1, 1, 1),
        detector_epoch=2,
        sequence_no=sequence_no,
    )


def _audio(sequence_no: int, samples: int) -> DetectorAudioItem:
    return DetectorAudioItem.from_pcm16(
        b"\x01\x00" * samples,
        identity=_identity(sequence_no),
        sample_rate_hz=16_000,
    )


def test_detector_audio_duration_uses_ceiling_accounting() -> None:
    item = _audio(1, 161)

    assert item.duration_us == 10_063


async def test_detector_queue_bounds_real_audio_duration_and_frames() -> None:
    duration_queue: DetectorDurationQueue[DetectorAudioItem, str] = DetectorDurationQueue(
        capacity_us=20_000,
        max_frames=2,
    )
    duration_queue.put_audio_nowait(_audio(1, 160))
    duration_queue.put_audio_nowait(_audio(2, 160))

    with pytest.raises(asyncio.QueueFull):
        duration_queue.put_audio_nowait(_audio(3, 1))

    assert duration_queue.audio_duration_us == 20_000
    assert duration_queue.audio_frames == 2


async def test_control_lane_survives_full_audio_budget_and_preserves_order() -> None:
    duration_queue: DetectorDurationQueue[DetectorAudioItem, str] = DetectorDurationQueue(
        capacity_us=10_000,
        max_frames=1,
    )
    audio = _audio(1, 160)
    duration_queue.put_audio_nowait(audio)
    duration_queue.put_control_nowait("evaluation-result")

    assert await duration_queue.get() is audio
    assert await duration_queue.get() == "evaluation-result"


async def test_priority_control_preempts_audio_backlog() -> None:
    duration_queue: DetectorDurationQueue[DetectorAudioItem, str] = DetectorDurationQueue()
    duration_queue.put_audio_nowait(_audio(1, 160))
    duration_queue.put_control_nowait("hard-reset", priority=True)

    assert await duration_queue.get() == "hard-reset"


def test_discard_audio_preserves_control_items() -> None:
    duration_queue: DetectorDurationQueue[DetectorAudioItem, str] = DetectorDurationQueue()
    duration_queue.put_audio_nowait(_audio(1, 160))
    duration_queue.put_control_nowait("invalidate")
    duration_queue.put_audio_nowait(_audio(2, 160))

    assert duration_queue.discard_audio() == 2
    assert duration_queue.audio_duration_us == 0
    assert duration_queue.audio_frames == 0
    assert duration_queue.get_nowait() == "invalidate"
