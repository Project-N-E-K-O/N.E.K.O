"""Provider-neutral contracts. No imports of chat owners or HTTP frameworks."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from config.topic_recommendation_settings import TopicRecommendationSettings

CHARACTER_ID_RE = re.compile(r"character_[0-9a-f]{32}\Z")
AVAILABILITIES = frozenset({"capability_disabled", "user_disabled", "waiting_context", "ready", "degraded", "maintenance", "closing"})
ASSESSMENTS = frozenset({"engaged", "disengaged", "unknown"})


class RecommendationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def empty_state(character_id: str) -> dict[str, Any]:
    if not isinstance(character_id, str) or not CHARACTER_ID_RE.fullmatch(character_id):
        raise RecommendationError("invalid_character_id")
    return {"schema_version": 1, "character_id": character_id, "state_epoch": uuid4().hex,
            "revision": 0, "subjects": [], "interests": [], "restrictions": [],
            "deliveries": [], "reset_requests": []}


def validate_state(state: dict, settings: TopicRecommendationSettings | None = None) -> dict:
    limits = settings or TopicRecommendationSettings()
    if not isinstance(state, dict) or type(state.get("schema_version")) is not int or state["schema_version"] != 1:
        raise RecommendationError("state_corrupt")
    if not CHARACTER_ID_RE.fullmatch(str(state.get("character_id", ""))):
        raise RecommendationError("state_corrupt")
    epoch = state.get("state_epoch")
    if not isinstance(epoch, str) or not re.fullmatch(r"[0-9a-f]{32}", epoch):
        raise RecommendationError("state_corrupt")
    revision = state.get("revision")
    if type(revision) is not int or revision < 0:
        raise RecommendationError("state_corrupt")
    for key, limit in (("subjects", limits.max_subjects), ("interests", limits.max_interests),
                       ("restrictions", limits.max_restrictions), ("deliveries", limits.max_deliveries),
                       ("reset_requests", 16)):
        records = state.get(key)
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise RecommendationError("state_corrupt")
        if len(records) > limit:
            raise RecommendationError("capacity_exhausted")
    try:
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError):
        raise RecommendationError("state_corrupt") from None
    if len(encoded) > limits.max_state_bytes:
        raise RecommendationError("capacity_exhausted")
    return state


@dataclass(frozen=True)
class TurnEvidence:
    ref: str
    turn_id: str
    session_id: str
    actor: str
    text: str
    language: str
    captured_at: float
    watermark: int
    binding_generation: str


@dataclass(frozen=True)
class RecommendationSnapshot:
    character_id: str
    session_id: str
    binding_generation: str
    root_generation: str
    state_epoch: str
    revision: int
    enable_generation: int
    user_evidence_watermark: int
    candidates: tuple[dict[str, Any], ...]
    restrictions: tuple[dict[str, Any], ...]
    publication_watermark: int = 0


@dataclass(frozen=True)
class AnalysisResult:
    subjects: tuple[dict[str, Any], ...]
    feedback: tuple[dict[str, Any], ...] = ()
    restriction_revocations: tuple[dict[str, Any], ...] = ()
