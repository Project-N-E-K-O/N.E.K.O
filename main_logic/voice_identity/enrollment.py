"""Explicit in-memory enrollment shared by provider-neutral voice identity models."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

from .profile import SpeakerProfile


def build_in_memory_speaker_profile(
    model: Any,
    enrollment_pcm16: Sequence[bytes],
    *,
    sample_rate_hz: int,
    profile_revision: int,
    minimum_segments: int,
    maximum_segments: int,
    minimum_samples: int,
    embedding_dimension: int,
    model_id: str,
    model_revision: str,
    profile_factory: Callable[..., SpeakerProfile] = SpeakerProfile,
) -> SpeakerProfile:
    """Embed explicit PCM segments, mean them, and normalize only in memory."""

    segments = tuple(enrollment_pcm16)
    if not minimum_segments <= len(segments) <= maximum_segments:
        raise ValueError(
            f"speaker enrollment requires {minimum_segments} to "
            f"{maximum_segments} segments"
        )
    for segment in segments:
        if not isinstance(segment, bytes) or not segment or len(segment) % 2:
            raise ValueError("speaker enrollment requires valid PCM16LE segments")
        if len(segment) // 2 < minimum_samples:
            raise ValueError("each speaker enrollment segment is too short")
    embeddings: list[np.ndarray] = []
    try:
        for segment in segments:
            embedding = model.embedding_from_pcm16(
                segment,
                sample_rate_hz=sample_rate_hz,
            )
            vector = np.array(embedding, dtype=np.float32, copy=True)
            if vector.shape != (embedding_dimension,):
                raise ValueError("speaker enrollment embedding shape changed")
            embeddings.append(vector)
        mean_embedding = np.mean(
            np.stack(embeddings, axis=0),
            axis=0,
            dtype=np.float32,
        )
        return profile_factory(
            mean_embedding,
            profile_revision=profile_revision,
            model_id=model_id,
            model_revision=model_revision,
            embedding_dimension=embedding_dimension,
        )
    finally:
        for embedding in embeddings:
            embedding.fill(0)
