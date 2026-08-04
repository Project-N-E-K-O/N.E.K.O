"""In-memory speaker profiles with explicit reference ownership."""

from __future__ import annotations

import threading

from .contracts import SpeakerModelIdentity
from .reference import SpeakerReference


class SpeakerProfile:
    """Own a reference clone under a caller-supplied opaque generation."""

    __slots__ = ("_closed", "_generation", "_lock", "_reference")

    def __init__(self, generation: str, reference: SpeakerReference) -> None:
        if type(generation) is not str or not generation.strip():
            raise ValueError("generation must be a non-empty string")
        if type(reference) is not SpeakerReference:
            raise TypeError("reference must be SpeakerReference")

        self._generation = generation
        self._lock = threading.Lock()
        self._closed = False
        cloned_reference: SpeakerReference | None = None
        try:
            cloned_reference = reference.clone()
            self._reference = cloned_reference
            return
        except BaseException:
            if cloned_reference is not None:
                cloned_reference.close()
            raise

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def generation(self) -> str:
        with self._lock:
            self._require_open()
            return self._generation

    @property
    def model_identity(self) -> SpeakerModelIdentity:
        with self._lock:
            self._require_open()
            return self._reference.model_identity

    def clone_reference(self) -> SpeakerReference:
        with self._lock:
            self._require_open()
            return self._reference.clone()

    def __copy__(self) -> SpeakerProfile:
        return self._clone()

    def __deepcopy__(self, memo: dict[int, object]) -> SpeakerProfile:
        del memo
        return self._clone()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._reference.close()
                self._closed = True
            except BaseException:
                if self._reference.closed:
                    self._closed = True
                raise

    def __repr__(self) -> str:
        with self._lock:
            return f"SpeakerProfile(generation={self._generation!r}, closed={self._closed})"

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("speaker profile is closed")

    def _clone(self) -> SpeakerProfile:
        with self._lock:
            self._require_open()
            clone = object.__new__(SpeakerProfile)
            clone._generation = self._generation
            clone._lock = threading.Lock()
            clone._closed = False
            cloned_reference: SpeakerReference | None = None
            try:
                cloned_reference = self._reference.clone()
                clone._reference = cloned_reference
                return clone
            except BaseException:
                if cloned_reference is not None:
                    cloned_reference.close()
                raise
