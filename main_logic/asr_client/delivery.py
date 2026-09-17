"""Bounded, metadata-only transport evidence owned by one worker queue.

An attempt is deliberately not an acknowledgement. Exceptions/cancellation
after entering send leave an attempted write with an unknown remote result.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TransportDeliveryEvidence:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    attempted: bool = False
    written_audio_bytes: int = 0
    protected: bool = False


def delivery_evidence(queue: object) -> TransportDeliveryEvidence:
    evidence = getattr(queue, "_transport_delivery_evidence", None)
    if evidence is None:
        evidence = TransportDeliveryEvidence()
        setattr(queue, "_transport_delivery_evidence", evidence)
    return evidence


def begin_transport_write(queue: object) -> TransportDeliveryEvidence:
    evidence = delivery_evidence(queue)
    evidence.attempted = True
    return evidence


def complete_transport_write(
    evidence: TransportDeliveryEvidence,
    audio_bytes: int,
    *,
    generation: int,
    buffer_epoch: int,
    provider: str,
) -> None:
    first = evidence.written_audio_bytes == 0
    evidence.written_audio_bytes += audio_bytes
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
