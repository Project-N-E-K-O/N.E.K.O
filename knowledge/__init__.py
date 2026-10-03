"""Local knowledge, strictly separate from user and character memory."""

from __future__ import annotations


_PUBLIC_NAMES = frozenset(
    {
        "KnowledgeEntry",
        "KnowledgeRetriever",
        "KnowledgeService",
        "KnowledgeStore",
        "KnowledgeTurnContext",
        "open_knowledge",
    }
)

__all__ = [
    "KnowledgeEntry",
    "KnowledgeRetriever",
    "KnowledgeService",
    "KnowledgeStore",
    "KnowledgeTurnContext",
    "open_knowledge",
]


def __getattr__(name: str):
    """Keep lightweight knowledge submodules from loading the full service graph."""
    if name not in _PUBLIC_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import api

    value = getattr(api, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *_PUBLIC_NAMES))
