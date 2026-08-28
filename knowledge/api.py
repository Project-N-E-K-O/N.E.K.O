"""Stable public prefix for local conversational knowledge."""

from __future__ import annotations

from pathlib import Path

from .models import KnowledgeEntry, KnowledgeHit, UpsertResult
from .retrieval import (
    KnowledgeMentionMatcher,
    KnowledgeRetriever,
    MatchPolicy,
)
from .store import KnowledgeSchemaTooNewError, KnowledgeStore, KnowledgeStoreError
from .packs import KnowledgePack, KnowledgePackSource, PackInstallResult
from .prebuilt_index import (
    MAX_PREBUILT_CHUNKS,
    MAX_PREBUILT_MANIFEST_BYTES,
    MAX_PREBUILT_VECTOR_BYTES,
    PREBUILT_DIMENSIONS,
    PREBUILT_ENCODING,
    PREBUILT_INDEX_SCHEMA_VERSION,
    PREBUILT_MODEL_ID,
    PREBUILT_VECTOR_ROW_BYTES,
    PrebuiltChunkReference,
    PrebuiltIndexArtifacts,
    ValidatedPrebuiltIndex,
    build_prebuilt_index_artifacts,
    canonical_prebuilt_manifest_bytes,
    validate_prebuilt_index,
)
from .subscriptions import (
    SUBSCRIPTION_PROTOCOL_VERSION,
    KnowledgeSubscription,
    canonical_pack_bytes,
    load_canonical_pack_artifact,
    validate_subscription,
)
from .service import (
    KnowledgeService,
    KnowledgeTurnContext,
    ResponsePolicy,
)


def open_knowledge(knowledge_root: str | Path) -> KnowledgeService:
    """Open the local service without starting tasks or accessing the network."""
    return KnowledgeService.from_root(knowledge_root)


__all__ = [
    "KnowledgeEntry",
    "KnowledgeHit",
    "KnowledgeMentionMatcher",
    "KnowledgePack",
    "KnowledgePackSource",
    "KnowledgeRetriever",
    "KnowledgeService",
    "KnowledgeStore",
    "KnowledgeStoreError",
    "KnowledgeSchemaTooNewError",
    "KnowledgeSubscription",
    "KnowledgeTurnContext",
    "MatchPolicy",
    "MAX_PREBUILT_CHUNKS",
    "MAX_PREBUILT_MANIFEST_BYTES",
    "MAX_PREBUILT_VECTOR_BYTES",
    "PackInstallResult",
    "PREBUILT_DIMENSIONS",
    "PREBUILT_ENCODING",
    "PREBUILT_INDEX_SCHEMA_VERSION",
    "PREBUILT_MODEL_ID",
    "PREBUILT_VECTOR_ROW_BYTES",
    "PrebuiltChunkReference",
    "PrebuiltIndexArtifacts",
    "ResponsePolicy",
    "SUBSCRIPTION_PROTOCOL_VERSION",
    "UpsertResult",
    "ValidatedPrebuiltIndex",
    "build_prebuilt_index_artifacts",
    "canonical_pack_bytes",
    "canonical_prebuilt_manifest_bytes",
    "load_canonical_pack_artifact",
    "open_knowledge",
    "validate_prebuilt_index",
    "validate_subscription",
]
