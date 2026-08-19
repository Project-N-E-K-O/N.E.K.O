"""Internal execution primitives for generic local knowledge."""

from .models import KnowledgeEntry, KnowledgeHit, UpsertResult
from .retrieval import KnowledgeMentionMatcher, KnowledgeRetriever
from .store import (
    KnowledgeStore,
    KnowledgeStoreError,
    publish_database_changed,
    register_database_change_listener,
    unregister_database_change_listener,
)

__all__ = [
    "KnowledgeEntry",
    "KnowledgeHit",
    "KnowledgeMentionMatcher",
    "KnowledgeRetriever",
    "KnowledgeStore",
    "KnowledgeStoreError",
    "UpsertResult",
    "publish_database_changed",
    "register_database_change_listener",
    "unregister_database_change_listener",
]
