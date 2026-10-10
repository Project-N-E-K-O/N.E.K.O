"""Bounded, metadata-only transport evidence owned by one worker queue.

An attempt is deliberately not an acknowledgement. Exceptions/cancellation
after entering send leave an attempted write with an unknown remote result.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field

from main_logic.voice_turn.interception_events import InterceptionDeliveryStage
from main_logic.voice_turn.audio_delivery import AudioDeliverySpan, AudioDeliveryTag

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TransportDeliveryEvidence:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    attempted: bool = False
    written_audio_bytes: int = 0
    protected: bool = False
    pending_intervals: dict[tuple[int, int, int | None], list[AudioDeliverySpan]] = field(default_factory=dict)
    attempted_intervals: set[AudioDeliverySpan] = field(default_factory=set)
    interval_lock: threading.RLock = field(default_factory=threading.RLock)
    retired_tags: set[AudioDeliveryTag] = field(default_factory=set)


def register_interval_delivery(
    queue: object, key: tuple[int, int, int | None], spans: tuple[AudioDeliverySpan, ...],
) -> None:
    """Register explicit source sidecars at atomic queue admission."""
    if not spans:
        return
    evidence = delivery_evidence(queue)
    with evidence.interval_lock:
        count = sum(len(items) for items in evidence.pending_intervals.values())
        if any(span.tag.settled or span.tag in evidence.retired_tags for span in spans):
            raise RuntimeError("ASR_DELIVERY_RETIRED: source interval cannot be replayed")
        if count + len(spans) + len(evidence.retired_tags) > 4096:
            raise RuntimeError("ASR_DELIVERY_METADATA_CAPACITY")
        evidence.protected = True
        evidence.pending_intervals.setdefault(key, []).extend(spans)
    for span in spans:
        span.observe(InterceptionDeliveryStage.QUEUED)


def interval_delivery_spans(queue: object, key: tuple[int, int, int | None]) -> tuple[AudioDeliverySpan, ...]:
    """Snapshot the committed physical request's exact source contributions."""
    evidence = delivery_evidence(queue)
    with evidence.interval_lock:
        return tuple(evidence.pending_intervals.get(key, ()))


def retire_interval_deliveries(
    queue: object, *, keep_scope: tuple[int, int] | None = None,
    only_spans: tuple[AudioDeliverySpan, ...] | None = None,
) -> None:
    """Dispose only this queue's records; an entered send remains unknown."""
    evidence = delivery_evidence(queue)
    outcomes = []
    selected = None if only_spans is None else set(only_spans)
    with evidence.interval_lock:
        for key in list(evidence.pending_intervals):
            if keep_scope is not None and key[:2] == keep_scope:
                continue
            retained = []
            for span in evidence.pending_intervals[key]:
                if selected is not None and span not in selected:
                    retained.append(span)
                    continue
                outcomes.append((span, InterceptionDeliveryStage.UNKNOWN if span in evidence.attempted_intervals
                                 else InterceptionDeliveryStage.NOT_SENT))
                evidence.retired_tags.add(span.tag)
                evidence.attempted_intervals.discard(span)
            if retained:
                evidence.pending_intervals[key] = retained
            else:
                del evidence.pending_intervals[key]
    for span, stage in outcomes:
        span.observe(stage)
        if span.tag.settled:
            # A complete NOT_SENT/transport-owned receipt leaves the replay
            # fence on the tag itself. Retaining another strong reference
            # here would exhaust a long-lived queue after settled captures.
            # Partial/unknown or rejected receipts keep their exact tombstone.
            # Late sends also require membership in pending_intervals, so
            # removing this duplicate fence cannot resurrect old spans.
            with evidence.interval_lock:
                evidence.retired_tags.discard(span.tag)


def delivery_evidence(queue: object) -> TransportDeliveryEvidence:
    evidence = getattr(queue, "_transport_delivery_evidence", None)
    if evidence is None:
        evidence = TransportDeliveryEvidence()
        setattr(queue, "_transport_delivery_evidence", evidence)
    return evidence


def begin_transport_write(
    queue: object, *, delivery_spans: tuple[AudioDeliverySpan, ...] = (),
) -> TransportDeliveryEvidence:
    evidence = delivery_evidence(queue)
    with evidence.interval_lock:
        if delivery_spans and evidence.protected:
            registered = {span for spans in evidence.pending_intervals.values() for span in spans}
            if not set(delivery_spans) <= registered or any(span.tag in evidence.retired_tags for span in delivery_spans):
                raise RuntimeError("ASR_DELIVERY_RETIRED: source contribution no longer owned")
        evidence.attempted = True
        evidence.attempted_intervals.update(delivery_spans)
    return evidence


def complete_transport_write(
    evidence: TransportDeliveryEvidence,
    audio_bytes: int,
    *,
    generation: int,
    buffer_epoch: int,
    provider: str,
    delivery_spans: tuple[AudioDeliverySpan, ...] = (),
    takes_ownership: bool = False,
) -> None:
    first = evidence.written_audio_bytes == 0
    evidence.written_audio_bytes += audio_bytes
    with evidence.interval_lock:
        registered = {span for spans in evidence.pending_intervals.values() for span in spans}
        # Cancellation/clear may retire this contribution while a native or
        # socket send is still returning. Its late success cannot resurrect
        # the retired interval or transfer its ownership to a successor.
        current_spans = tuple(span for span in delivery_spans
                              if span.tag not in evidence.retired_tags and (not evidence.protected or span in registered))
        for span in current_spans:
            evidence.attempted_intervals.discard(span)
        completed = set(current_spans)
        for key in list(evidence.pending_intervals):
            retained = [span for span in evidence.pending_intervals[key] if span not in completed]
            if retained:
                evidence.pending_intervals[key] = retained
            else:
                del evidence.pending_intervals[key]
    for span in current_spans:
        span.observe(InterceptionDeliveryStage.TRANSPORT_WRITTEN)
        if takes_ownership:
            span.observe(InterceptionDeliveryStage.TRANSPORT_OWNED)
    if first and audio_bytes:
        logger.info(
            "ASR delivery trace=%s phase=transport_written provider=%s generation=%s "
            "buffer_epoch=%s audio_bytes=%s",
            evidence.trace_id,
            provider,
            generation,
            buffer_epoch,
            audio_bytes,
        )


def log_delivery_phase(
    evidence: TransportDeliveryEvidence | None,
    *,
    phase: str,
    generation: int,
    buffer_epoch: int,
) -> None:
    logger.info(
        "ASR delivery trace=%s phase=%s generation=%s buffer_epoch=%s "
        "transport_written_audio_bytes=%s",
        evidence.trace_id if evidence else "unobserved",
        phase,
        generation,
        buffer_epoch,
        evidence.written_audio_bytes if evidence else None,
    )
