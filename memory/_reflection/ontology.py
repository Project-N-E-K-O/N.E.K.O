"""Reflection ontology constants and validation."""

from __future__ import annotations

from config import REFLECTION_TEXT_MAX_TOKENS
from utils.tokenize import count_tokens


RELATION_TYPES = {
    "preference": "偏好/喜好",
    "trait": "性格/特征",
    "habit": "习惯/日常",
    "identity": "身份/背景",
    "emotional": "情感状态",
    "boundary": "边界/禁忌",
    "self_awareness": "自我认知",
    "learned": "习得行为",
    "role_note": "角色备注",
    "dynamic": "互动模式",
    "milestone": "关系里程碑",
    "tension": "摩擦/冲突",
    "shared_memory": "共同记忆",
    "agreement": "约定/共识",
}

ENTITY_KINDS: dict[str, str] = {
    "master": "user",
    "neko": "character",
    "relationship": "relationship",
}

KIND_RELATION_MAP: dict[str, frozenset[str]] = {
    "user": frozenset(
        {"preference", "trait", "habit", "identity", "emotional", "boundary"}
    ),
    "character": frozenset({"self_awareness", "learned", "role_note"}),
    "relationship": frozenset(
        {"dynamic", "milestone", "tension", "shared_memory", "agreement"}
    ),
}

TEMPORAL_SCOPES = frozenset(
    {
        "pattern",
        "state",
        "episode",
        "past",
        "current",
        "ongoing",
    }
)

MAX_REFLECTION_TEXT_TOKENS = REFLECTION_TEXT_MAX_TOKENS


def entity_kind(entity: str) -> str:
    """Resolve an entity name to its ontology kind."""
    return ENTITY_KINDS.get(entity, "user")


def allowed_relation_types(entity: str) -> frozenset[str]:
    return KIND_RELATION_MAP.get(entity_kind(entity), frozenset())


def validate_reflection_ontology(
    entity: str,
    relation_type: str | None,
    temporal_scope: str | None,
    text: str,
) -> tuple[bool, str]:
    """Validate ontology metadata without mutating the reflection."""
    if relation_type is not None:
        if relation_type not in RELATION_TYPES:
            return False, f"unknown relation_type: {relation_type!r}"
        allowed = allowed_relation_types(entity)
        if relation_type not in allowed:
            return (
                False,
                f"{relation_type!r} not valid for entity {entity!r} "
                f"(kind={entity_kind(entity)!r})",
            )
    if temporal_scope is not None and temporal_scope not in TEMPORAL_SCOPES:
        return False, f"unknown temporal_scope: {temporal_scope!r}"
    n_tokens = count_tokens(text)
    if n_tokens > MAX_REFLECTION_TEXT_TOKENS:
        return False, f"text too long: {n_tokens} tokens (compound risk)"
    return True, "ok"
