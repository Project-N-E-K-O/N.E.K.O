"""Bounded candidate PCM sources on the original capture sample axis."""

from __future__ import annotations

from typing import Protocol

from .prewire_gate.candidate_contracts import CandidateAudio, CandidateBatch, CandidateBinding
from .prewire_gate.contracts import PrewireIntervalSpec, SampleRange


class CandidateSourceUnavailable(RuntimeError):
    """No complete candidate exists for this exact real scoring interval."""


class CandidateSource(Protocol):
    def batch_for(self, spec: PrewireIntervalSpec, binding: CandidateBinding) -> CandidateBatch: ...
    def discard_before(self, sample: int) -> None: ...
    def clear(self) -> None: ...


class CandidateAudioBuffer:
    """Cache a continuous single target output, without padding or raw fallback.

    The runtime supplies PCM converted by the same code used for delivery.
    This adapter makes no anonymous multi-channel continuity claim.
    """

    def __init__(self, binding: CandidateBinding, *, max_buffered_pcm_bytes: int, start_sample: int = 0):
        if type(binding) is not CandidateBinding:
            raise ValueError("binding must be CandidateBinding")
        if type(max_buffered_pcm_bytes) is not int or max_buffered_pcm_bytes <= 0:
            raise ValueError("max_buffered_pcm_bytes must be positive")
        if type(start_sample) is not int or start_sample < 0:
            raise ValueError("start_sample must be non-negative")
        self.binding = binding
        self._max_bytes = max_buffered_pcm_bytes
        self._start = start_sample
        self._pcm = bytearray()
        self._closed = False

    @property
    def buffered_pcm_bytes(self) -> int:
        return len(self._pcm)

    @property
    def captured_end(self) -> int:
        return self._start + len(self._pcm) // 2

    def append_pcm(self, *, start_sample: int, pcm16: bytes) -> None:
        if self._closed:
            raise CandidateSourceUnavailable("candidate_source_retired")
        if type(start_sample) is not int or start_sample != self.captured_end:
            raise CandidateSourceUnavailable("candidate_input_discontinuity")
        if type(pcm16) is not bytes or not pcm16 or len(pcm16) % 2:
            raise ValueError("candidate PCM must contain complete real samples")
        if len(self._pcm) + len(pcm16) > self._max_bytes:
            raise CandidateSourceUnavailable("candidate_source_capacity")
        self._pcm.extend(pcm16)

    def batch_for(self, spec: PrewireIntervalSpec, binding: CandidateBinding) -> CandidateBatch:
        if self._closed or binding != self.binding:
            raise CandidateSourceUnavailable("candidate_source_identity_mismatch")
        binding.validate_spec(spec)
        sample_range = spec.scoring_range
        if sample_range.start < self._start or sample_range.end > self.captured_end:
            raise CandidateSourceUnavailable("candidate_scoring_range_not_available")
        offset = (sample_range.start - self._start) * 2
        pcm = bytes(self._pcm[offset:offset + sample_range.sample_count * 2])
        return CandidateBatch(binding, spec, (CandidateAudio(binding, "target", sample_range, pcm),))

    def discard_before(self, sample: int) -> None:
        if type(sample) is not int or sample < self._start or sample > self.captured_end:
            raise ValueError("candidate_discard_range_invalid")
        count = (sample - self._start) * 2
        self._pcm[:count] = b"\x00" * count
        del self._pcm[:count]
        self._start = sample

    def clear(self) -> None:
        self._closed = True
        self._pcm[:] = b"\x00" * len(self._pcm)
        self._pcm.clear()


class CandidateBatchBuffer:
    """Hold complete zero/one/two-channel windows from an injected separator.

    Batches include their own precise identity and content. The source never
    stitches anonymous channel indices across blocks or treats an absent batch
    as evidence that no one spoke. Separator implementations must supply the
    real full scoring windows; streaming multi-channel inference is separate.
    """

    def __init__(self, binding: CandidateBinding, *, max_buffered_pcm_bytes: int, max_batches: int):
        if type(binding) is not CandidateBinding:
            raise ValueError("binding must be CandidateBinding")
        if type(max_buffered_pcm_bytes) is not int or max_buffered_pcm_bytes <= 0 or type(max_batches) is not int or max_batches <= 0:
            raise ValueError("candidate batch budgets must be positive")
        self.binding = binding
        self._max_bytes = max_buffered_pcm_bytes
        self._max_batches = max_batches
        self._batches: dict[PrewireIntervalSpec, CandidateBatch] = {}
        self._bytes = 0
        self._released_cursor = 0
        self._closed = False

    @property
    def buffered_pcm_bytes(self) -> int:
        return self._bytes

    def push_batch(self, batch: CandidateBatch) -> None:
        if self._closed or type(batch) is not CandidateBatch or batch.binding != self.binding:
            raise CandidateSourceUnavailable("candidate_source_identity_mismatch")
        if batch.spec in self._batches:
            raise CandidateSourceUnavailable("candidate_batch_already_published")
        if batch.spec.commit_range.start < self._released_cursor:
            raise CandidateSourceUnavailable("candidate_batch_already_released")
        added = sum(len(candidate.pcm16) for candidate in batch.candidates)
        if self._bytes + added > self._max_bytes or len(self._batches) >= self._max_batches:
            raise CandidateSourceUnavailable("candidate_source_capacity")
        self._batches[batch.spec] = batch
        self._bytes += added

    def batch_for(self, spec: PrewireIntervalSpec, binding: CandidateBinding) -> CandidateBatch:
        if self._closed or binding != self.binding:
            raise CandidateSourceUnavailable("candidate_source_identity_mismatch")
        batch = self._batches.get(spec)
        if batch is None:
            raise CandidateSourceUnavailable("candidate_scoring_range_not_available")
        return batch

    def discard_before(self, sample: int) -> None:
        if type(sample) is not int or sample < self._released_cursor:
            raise ValueError("sample must be non-negative")
        self._released_cursor = sample
        for spec in tuple(self._batches):
            if spec.commit_range.end <= sample:
                batch = self._batches.pop(spec)
                self._bytes -= sum(len(candidate.pcm16) for candidate in batch.candidates)

    def clear(self) -> None:
        self._closed = True
        self._batches.clear()
        self._bytes = 0
