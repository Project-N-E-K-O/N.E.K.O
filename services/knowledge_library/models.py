"""Shared limits and data validation for the personal library domain."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class LibraryError(ValueError):
    """Expose a stable reason without leaking document content or local paths."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LibraryLimits:
    """Bound input and derived work; these are product limits, not benchmarks."""

    max_document_bytes: int = 16 * 1024 * 1024
    max_document_chars: int = 256_000
    max_pdf_pages: int = 40
    max_documents: int = 200
    max_library_chars: int = 2_000_000
    max_chunks: int = 20_000
    max_libraries: int = 50
    max_pending_jobs: int = 8


class EmbeddingAdapter(Protocol):
    """Host-owned computation; the domain never loads a memory singleton."""

    async def specification(self) -> dict: ...

    async def embed_batch(self, texts: list[str], *, expected_spec: dict | None = None) -> list[list[float]]: ...

    async def embed_background(self, texts: list[str], *, expected_spec: dict | None = None) -> list[list[float]]: ...

    async def plan_inputs(self, inputs: list[dict], *, expected_spec: dict | None = None) -> list[list[dict]]: ...


def new_id() -> str:
    """Generate a filesystem-safe, stable domain identifier."""
    return uuid.uuid4().hex


def checked_id(value: str) -> str:
    """Reject path input where callers must supply an opaque identifier."""
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", value):
        raise LibraryError("invalid_id")
    return value


def checked_name(value: str) -> str:
    """Validate display names without turning them into storage filenames."""
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise LibraryError("invalid_name")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        # JSON can represent lone surrogates, but durable UTF-8 metadata and
        # browser responses cannot. Reject before creating any managed data.
        raise LibraryError("invalid_name") from error
    return value.strip()


def now() -> str:
    """Return an unambiguous persisted UTC timestamp."""
    return datetime.now(timezone.utc).isoformat()


def digest(data: bytes) -> str:
    """Hash exact bytes used for preview confirmation and immutable versions."""
    return hashlib.sha256(data).hexdigest()
