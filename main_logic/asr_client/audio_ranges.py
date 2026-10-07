"""PCM buffers whose sample ownership survives trimming and migration."""

from dataclasses import dataclass

from .audio import AudioRingBuffer


@dataclass(frozen=True, slots=True)
class AudioSampleSpan:
    start: int | None
    samples: int

    @property
    def end(self) -> int | None:
        return None if self.start is None else self.start + self.samples


class RangedAudioBuffer(AudioRingBuffer):
    """Keep metadata beside bytes, including unknown legacy input and holes."""

    def __init__(self, *, capacity_ms: int, sample_rate_hz: int = 16_000):
        super().__init__(capacity_ms=capacity_ms, sample_rate_hz=sample_rate_hz)
        self._spans: list[AudioSampleSpan] = []

    @property
    def spans(self) -> tuple[AudioSampleSpan, ...]:
        return tuple(self._spans)

    def append(self, pcm16: bytes, *, sample_rate_hz: int | None = None,
               start_sample: int | None = None) -> bytes:
        dropped = super().append(pcm16, sample_rate_hz=sample_rate_hz)
        if pcm16:
            span = AudioSampleSpan(start_sample, len(pcm16) // 2)
            if self._spans and start_sample is not None and self._spans[-1].end == start_sample:
                previous = self._spans.pop()
                span = AudioSampleSpan(previous.start, previous.samples + span.samples)
            self._spans.append(span)
            self._trim_spans(len(dropped) // 2)
        return dropped

    def _trim_spans(self, samples: int) -> None:
        while samples and self._spans:
            span = self._spans.pop(0)
            if samples < span.samples:
                start = None if span.start is None else span.start + samples
                self._spans.insert(0, AudioSampleSpan(start, span.samples - samples))
                return
            samples -= span.samples

    def trim_to_bytes(self, count: int) -> None:
        count = max(0, count - count % 2)
        remove = max(0, self.byte_count - count)
        if remove:
            del self._audio[:remove]
            self._trim_spans(remove // 2)

    def move_to(self, target: "RangedAudioBuffer") -> int:
        payload = self.peek()
        offset = 0
        dropped = 0
        for span in self._spans:
            size = span.samples * 2
            dropped += len(target.append(payload[offset:offset + size], start_sample=span.start))
            offset += size
        self.clear()
        return dropped

    def range_failure(self, start: int, end: int) -> str | None:
        if start >= end or not self._spans:
            return "candidate_audio_range_missing"
        expected = start
        found = False
        for span in self._spans:
            if span.start is None:
                return "candidate_audio_range_discontinuous"
            # Rejected/evicted predecessors are outside this candidate. A gap
            # there does not invalidate an otherwise complete requested tail.
            if span.end <= start:
                continue
            if not found:
                if span.start > start:
                    return "candidate_audio_range_missing"
                found = True
            elif span.start != expected:
                return "candidate_audio_range_discontinuous"
            expected = span.end
        if not found or expected != end:
            return "candidate_audio_range_missing"
        return None

    def drain(self) -> bytes:
        payload = super().drain()
        self._spans.clear()
        return payload

    def clear(self) -> None:
        super().clear()
        self._spans.clear()
