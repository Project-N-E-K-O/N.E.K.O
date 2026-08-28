"""Bind the domain-neutral local embedding facade to this process runtime."""

from __future__ import annotations


def _get_embedding_service():
    try:
        from .embeddings import get_embedding_service
    except Exception:
        from .embeddings_fallback import get_embedding_service
    return get_embedding_service()


async def _release_embedding_service() -> None:
    try:
        from .embeddings import release_embedding_service
    except Exception:
        service = _get_embedding_service()
        close = getattr(service, "close", None)
        if close is not None:
            await close()
        return
    await release_embedding_service()


def bind_process_local_embedding_provider() -> None:
    """Compose the shared facade with the process-local implementation."""
    from utils.local_embedding_runtime import bind_local_embedding_provider

    bind_local_embedding_provider(
        _get_embedding_service,
        _release_embedding_service,
    )
