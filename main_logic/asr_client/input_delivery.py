"""Map retained ASR source spans to opaque original audio delivery tags."""

from __future__ import annotations

from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag
from main_logic.voice_turn.interception_events import InterceptionDeliveryStage
from .audio_ranges import AudioSampleSpan


class InputAudioDeliveryLedger:
    def __init__(self, max_tags: int = 128) -> None:
        self._max_tags = max_tags
        self._tags: list[tuple[int, int, AudioDeliveryTag]] = []
        self.enabled = False

    def register(self, start: int, end: int, tag: AudioDeliveryTag) -> None:
        self._tags = [entry for entry in self._tags if not entry[2].settled]
        if len(self._tags) >= self._max_tags:
            raise RuntimeError("ASR_DELIVERY_TAG_CAPACITY")
        if end - start != tag.sample_count:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_MISMATCH")
        if self._tags and start < self._tags[-1][1]:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_OVERLAP")
        self.enabled = True
        self._tags.append((start, end, tag))

    def spans(self, ranges: tuple[AudioSampleSpan, ...], samples: int) -> tuple[AudioDeliverySpan, ...]:
        if not self.enabled:
            return ()
        result = []
        for span in ranges:
            if span.start is None:
                raise RuntimeError("ASR_DELIVERY_SOURCE_RANGE_UNKNOWN")
            cursor = span.start
            for left, right, tag in self._tags:
                begin, end = max(span.start, left), min(span.end, right)
                if begin < end:
                    if begin != cursor:
                        raise RuntimeError("ASR_DELIVERY_SOURCE_RANGE_MISSING")
                    result.append(AudioDeliverySpan(tag, begin - left, end - left))
                    cursor = end
            if cursor != span.end:
                raise RuntimeError("ASR_DELIVERY_SOURCE_RANGE_MISSING")
        if sum(span.samples for span in result) != samples:
            raise RuntimeError("ASR_DELIVERY_PAYLOAD_RANGE_MISMATCH")
        return tuple(result)

    def discard(self, span: AudioSampleSpan) -> None:
        if span.start is None:
            return
        for left, right, tag in self._tags:
            begin, end = max(span.start, left), min(span.end, right)
            if begin < end and not tag.settled:
                tag.observe(begin - left, end - left, InterceptionDeliveryStage.NOT_SENT)
