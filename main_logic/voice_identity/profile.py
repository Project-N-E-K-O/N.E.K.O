"""In-memory speaker profiles with explicit reference ownership."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .contracts import SpeakerModelIdentity
from .reference import SpeakerReference
from .extraction_reference import SpeakerExtractionReference, SpeakerExtractionReferenceContract


@dataclass(frozen=True, slots=True)
class SpeakerActivityReferenceContract:
    """Immutable provenance for a separately owned activity reference."""

    resource_revision: str
    preprocessing_revision: str
    reference_method: str
    sample_rate_hz: int
    noise_reduction_enabled: bool

    def __post_init__(self) -> None:
        for value in (
            self.resource_revision,
            self.preprocessing_revision,
            self.reference_method,
        ):
            if type(value) is not str or not value.strip():
                raise ValueError("activity reference revisions must be non-empty strings")
        if type(self.sample_rate_hz) is not int or self.sample_rate_hz <= 0:
            raise ValueError("activity reference sample rate must be positive")
        if type(self.noise_reduction_enabled) is not bool:
            raise TypeError("activity reference noise reduction must be bool")


class SpeakerProfile:
    """Own a reference clone under a caller-supplied opaque generation."""

    __slots__ = (
        "_closed", "_generation", "_lock", "_reference", "_activity_reference",
        "_activity_reference_contract",
        "_extraction_reference", "_extraction_reference_contract",
    )

    def __init__(
        self,
        generation: str,
        reference: SpeakerReference,
        *,
        activity_reference: SpeakerReference | None = None,
        activity_reference_contract: SpeakerActivityReferenceContract | None = None,
        extraction_reference: SpeakerExtractionReference | None = None,
        extraction_reference_contract: SpeakerExtractionReferenceContract | None = None,
    ) -> None:
        if type(generation) is not str or not generation.strip():
            raise ValueError("generation must be a non-empty string")
        if type(reference) is not SpeakerReference:
            raise TypeError("reference must be SpeakerReference")
        if activity_reference is not None and type(activity_reference) is not SpeakerReference:
            raise TypeError("activity_reference must be SpeakerReference or None")
        if (activity_reference is None) != (activity_reference_contract is None):
            raise ValueError("activity reference and contract must be provided together")
        if (
            activity_reference_contract is not None
            and type(activity_reference_contract) is not SpeakerActivityReferenceContract
        ):
            raise TypeError("activity reference contract must be concrete")

        if (extraction_reference is None) != (extraction_reference_contract is None):
            raise ValueError("extraction reference and contract must be provided together")
        if extraction_reference is not None and type(extraction_reference) is not SpeakerExtractionReference:
            raise TypeError("invalid extraction reference")
        if extraction_reference_contract is not None and type(extraction_reference_contract) is not SpeakerExtractionReferenceContract:
            raise TypeError("invalid extraction reference contract")
        self._generation = generation
        self._lock = threading.Lock()
        self._closed = False
        self._activity_reference = None
        self._activity_reference_contract = activity_reference_contract
        self._extraction_reference = None
        self._extraction_reference_contract = extraction_reference_contract
        cloned_reference: SpeakerReference | None = None
        try:
            cloned_reference = reference.clone()
            self._reference = cloned_reference
            if activity_reference is not None:
                self._activity_reference = activity_reference.clone()
            if extraction_reference is not None:
                self._extraction_reference = extraction_reference.clone()
            return
        except BaseException:
            if self._activity_reference is not None:
                self._activity_reference.close()
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

    def clone_activity_reference(self) -> SpeakerReference | None:
        with self._lock:
            self._require_open()
            return None if self._activity_reference is None else self._activity_reference.clone()

    @property
    def has_activity_reference(self) -> bool:
        with self._lock:
            self._require_open()
            return self._activity_reference is not None

    @property
    def activity_reference_contract(self) -> SpeakerActivityReferenceContract | None:
        with self._lock:
            self._require_open()
            return self._activity_reference_contract

    def __copy__(self) -> SpeakerProfile:
        return self._clone()

    def clone_extraction_reference(self) -> SpeakerExtractionReference | None:
        with self._lock:
            self._require_open()
            return None if self._extraction_reference is None else self._extraction_reference.clone()

    @property
    def has_extraction_reference(self) -> bool:
        with self._lock:
            self._require_open()
            return self._extraction_reference is not None

    @property
    def extraction_reference_contract(self) -> SpeakerExtractionReferenceContract | None:
        with self._lock:
            self._require_open()
            return self._extraction_reference_contract

    def __deepcopy__(self, memo: dict[int, object]) -> SpeakerProfile:
        del memo
        return self._clone()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            first_error: BaseException | None = None
            for reference in (self._extraction_reference, self._activity_reference, self._reference):
                if reference is None or reference.closed:
                    continue
                try:
                    reference.close()
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            self._closed = self._reference.closed and (
                self._activity_reference is None or self._activity_reference.closed
            ) and (
                self._extraction_reference is None or self._extraction_reference.closed
            )
            if first_error is not None:
                raise first_error

    def __repr__(self) -> str:
        with self._lock:
            return f"SpeakerProfile(generation={self._generation!r}, closed={self._closed})"

    def _require_open(self) -> None:
        if (
            self._closed
            or self._reference.closed
            or (self._activity_reference is not None and self._activity_reference.closed)
            or (self._extraction_reference is not None and self._extraction_reference.closed)
        ):
            raise RuntimeError("speaker profile is closed")

    def _clone(self) -> SpeakerProfile:
        with self._lock:
            self._require_open()
            clone = object.__new__(SpeakerProfile)
            clone._generation = self._generation
            clone._lock = threading.Lock()
            clone._closed = False
            clone._activity_reference = None
            clone._activity_reference_contract = self._activity_reference_contract
            clone._extraction_reference = None
            clone._extraction_reference_contract = self._extraction_reference_contract
            cloned_reference: SpeakerReference | None = None
            try:
                cloned_reference = self._reference.clone()
                clone._reference = cloned_reference
                if self._activity_reference is not None:
                    clone._activity_reference = self._activity_reference.clone()
                if self._extraction_reference is not None:
                    clone._extraction_reference = self._extraction_reference.clone()
                return clone
            except BaseException:
                if clone._activity_reference is not None:
                    clone._activity_reference.close()
                if cloned_reference is not None:
                    cloned_reference.close()
                raise
