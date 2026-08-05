"""Composition root for trusted built-in knowledge domains."""

from __future__ import annotations

from pathlib import Path

from .corpora import CORPORA_COLLECTION
from .moegirl_knowledge import MEME_COLLECTION
from .service import KnowledgeService


BUILTIN_COLLECTIONS = (MEME_COLLECTION, CORPORA_COLLECTION)


def open_builtin_knowledge(knowledge_root: str | Path) -> KnowledgeService:
    """Open built-in policies without importing data or starting background work."""
    return KnowledgeService(knowledge_root, collections=BUILTIN_COLLECTIONS)


__all__ = ["BUILTIN_COLLECTIONS", "open_builtin_knowledge"]
