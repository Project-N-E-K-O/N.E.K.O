"""Shared local ONNX text-embedding capability.

This module is the non-business API used outside the memory domain. Process
composition roots bind the existing implementation through a neutral provider
interface, so the knowledge runtime does not couple itself to Memory Server
APIs, storage, or recall logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol


class LocalEmbeddingService(Protocol):
    def is_available(self) -> bool: ...
    def is_disabled(self) -> bool: ...
    def disable_reason(self) -> str: ...
    def model_id(self) -> str | None: ...
    def dim(self) -> int | None: ...
    async def request_load(self) -> bool: ...
    async def embed(self, text: str) -> list[float] | None: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]: ...
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LocalEmbeddingStatus:
    state: str
    model_id: str = ""
    dimensions: int = 0
    disable_reason: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready" and bool(self.model_id) and self.dimensions > 0


class _UnavailableLocalEmbeddingService:
    """Safe fallback used until the application binds a concrete provider."""

    def is_available(self) -> bool:
        return False

    def is_disabled(self) -> bool:
        return True

    def disable_reason(self) -> str:
        return "provider_unconfigured"

    def model_id(self) -> str | None:
        return None

    def dim(self) -> int | None:
        return None

    async def request_load(self) -> bool:
        return False

    async def embed(self, text: str) -> list[float] | None:
        return None

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        return [None] * len(texts)

    async def close(self) -> None:
        return None


_ServiceFactory = Callable[[], LocalEmbeddingService]
_ReleaseCallback = Callable[[], Awaitable[None]]
_UNAVAILABLE_SERVICE = _UnavailableLocalEmbeddingService()
_service_factory: _ServiceFactory | None = None
_release_callback: _ReleaseCallback | None = None


def bind_local_embedding_provider(
    service_factory: _ServiceFactory,
    release_callback: _ReleaseCallback,
) -> None:
    """Bind the process-owned implementation at the application boundary."""
    global _service_factory, _release_callback
    _service_factory = service_factory
    _release_callback = release_callback


def reset_local_embedding_provider_for_tests() -> None:
    """Remove the process provider so isolated tests get the safe fallback."""
    global _service_factory, _release_callback
    _service_factory = None
    _release_callback = None


def get_local_embedding_service() -> LocalEmbeddingService:
    """Return the bound process service without depending on its owner."""
    factory = _service_factory
    return factory() if factory is not None else _UNAVAILABLE_SERVICE


def get_local_embedding_status() -> LocalEmbeddingStatus:
    service = get_local_embedding_service()
    if service.is_available():
        state = "ready"
    elif service.is_disabled():
        state = "disabled"
    else:
        state = "not_ready"
    return LocalEmbeddingStatus(
        state=state,
        model_id=service.model_id() or "",
        dimensions=int(service.dim() or 0),
        disable_reason=service.disable_reason() if service.is_disabled() else "",
    )


async def release_local_embedding_service() -> None:
    """Release the bound process singleton without exposing its owner."""
    callback = _release_callback
    if callback is not None:
        await callback()
