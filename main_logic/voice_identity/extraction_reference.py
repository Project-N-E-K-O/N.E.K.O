"""Owned, unnormalized speaker conditioning with explicit provenance."""

from __future__ import annotations

from dataclasses import dataclass
import threading

import numpy as np

from .contracts import SpeakerModelIdentity


@dataclass(frozen=True, slots=True)
class SpeakerExtractionReferenceContract:
    resource_revision: str
    preprocessing_revision: str
    reference_method: str
    sample_rate_hz: int
    noise_reduction_enabled: bool

    def __post_init__(self) -> None:
        if any(type(v) is not str or not v.strip() for v in (
            self.resource_revision, self.preprocessing_revision, self.reference_method,
        )):
            raise ValueError("extraction reference revisions must be non-empty")
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz <= 0:
            raise ValueError("invalid extraction sample rate")
        if type(self.noise_reduction_enabled) is not bool:
            raise TypeError("extraction noise reduction must be bool")


class SpeakerExtractionReference:
    """Preserve the encoder's raw vector; identity embeddings normalize separately."""

    __slots__ = ("_embedding", "_identity", "_lock", "_closed")

    def __init__(self, identity: SpeakerModelIdentity, embedding: np.ndarray) -> None:
        if type(identity) is not SpeakerModelIdentity:
            raise TypeError("identity must be SpeakerModelIdentity")
        if np.iscomplexobj(embedding):
            raise ValueError("embedding must be real-valued")
        owned = np.array(embedding, dtype=np.float32, order="C", copy=True)
        if (owned.shape != (identity.embedding_dimension,)
                or not np.isfinite(owned).all() or not np.any(owned)):
            owned.fill(0)
            raise ValueError("invalid extraction embedding")
        self._embedding = owned
        self._identity = identity
        self._lock = threading.Lock()
        self._closed = False

    @property
    def model_identity(self) -> SpeakerModelIdentity:
        with self._lock:
            self._require_open()
            return self._identity

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def copy_embedding(self) -> np.ndarray:
        with self._lock:
            self._require_open()
            return self._embedding.copy()

    def clone(self) -> SpeakerExtractionReference:
        owned = self.copy_embedding()
        try:
            return SpeakerExtractionReference(self._identity, owned)
        finally:
            owned.fill(0)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._embedding.fill(0)

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("extraction reference is closed")

    def __repr__(self) -> str:
        return f"SpeakerExtractionReference(closed={self.closed})"

    def __reduce__(self):
        raise TypeError("SpeakerExtractionReference must not be pickled")

    def __copy__(self):
        return self.clone()

    def __deepcopy__(self, memo):
        return self.clone()
