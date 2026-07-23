"""In-memory voice identity profile without PCM, paths, or persistent identity."""

from __future__ import annotations

import math

import numpy as np


class SpeakerProfile:
    """Normalized defensive-copy reference embedding and model identity."""

    def __init__(
        self,
        reference_embedding: np.ndarray,
        *,
        profile_revision: int,
        model_id: str,
        model_revision: str,
        embedding_dimension: int,
    ) -> None:
        if profile_revision < 0:
            raise ValueError("profile_revision must not be negative")
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive")
        embedding = np.array(reference_embedding, dtype=np.float32, copy=True)
        if embedding.shape != (embedding_dimension,):
            raise ValueError(
                f"reference embedding must have shape ({embedding_dimension},)"
            )
        if not np.isfinite(embedding).all():
            raise ValueError("reference embedding must contain only finite values")
        norm = float(np.linalg.norm(embedding))
        if not math.isfinite(norm) or norm <= 1e-12:
            raise ValueError("reference embedding must have a non-zero L2 norm")
        embedding /= np.float32(norm)
        self._reference_embedding = embedding
        self._profile_revision = int(profile_revision)
        self._model_id = str(model_id)
        self._model_revision = str(model_revision)
        self._embedding_dimension = int(embedding_dimension)
        self._closed = False

    @property
    def reference_embedding(self) -> np.ndarray:
        if self._closed:
            raise RuntimeError("speaker profile is closed")
        return np.array(self._reference_embedding, dtype=np.float32, copy=True)

    @property
    def profile_revision(self) -> int:
        return self._profile_revision

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_revision(self) -> str:
        return self._model_revision

    @property
    def embedding_dimension(self) -> int:
        return self._embedding_dimension

    def close(self) -> None:
        if self._closed:
            return
        self._reference_embedding.fill(0)
        self._closed = True
