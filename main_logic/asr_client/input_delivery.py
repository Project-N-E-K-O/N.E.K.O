"""Map retained ASR source spans to opaque original audio delivery tags."""

from __future__ import annotations

from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag
from main_logic.voice_turn.interception_events import InterceptionDeliveryStage
from .audio_ranges import AudioSampleSpan


class InputAudioDeliveryLedger:
    def __init__(self, max_tags: int = 128) -> None:
        self._max_tags = max_tags
        self._tags: list[tuple[int, int, AudioDeliveryTag]] = []
        self._untagged: list[tuple[int, int]] = []
        self._max_untagged_end: int | None = None
        self.enabled = False

    def register(self, start: int, end: int, tag: AudioDeliveryTag) -> None:
        self._tags = [entry for entry in self._tags if not entry[2].settled]
        if len(self._tags) >= self._max_tags:
            raise RuntimeError("ASR_DELIVERY_TAG_CAPACITY")
        if end - start != tag.sample_count:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_MISMATCH")
        if self._tags and start < self._tags[-1][1]:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_OVERLAP")
        if self._untagged and start < self._untagged[-1][1]:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_OVERLAP")
        if self._max_untagged_end is not None and start < self._max_untagged_end:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_OVERLAP")
        self.enabled = True
        self._tags.append((start, end, tag))

    def register_untagged(self, start: int, end: int) -> None:
        if start >= end:
            return
        if self._tags and start < self._tags[-1][1]:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_OVERLAP")
        if self._untagged and start < self._untagged[-1][1]:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_OVERLAP")
        if self._max_untagged_end is not None and start < self._max_untagged_end:
            raise ValueError("ASR_DELIVERY_TAG_RANGE_OVERLAP")
        self._max_untagged_end = end
        if self._untagged and self._untagged[-1][1] == start:
            self._untagged[-1] = (self._untagged[-1][0], end)
        else:
            self._untagged.append((start, end))

    def _subtract_untagged(self, start: int, end: int) -> None:
        if start >= end or not self._untagged:
            return
        updated: list[tuple[int, int]] = []
        for left, right in self._untagged:
            if right <= start or left >= end:
                updated.append((left, right))
                continue
            if left < start:
                updated.append((left, start))
            if right > end:
                updated.append((end, right))
        self._untagged = updated

    def prune_untagged_before(self, sample: int) -> None:
        """Drop untagged intervals that end at or before sample and trim straddling intervals."""
        if not self._untagged:
            return
        updated: list[tuple[int, int]] = []
        for left, right in self._untagged:
            if right <= sample:
                continue
            updated.append((max(left, sample), right))
        self._untagged = updated

    def partition_spans(
        self, ranges: tuple[AudioSampleSpan, ...], samples: int,
    ) -> list[tuple[int, int, tuple[AudioDeliverySpan, ...]]]:
        """Partition payload spans into contiguous tagged and untagged segments."""
        if not self.enabled:
            return [(0, samples, ())]
        if not self._untagged:
            return [(0, samples, self.spans(ranges, samples))]
        segments: list[tuple[int, int, tuple[AudioDeliverySpan, ...]]] = []
        payload_offset = 0
        for span in ranges:
            if span.start is None:
                raise RuntimeError("ASR_DELIVERY_SOURCE_RANGE_UNKNOWN")
            if span.samples == 0:
                continue
            pieces: list[tuple[int, int, AudioDeliverySpan | None]] = []
            for left, right, tag in self._tags:
                begin, end = max(span.start, left), min(span.end, right)
                if begin < end:
                    pieces.append((begin, end, AudioDeliverySpan(tag, begin - left, end - left)))
            for left, right in self._untagged:
                begin, end = max(span.start, left), min(span.end, right)
                if begin < end:
                    pieces.append((begin, end, None))
            pieces.sort(key=lambda item: item[0])
            cursor = span.start
            for begin, end, delivery_span in pieces:
                if begin != cursor:
                    raise RuntimeError("ASR_DELIVERY_SOURCE_RANGE_MISSING")
                count = end - begin
                if delivery_span is None:
                    if segments and not segments[-1][2]:
                        prev_off, prev_cnt, _ = segments[-1]
                        segments[-1] = (prev_off, prev_cnt + count, ())
                    else:
                        segments.append((payload_offset, count, ()))
                else:
                    if segments and segments[-1][2]:
                        prev_off, prev_cnt, prev_spans = segments[-1]
                        segments[-1] = (prev_off, prev_cnt + count, prev_spans + (delivery_span,))
                    else:
                        segments.append((payload_offset, count, (delivery_span,)))
                payload_offset += count
                cursor = end
            if cursor != span.end:
                raise RuntimeError("ASR_DELIVERY_SOURCE_RANGE_MISSING")
        if payload_offset != samples:
            raise RuntimeError("ASR_DELIVERY_PAYLOAD_RANGE_MISMATCH")
        if not segments:
            return [(0, 0, ())]
        return segments

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
        if span.samples > 0 and span.end is not None:
            self._subtract_untagged(span.start, span.end)
