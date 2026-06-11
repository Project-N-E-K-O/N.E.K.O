# -*- coding: utf-8 -*-
"""Import-time fallback stubs for :mod:`memory.embeddings`.

This module exists so that if ``memory/embeddings.py`` is quarantined or
deleted by an external actor (over-eager antivirus has done both: a
historical low-level CPU probe was flagged as a trojan and the file was
removed on disk while N.E.K.O was running), the memory
subsystem still imports successfully and degrades to the pre-vector code
path instead of crashing with ``ImportError`` at module load.

The signatures mirror the real ``memory.embeddings`` module's public
surface. Every function answers exactly the way the real implementation
would answer when its :class:`EmbeddingService` is in the sticky
``DISABLED`` state — that path is already covered by the existing
"vectors disabled → no-op" branches in the rest of the memory
pipeline, so consumers don't need any extra ``hasattr`` guards.

Kept intentionally pure-Python with no imports beyond ``logging`` and no
low-level CPU-probing tricks, so AV heuristic scanners have nothing to
latch onto here.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── service stub ──────────────────────────────────────────────────────


class _DisabledEmbeddingService:
    """Permanently-disabled twin of :class:`memory.embeddings.EmbeddingService`.

    Every consumer reaches into the service via ``is_available()`` first
    (the gate documented at the top of ``embeddings.py``), so as long as
    that returns ``False`` everything downstream takes the pre-vector
    fallback path. The other methods are present so a stray call site
    we missed still gets a safe answer instead of ``AttributeError``.
    """

    _DISABLE_REASON = "embeddings_module_missing"

    def is_available(self) -> bool:
        return False

    def is_disabled(self) -> bool:
        return True

    def disable_reason(self) -> str:
        return self._DISABLE_REASON

    def model_id(self) -> str | None:
        return None

    def dim(self) -> int | None:
        return None

    def quantization(self) -> str | None:
        return None

    def ram_gb(self) -> float | None:
        return None

    def has_vnni(self) -> bool:
        return False

    async def request_load(self) -> bool:
        return False

    async def embed(self, text: str) -> list[float] | None:
        return None

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        return [None] * len(texts) if texts else []


_SERVICE_SINGLETON = _DisabledEmbeddingService()


def get_embedding_service() -> _DisabledEmbeddingService:
    return _SERVICE_SINGLETON


def reset_embedding_service_for_tests() -> None:  # pragma: no cover — stub
    """No-op in the stub: there's nothing to reset and tests targeting
    the real module won't import this file."""
    return None


# ── cache-helper stubs ────────────────────────────────────────────────


def clear_embedding_fields(entry: dict) -> None:
    if not isinstance(entry, dict):
        return
    entry["embedding"] = None
    entry["embedding_text_sha256"] = None
    entry["embedding_model_id"] = None


def stamp_embedding_fields(  # pragma: no cover — never reached without service
    entry: dict, vector, text: str, model_id: str,
) -> None:
    """No-op: the disabled service never produces a vector to stamp."""
    return None


def is_cached_embedding_valid(
    entry: dict, current_text: str, current_model_id: str | None,
) -> bool:
    return False


# ── decode helpers ────────────────────────────────────────────────────


def decode_embedding(emb: Any):
    """Always returns ``None`` — without the real encoder we can't tell
    a valid base64 fp16 payload from a legacy ``list[float]`` row, so
    we refuse to decode and let the cosine path treat the candidate as
    unembedded."""
    return None


def cosine_similarity(a: Any, b: Any) -> float:  # noqa: ARG001
    """Always 0.0 — consumers fall back to non-vector ranking signals."""
    return 0.0


def parse_dim_from_model_id(model_id: str | None) -> int | None:  # noqa: ARG001
    """The real implementation parses a stamped model_id back to its
    dim; with no real service nothing should ever carry a usable
    model_id, so we always return ``None`` here."""
    return None


def _warn_once(consumer_module: str) -> None:
    """Helper for the four top-level import sites to log a single
    warning when they fall back to this stub. Importers call this from
    inside their ``except ImportError`` block; the gate avoids spamming
    on every reload of the consuming module."""
    if getattr(_warn_once, "_seen", None) is None:
        _warn_once._seen = set()  # type: ignore[attr-defined]
    seen: set = _warn_once._seen  # type: ignore[attr-defined]
    if consumer_module in seen:
        return
    seen.add(consumer_module)
    logger.warning(
        "memory.embeddings is unavailable; %s falling back to disabled "
        "stub — local vector recall, fact dedup and refine clustering "
        "will skip the embedding path until the real module is restored "
        "(check antivirus quarantine if the file vanished from disk)",
        consumer_module,
    )
