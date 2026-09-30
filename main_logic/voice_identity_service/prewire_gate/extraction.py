"""Join authorized pre-wire ranges with extracted PCM, without sending audio.

This adapter is not connected to the production ASR runtime. It neither scores
identity nor grants permission: the caller must pass events from the trusted
PrewireGate and must separately enqueue/claim their delivery. A returned event
is not a transport acknowledgement. If enqueue or remote delivery is uncertain,
retire the handle; never replay returned events through a replacement stream.

Use one instance from one serial coordinator. The coordinator passes the handle
captured before every asynchronous inference/gate operation, so a late result
cannot populate its successor. PCM is 16 kHz mono PCM16 on the original sample
axis. Chunk boundaries and gate boundaries may differ; gaps remain explicit.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TypeAlias

from .contracts import PrewireDecisionState, PrewireIntervalIdentity, PrewireStreamKey, SampleRange
from .gate import PrewireAudioEvent, PrewireEndEvent, PrewireGapEvent


class ExtractionAlignmentError(RuntimeError):
    """Current stream data cannot safely be aligned for submission."""


class ExtractionIdentityError(ExtractionAlignmentError):
    """A stale or foreign result has no authority over this stream."""


class ExtractionCapacityError(ExtractionAlignmentError):
    """A bounded resource was exhausted; the current stream is retired."""


@dataclass(frozen=True, slots=True)
class ExtractionHandle:
    """Adapter-issued ownership token, valid by object identity, not equality."""

    stream: PrewireStreamKey
    profile_generation: str
    model_generation: str
    config_generation: str
    generation: int


@dataclass(frozen=True, slots=True)
class ExtractedPrewireAudio:
    """Only extracted audio for one complete, already-authorized gate event."""

    identity: PrewireIntervalIdentity
    original_range: SampleRange
    asr_range: SampleRange
    pcm16: bytes
    kind: str = field(default="enhanced_audio", init=False)


ExtractedPrewireEvent: TypeAlias = ExtractedPrewireAudio | PrewireGapEvent | PrewireEndEvent


@dataclass(frozen=True, slots=True)
class _AuthorizedRange:
    # Do not retain PrewireAudioEvent: it contains the original microphone PCM.
    identity: PrewireIntervalIdentity
    original_range: SampleRange
    asr_range: SampleRange


class PrewireExtractionAdapter:
    """A bounded, exactly ordered join of gate decisions and TSE output.

    open_stream() -> handle; append_extracted(handle, range, pcm16) and
    accept_event(handle, gate_event) each return newly available ordered events.
    Neither call waits, performs inference, nor falls back to raw gate audio.

    Invalid current-stream order/size or capacity retires its handle. A foreign
    handle or gate version is rejected without changing the current owner.
    retire() is for capture gaps, profile/config changes or uncertain delivery;
    close() permanently prevents new streams. ASR sentence ends must not call
    either method: PrewireEndEvent denotes the end of continuous capture.
    """

    def __init__(self, *, max_buffered_pcm_bytes: int, max_pending_events: int) -> None:
        if type(max_buffered_pcm_bytes) is not int or max_buffered_pcm_bytes <= 0:
            raise ValueError("max_buffered_pcm_bytes must be positive")
        if type(max_pending_events) is not int or max_pending_events <= 0:
            raise ValueError("max_pending_events must be positive")
        self._max_bytes = max_buffered_pcm_bytes
        self._max_events = max_pending_events
        self._handle: ExtractionHandle | None = None
        self._generation = 0
        self._closed = False
        self._completed = False
        self._failure_reason: str | None = None
        self._pcm = bytearray()
        self._pending: deque[_AuthorizedRange | PrewireGapEvent | PrewireEndEvent] = deque()
        self._buffer_start = self._extracted_cursor = 0
        self._authorized_cursor = self._emitted_cursor = self._asr_cursor = 0
        self._last_segment = 0
        self._end_cursor: int | None = None

    @property
    def buffered_pcm_bytes(self) -> int:
        return len(self._pcm)

    @property
    def pending_event_count(self) -> int:
        return len(self._pending)

    @property
    def failure_reason(self) -> str | None:
        return self._failure_reason

    def delivery_is_current(self, handle: ExtractionHandle) -> bool:
        """Fence returned events after a caller await and before enqueue.

        A completed capture can still deliver its final returned audio/end.
        Retirement, replacement or close invalidates those events. This is
        ownership evidence only; it does not authorize a gate range or prove
        that the transport accepted it.
        """
        return type(handle) is ExtractionHandle and not self._closed and handle is self._handle

    def open_stream(
        self, stream: PrewireStreamKey, *, profile_generation: str,
        model_generation: str, config_generation: str,
        original_cursor: int = 0, asr_cursor: int = 0,
    ) -> ExtractionHandle:
        if self._closed:
            raise ExtractionAlignmentError("extraction_adapter_closed")
        if self._handle is not None and not self._completed:
            raise ExtractionIdentityError("extraction_stream_already_open")
        if type(stream) is not PrewireStreamKey:
            raise TypeError("stream must be PrewireStreamKey")
        for value in (profile_generation, model_generation, config_generation):
            if type(value) is not str or not value.strip():
                raise ValueError("extraction versions must be non-empty strings")
        for cursor in (original_cursor, asr_cursor):
            if type(cursor) is not int or cursor < 0:
                raise ValueError("extraction cursors must be non-negative integers")
        self._clear()
        self._generation += 1
        handle = ExtractionHandle(stream, profile_generation, model_generation, config_generation, self._generation)
        self._handle = handle
        self._buffer_start = self._extracted_cursor = original_cursor
        self._authorized_cursor = self._emitted_cursor = original_cursor
        self._asr_cursor = asr_cursor
        self._last_segment = 0
        self._end_cursor = None
        self._completed = False
        self._failure_reason = None
        return handle

    def append_extracted(
        self, handle: ExtractionHandle, original_range: SampleRange, pcm16: bytes,
    ) -> tuple[ExtractedPrewireEvent, ...]:
        """Accept strictly consecutive TSE output, independent of gate timing."""
        self._require_current(handle)
        if (type(original_range) is not SampleRange or type(pcm16) is not bytes
                or len(pcm16) != original_range.sample_count * 2):
            self._fail("extraction_pcm_range_mismatch")
        if original_range.start != self._extracted_cursor:
            self._fail("extraction_output_gap_overlap_or_reorder")
        if self._end_cursor is not None and original_range.end > self._end_cursor:
            self._fail("extraction_output_exceeds_capture_end")
        # A prior gap decision may already have consumed this original range.
        # The TSE stream still processes it continuously, but we retain no audio.
        retained_start = max(original_range.start, self._emitted_cursor)
        retained_count = max(0, original_range.end - retained_start)
        if len(self._pcm) + retained_count * 2 > self._max_bytes:
            self._fail("extraction_pcm_capacity", capacity=True)
        if retained_count:
            if not self._pcm:
                self._buffer_start = retained_start
            self._pcm.extend(pcm16[(retained_start - original_range.start) * 2:])
        self._extracted_cursor = original_range.end
        return self._drain()

    def accept_event(
        self, handle: ExtractionHandle, event: PrewireAudioEvent | PrewireGapEvent | PrewireEndEvent,
    ) -> tuple[ExtractedPrewireEvent, ...]:
        """Consume a trusted gate event once; original event audio is discarded."""
        self._require_current(handle)
        if type(event) not in (PrewireAudioEvent, PrewireGapEvent, PrewireEndEvent):
            self._fail("unsupported_prewire_event")
        if self._end_cursor is not None:
            self._fail("gate_event_after_capture_end")
        if type(event) is PrewireEndEvent:
            if event.stream != handle.stream:
                raise ExtractionIdentityError("foreign_capture_end")
            if (type(event.original_cursor) is not int or type(event.asr_cursor) is not int
                    or event.original_cursor != self._authorized_cursor
                    or event.asr_cursor != self._asr_cursor
                    or self._extracted_cursor > event.original_cursor):
                self._fail("capture_end_cursor_mismatch")
            pending = event
        else:
            self._require_event_identity(handle, event.identity)
            original = event.original_range
            if (type(original) is not SampleRange
                    or not event.identity.original_range.contains(original)
                    or original.start != self._authorized_cursor
                    or event.identity.segment_id <= self._last_segment):
                self._fail("gate_range_gap_overlap_or_replay")
            if type(event) is PrewireAudioEvent:
                if (type(event.asr_range) is not SampleRange
                        or event.asr_range.start != self._asr_cursor
                        or event.asr_range.sample_count != original.sample_count
                        or type(event.pcm16) is not bytes or len(event.pcm16) != original.sample_count * 2):
                    self._fail("gate_audio_mapping_mismatch")
                # One authorized event is emitted atomically; reject a request
                # that can never fit even when all of its TSE samples arrive.
                if original.sample_count * 2 > self._max_bytes:
                    self._fail("authorized_range_exceeds_pcm_capacity", capacity=True)
                pending = _AuthorizedRange(event.identity, original, event.asr_range)
            else:
                if (type(event.decision) is not PrewireDecisionState
                        or event.decision not in {PrewireDecisionState.DROP, PrewireDecisionState.UNCERTAIN,
                        PrewireDecisionState.UNAVAILABLE, PrewireDecisionState.STALE}
                        or type(event.reason) is not str or not event.reason.strip()):
                    self._fail("gate_gap_is_not_a_terminal_decision")
                pending = event
        if len(self._pending) >= self._max_events:
            self._fail("extraction_event_capacity", capacity=True)
        self._pending.append(pending)
        if type(event) is PrewireEndEvent:
            self._end_cursor = event.original_cursor
        else:
            self._authorized_cursor = event.original_range.end
            self._last_segment = event.identity.segment_id
            if type(event) is PrewireAudioEvent:
                self._asr_cursor = event.asr_range.end
        return self._drain()

    def _require_current(self, handle: ExtractionHandle) -> None:
        if type(handle) is not ExtractionHandle or self._closed or handle is not self._handle or self._completed:
            raise ExtractionIdentityError("extraction_handle_retired_or_foreign")

    @staticmethod
    def _require_event_identity(handle: ExtractionHandle, identity: PrewireIntervalIdentity) -> None:
        if (type(identity) is not PrewireIntervalIdentity or identity.stream != handle.stream
                or identity.profile_generation != handle.profile_generation
                or identity.model_generation != handle.model_generation
                or identity.config_generation != handle.config_generation):
            raise ExtractionIdentityError("gate_event_version_or_stream_mismatch")

    def _drain(self) -> tuple[ExtractedPrewireEvent, ...]:
        ready: list[ExtractedPrewireEvent] = []
        while self._pending:
            pending = self._pending[0]
            if type(pending) is PrewireEndEvent:
                if self._extracted_cursor < pending.original_cursor:
                    break
                self._pending.popleft()
                self._completed = True
                self._clear_pcm()
                ready.append(pending)
                break
            if type(pending) is _AuthorizedRange:
                if self._extracted_cursor < pending.original_range.end:
                    break
                start = (pending.original_range.start - self._buffer_start) * 2
                end = start + pending.original_range.sample_count * 2
                if start < 0 or end > len(self._pcm):
                    self._fail("authorized_extraction_range_not_retained")
                ready.append(ExtractedPrewireAudio(
                    pending.identity, pending.original_range, pending.asr_range, bytes(self._pcm[start:end]),
                ))
            else:
                ready.append(pending)
            self._pending.popleft()
            self._emitted_cursor = pending.original_range.end
            self._discard_through(self._emitted_cursor)
        return tuple(ready)

    def _discard_through(self, cursor: int) -> None:
        count = min(len(self._pcm), max(0, cursor - self._buffer_start) * 2)
        if count:
            self._pcm[:count] = bytes(count)
            del self._pcm[:count]
            self._buffer_start += count // 2

    def _clear_pcm(self) -> None:
        self._pcm[:] = bytes(len(self._pcm))
        self._pcm.clear()

    def _clear(self) -> None:
        self._clear_pcm()
        self._pending.clear()

    def _fail(self, reason: str, *, capacity: bool = False) -> None:
        self._failure_reason = reason
        self._handle = None
        self._clear()
        error = ExtractionCapacityError if capacity else ExtractionAlignmentError
        raise error(reason)

    def retire(self, handle: ExtractionHandle) -> bool:
        """Fence and discard the current owner; a late retirement is harmless."""
        if type(handle) is not ExtractionHandle or handle is not self._handle:
            return False
        self._handle = None
        self._clear()
        return True

    def close(self) -> None:
        """Permanently stop admission and release all adapter-owned buffers."""
        self._closed = True
        self._handle = None
        self._clear()
