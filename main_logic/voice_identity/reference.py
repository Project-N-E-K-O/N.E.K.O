"""Owned, normalized in-memory speaker references."""

from __future__ import annotations

import math
import threading

import numpy as np
import numpy.typing as npt

from .contracts import SpeakerModelIdentity


def _wipe(array: np.ndarray) -> None:
    """Best-effort zeroization for an owned array."""

    try:
        if not array.flags.writeable:
            array.setflags(write=True)
        array.fill(0.0)
    except Exception:
        pass


def _copy_model_identity(identity: SpeakerModelIdentity) -> SpeakerModelIdentity:
    return SpeakerModelIdentity(
        identity.model_id,
        identity.model_revision,
        identity.embedding_dimension,
    )


def _owned_normalized_embedding(
    model_identity: SpeakerModelIdentity,
    embedding: npt.ArrayLike,
) -> np.ndarray:
    """Materialize, validate, and normalize a caller-independent array."""

    try:
        materialized = np.array(embedding, order="C", copy=True)
    except MemoryError:
        raise
    except Exception:
        raise ValueError("embedding must be convertible to float32") from None

    owned: np.ndarray | None = None
    normalized: np.ndarray | None = None
    try:
        if np.iscomplexobj(materialized):
            raise ValueError("embedding must be real-valued")
        try:
            with np.errstate(over="ignore", invalid="ignore"):
                owned = np.array(
                    materialized,
                    dtype=np.float32,
                    order="C",
                    copy=True,
                )
        except MemoryError:
            raise
        except Exception:
            raise ValueError("embedding must be convertible to float32") from None

        if owned.ndim != 1:
            raise ValueError("embedding must be one-dimensional")
        if owned.shape[0] != model_identity.embedding_dimension:
            raise ValueError("embedding dimension does not match model identity")
        if not bool(np.all(np.isfinite(owned))):
            raise ValueError("embedding must contain only finite values")

        squared_norm = math.fsum(float(value) * float(value) for value in owned)
        norm = math.sqrt(squared_norm)
        if not math.isfinite(norm) or norm == 0.0:
            raise ValueError("embedding norm must be finite and non-zero")

        normalized = np.array(
            owned,
            dtype=np.float64,
            order="C",
            copy=True,
        )
        with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
            np.divide(normalized, norm, out=normalized)
        owned[:] = normalized
        if not bool(np.all(np.isfinite(owned))):
            raise ValueError("normalized embedding must contain only finite values")
        return owned
    except BaseException:
        if owned is not None:
            _wipe(owned)
        raise
    finally:
        if normalized is not None:
            _wipe(normalized)
        _wipe(materialized)


class SpeakerReference:
    """Own a normalized embedding and export only caller-owned copies."""

    __slots__ = ("_closed", "_embedding", "_lock", "_model_identity")

    def __init__(
        self,
        model_identity: SpeakerModelIdentity,
        embedding: npt.ArrayLike,
    ) -> None:
        if type(model_identity) is not SpeakerModelIdentity:
            raise TypeError("model_identity must be SpeakerModelIdentity")

        self._lock = threading.Lock()
        self._closed = False
        self._model_identity = _copy_model_identity(model_identity)
        self._embedding = _owned_normalized_embedding(
            self._model_identity,
            embedding,
        )

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def model_identity(self) -> SpeakerModelIdentity:
        with self._lock:
            self._require_open()
            return _copy_model_identity(self._model_identity)

    def clone(self) -> SpeakerReference:
        """Return an independently owned reference with the same value."""

        identity, embedding = self._copy_embedding()
        try:
            clone = object.__new__(SpeakerReference)
            clone._lock = threading.Lock()
            clone._closed = False
            clone._model_identity = identity
            clone._embedding = embedding
            return clone
        except BaseException:
            _wipe(embedding)
            raise

    def copy_embedding(self) -> np.ndarray:
        """Return an independent caller-owned embedding copy.

        Trusted in-process adapters are responsible for clearing this copy
        after use. This method is an ownership boundary, not a sandbox for
        untrusted Python code.
        """

        _, embedding = self._copy_embedding()
        return embedding

    def __copy__(self) -> SpeakerReference:
        return self.clone()

    def __deepcopy__(self, memo: dict[int, object]) -> SpeakerReference:
        del memo
        return self.clone()

    def __reduce__(self) -> object:
        raise TypeError("SpeakerReference must not be pickled")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("SpeakerReference must not be pickled")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            _wipe(self._embedding)

    def __repr__(self) -> str:
        with self._lock:
            return (
                "SpeakerReference("
                f"model_identity={self._model_identity!r}, closed={self._closed})"
            )

    def _copy_embedding(self) -> tuple[SpeakerModelIdentity, np.ndarray]:
        with self._lock:
            self._require_open()
            return (
                _copy_model_identity(self._model_identity),
                self._embedding.copy(order="C"),
            )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("speaker reference is closed")
