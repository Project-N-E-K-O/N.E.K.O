from __future__ import annotations

from enum import StrEnum
import hashlib
import math
import re
from typing import Any

from .models import json_copy


class KnowledgeCandidateType(StrEnum):
    TOPIC = "topic"
    EDGE = "edge"
    MISCONCEPTION = "misconception"
    QUESTION_TYPE = "question_type"


class KnowledgeCandidateStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    TRUSTED = "trusted"
    DEPRECATED = "deprecated"


class KnowledgeEvidenceType(StrEnum):
    MENTIONED = "mentioned"
    USED_IN_PROMPT = "used_in_prompt"
    USER_CONFIRMED = "user_confirmed"
    ANSWER_IMPROVED = "answer_improved"
    USER_REJECTED = "user_rejected"
    CONFLICT_DETECTED = "conflict_detected"
    DUPLICATE_DETECTED = "duplicate_detected"
    REVIEW_RETAINED = "review_retained"


_POSITIVE_EVENTS = {
    KnowledgeEvidenceType.MENTIONED.value,
    KnowledgeEvidenceType.USED_IN_PROMPT.value,
    KnowledgeEvidenceType.USER_CONFIRMED.value,
    KnowledgeEvidenceType.ANSWER_IMPROVED.value,
    KnowledgeEvidenceType.REVIEW_RETAINED.value,
}
_NEGATIVE_EVENTS = {
    KnowledgeEvidenceType.USER_REJECTED.value,
    KnowledgeEvidenceType.CONFLICT_DETECTED.value,
    KnowledgeEvidenceType.DUPLICATE_DETECTED.value,
}
_DEFAULT_WEIGHTS = {
    KnowledgeEvidenceType.MENTIONED.value: 0.2,
    KnowledgeEvidenceType.USED_IN_PROMPT.value: 0.35,
    KnowledgeEvidenceType.USER_CONFIRMED.value: 0.8,
    KnowledgeEvidenceType.ANSWER_IMPROVED.value: 1.0,
    KnowledgeEvidenceType.USER_REJECTED.value: -1.0,
    KnowledgeEvidenceType.CONFLICT_DETECTED.value: -1.0,
    KnowledgeEvidenceType.DUPLICATE_DETECTED.value: -0.45,
    KnowledgeEvidenceType.REVIEW_RETAINED.value: 0.75,
}
DEFAULT_EVIDENCE_RECOMPUTE_LIMIT = 5000
# Trusted candidates tolerate duplicate noise, but explicit rejection,
# conflicts, or this many total negative events deprecates them.
DEFAULT_TRUSTED_NEGATIVE_THRESHOLD = 3


def _normalized_key(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    return text[:120]


def _hash_key(value: object) -> str:
    normalized = _normalized_key(value)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12] if normalized else ""


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if not math.isfinite(value):
        return minimum
    return max(minimum, min(maximum, value))


class KnowledgeQualityStore:
    def __init__(
        self,
        store: Any,
        *,
        evidence_recompute_limit: int = DEFAULT_EVIDENCE_RECOMPUTE_LIMIT,
        trusted_negative_threshold: int = DEFAULT_TRUSTED_NEGATIVE_THRESHOLD,
    ) -> None:
        """Create a quality store.

        evidence_recompute_limit bounds score recomputation reads. trusted_negative_threshold controls
        how many negative evidence events can force a TRUSTED candidate to DEPRECATED.
        """
        self._store = store
        self._evidence_recompute_limit = max(1, int(evidence_recompute_limit or DEFAULT_EVIDENCE_RECOMPUTE_LIMIT))
        self._trusted_negative_threshold = max(1, int(trusted_negative_threshold or DEFAULT_TRUSTED_NEGATIVE_THRESHOLD))

    def upsert_candidate(
        self,
        item_type: str,
        payload: dict[str, Any],
        source: str,
        evidence_type: str = KnowledgeEvidenceType.MENTIONED.value,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_type = self._normalize_item_type(item_type)
        clean_payload = json_copy(payload or {})
        dedupe_key = self._dedupe_key(normalized_type, clean_payload)
        candidate = self._store.upsert_candidate_item(
            item_type=normalized_type,
            payload=clean_payload,
            source=str(source or "runtime"),
            dedupe_key=dedupe_key,
        )
        evidence_context = {
            **dict(context or {}),
            "source": str(source or "runtime"),
            "dedupe_key": dedupe_key,
        }
        self.add_evidence(
            candidate["id"],
            evidence_type,
            _DEFAULT_WEIGHTS.get(str(evidence_type), 0.2),
            evidence_context,
        )
        refreshed = self._store.get_candidate_item(candidate["id"])
        return refreshed or candidate

    def add_evidence(
        self,
        item_id: str,
        event_type: str,
        weight: float = 0.0,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = self._normalize_event_type(event_type)
        actual_weight = float(weight if weight is not None else _DEFAULT_WEIGHTS.get(event, 0.0))
        evidence = self._store.add_knowledge_evidence(
            item_id=item_id,
            event_type=event,
            weight=actual_weight,
            context=context or {},
        )
        self.recompute_score(item_id)
        return evidence

    def recompute_score(self, item_id: str) -> dict[str, Any]:
        item = self._store.get_candidate_item(item_id)
        if not item:
            raise KeyError(f"knowledge candidate not found: {item_id}")
        evidence = self._store.list_knowledge_evidence(item_id, limit=self._evidence_recompute_limit)
        score_parts = self._score_parts(item, evidence)
        status = self._next_status(item, score_parts)
        self._store.update_candidate_score_status(
            item_id=item_id,
            score=score_parts["score"],
            status=status,
            evidence_count=score_parts["evidence_count"],
            positive_count=score_parts["positive_count"],
            negative_count=score_parts["negative_count"],
            conflict_count=score_parts["conflict_count"],
        )
        return self._store.get_candidate_item(item_id) or item

    def list_candidates(
        self,
        statuses: tuple[str, ...] | list[str] | None = None,
        item_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_type = self._normalize_item_type(item_type) if item_type else None
        return self._store.list_candidate_items(statuses=statuses, item_type=normalized_type, limit=limit)

    def promote_or_deprecate(self, item_id: str) -> dict[str, Any]:
        return self.recompute_score(item_id)

    def detect_duplicate_or_conflict(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_type = self._normalize_item_type(payload.get("item_type") or payload.get("type") or "topic")
        body = dict(payload.get("payload") if isinstance(payload.get("payload"), dict) else payload)
        dedupe_key = self._dedupe_key(item_type, body)
        duplicate = self._store.get_candidate_by_key(item_type=item_type, dedupe_key=dedupe_key)
        conflict = None
        if item_type == KnowledgeCandidateType.EDGE.value:
            from_id = str(body.get("from_topic_id") or "").strip()
            to_id = str(body.get("to_topic_id") or "").strip()
            relation = str(body.get("relation") or "").strip()
            if from_id and to_id and relation:
                reverse_key = self._dedupe_key(
                    item_type,
                    {
                        "from_topic_id": to_id,
                        "to_topic_id": from_id,
                        "relation": relation,
                    },
                )
                conflict = self._store.get_candidate_by_key(item_type=item_type, dedupe_key=reverse_key)
        return {
            "dedupe_key": dedupe_key,
            "duplicate": duplicate or {},
            "conflict": conflict or {},
        }

    def status_summary(self, *, limit: int = 8) -> dict[str, Any]:
        counts = self._store.candidate_status_counts()
        recent = self._store.list_recent_knowledge_evidence(limit=limit)
        return {
            **counts,
            "recent_evidence": recent,
        }

    def prompt_evidence_summary(self, *, topic_id: str = "", limit: int = 8) -> list[dict[str, Any]]:
        rows = self.list_candidates(
            statuses=(KnowledgeCandidateStatus.ACTIVE.value, KnowledgeCandidateStatus.TRUSTED.value),
            limit=limit,
        )
        result: list[dict[str, Any]] = []
        topic_filter = str(topic_id or "").strip()
        for row in rows:
            payload = dict(row.get("payload") or {})
            if topic_filter:
                candidate_topic = str(
                    payload.get("topic_id")
                    or payload.get("from_topic_id")
                    or payload.get("to_topic_id")
                    or payload.get("id")
                    or ""
                )
                if candidate_topic and candidate_topic != topic_filter:
                    continue
            result.append(
                {
                    "id": row.get("id"),
                    "item_type": row.get("item_type"),
                    "status": row.get("status"),
                    "score": row.get("score"),
                    "evidence_count": row.get("evidence_count"),
                    "positive_count": row.get("positive_count"),
                    "conflict_count": row.get("conflict_count"),
                    "payload_summary": self._payload_summary(row.get("item_type"), payload),
                }
            )
        return result[: max(1, int(limit))]

    @staticmethod
    def _normalize_item_type(item_type: object) -> str:
        value = str(item_type or "").strip()
        allowed = {item.value for item in KnowledgeCandidateType}
        if value not in allowed:
            raise ValueError(f"unsupported knowledge candidate type: {value}")
        return value

    @staticmethod
    def _normalize_event_type(event_type: object) -> str:
        value = str(event_type or "").strip()
        allowed = {item.value for item in KnowledgeEvidenceType}
        if value not in allowed:
            raise ValueError(f"unsupported knowledge evidence type: {value}")
        return value

    @classmethod
    def _dedupe_key(cls, item_type: str, payload: dict[str, Any]) -> str:
        if item_type == KnowledgeCandidateType.TOPIC.value:
            subject = _normalized_key(payload.get("subject") or "general")
            name = _normalized_key(payload.get("name") or payload.get("topic") or payload.get("topic_id"))
            if not name:
                raise ValueError("topic candidate requires name/topic/topic_id")
            return f"{subject}:{name}"
        if item_type == KnowledgeCandidateType.EDGE.value:
            from_id = str(payload.get("from_topic_id") or "").strip()
            to_id = str(payload.get("to_topic_id") or "").strip()
            relation = _normalized_key(payload.get("relation"))
            if not from_id or not to_id or not relation:
                raise ValueError("edge candidate requires from_topic_id, to_topic_id, and relation")
            return f"{from_id}:{to_id}:{relation}"
        if item_type == KnowledgeCandidateType.MISCONCEPTION.value:
            topic_id = str(payload.get("topic_id") or "").strip()
            key = _normalized_key(payload.get("misconception_key") or payload.get("key"))
            if not key and payload.get("text"):
                key = _hash_key(payload.get("text"))
            if not topic_id or not key:
                raise ValueError("misconception candidate requires topic_id and misconception_key/key")
            return f"{topic_id}:{key}"
        if item_type == KnowledgeCandidateType.QUESTION_TYPE.value:
            topic_id = str(payload.get("topic_id") or "").strip()
            key = _normalized_key(payload.get("question_type_key") or payload.get("key") or payload.get("type"))
            if not key and payload.get("text"):
                key = _hash_key(payload.get("text"))
            if not topic_id or not key:
                raise ValueError("question_type candidate requires topic_id and question_type_key/key")
            return f"{topic_id}:{key}"
        raise ValueError(f"unsupported knowledge candidate type: {item_type}")

    @classmethod
    def _score_parts(cls, item: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        weight_sums: dict[str, float] = {}
        sources = {str(item.get("source") or "")}
        for event in evidence:
            event_type = str(event.get("event_type") or "")
            counts[event_type] = counts.get(event_type, 0) + 1
            weight_sums[event_type] = weight_sums.get(event_type, 0.0) + max(0.0, float(event.get("weight") or 0.0))
            context = event.get("context") if isinstance(event.get("context"), dict) else {}
            source = str(context.get("source") or "").strip()
            if source:
                sources.add(source)

        usage_signal = _clamp(
            (counts.get(KnowledgeEvidenceType.MENTIONED.value, 0) + counts.get(KnowledgeEvidenceType.USED_IN_PROMPT.value, 0))
            / 5.0
        )
        learning_gain = _clamp(weight_sums.get(KnowledgeEvidenceType.ANSWER_IMPROVED.value, 0.0) / 3.0)
        user_feedback = _clamp(weight_sums.get(KnowledgeEvidenceType.USER_CONFIRMED.value, 0.0) / 3.0)
        consistency_signal = _clamp((len({source for source in sources if source}) - 1) / 2.0)
        review_outcome = _clamp(weight_sums.get(KnowledgeEvidenceType.REVIEW_RETAINED.value, 0.0) / 2.0)
        conflict_count = counts.get(KnowledgeEvidenceType.CONFLICT_DETECTED.value, 0)
        duplicate_count = counts.get(KnowledgeEvidenceType.DUPLICATE_DETECTED.value, 0)
        rejected_count = counts.get(KnowledgeEvidenceType.USER_REJECTED.value, 0)
        penalty = (
            0.45 * rejected_count
            + 0.35 * conflict_count
            + 0.20 * duplicate_count
            + cls._payload_penalty(str(item.get("item_type") or ""), dict(item.get("payload") or {}))
        )
        score = (
            0.25 * usage_signal
            + 0.25 * learning_gain
            + 0.20 * user_feedback
            + 0.15 * consistency_signal
            + 0.15 * review_outcome
            - penalty
        )
        positive_count = sum(counts.get(event_type, 0) for event_type in _POSITIVE_EVENTS)
        negative_count = sum(counts.get(event_type, 0) for event_type in _NEGATIVE_EVENTS)
        return {
            "score": round(score, 4),
            "evidence_count": len(evidence),
            "positive_count": positive_count,
            "negative_count": negative_count,
            "conflict_count": conflict_count,
            "user_rejected_count": rejected_count,
        }

    @staticmethod
    def _payload_penalty(item_type: str, payload: dict[str, Any]) -> float:
        try:
            KnowledgeQualityStore._dedupe_key(item_type, payload)
        except ValueError:
            return 0.35
        for key in ("name", "topic", "misconception_key", "question_type_key", "key"):
            text = str(payload.get(key) or "")
            if len(text) > 80:
                return 0.15
        return 0.0

    def _next_status(self, item: dict[str, Any], score_parts: dict[str, Any]) -> str:
        current = str(item.get("status") or KnowledgeCandidateStatus.CANDIDATE.value)
        score = float(score_parts.get("score") or 0.0)
        evidence_count = int(score_parts.get("evidence_count") or 0)
        positive_count = int(score_parts.get("positive_count") or 0)
        negative_count = int(score_parts.get("negative_count") or 0)
        conflict_count = int(score_parts.get("conflict_count") or 0)
        user_rejected_count = int(score_parts.get("user_rejected_count") or 0)
        if current == KnowledgeCandidateStatus.TRUSTED.value:
            if user_rejected_count > 0 or negative_count >= self._trusted_negative_threshold:
                return KnowledgeCandidateStatus.DEPRECATED.value
            return current
        if score <= -0.20 or negative_count >= 2 or conflict_count >= 2:
            return KnowledgeCandidateStatus.DEPRECATED.value
        if score >= 0.72 and positive_count >= 4 and conflict_count == 0:
            return KnowledgeCandidateStatus.TRUSTED.value
        if score >= 0.35 and evidence_count >= 2:
            return KnowledgeCandidateStatus.ACTIVE.value
        return current if current in {KnowledgeCandidateStatus.ACTIVE.value, KnowledgeCandidateStatus.CANDIDATE.value} else KnowledgeCandidateStatus.CANDIDATE.value

    @staticmethod
    def _payload_summary(item_type: object, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(item_type or "")
        if kind == KnowledgeCandidateType.TOPIC.value:
            return {"subject": payload.get("subject"), "topic_id": payload.get("topic_id") or payload.get("id")}
        if kind == KnowledgeCandidateType.EDGE.value:
            return {
                "from_topic_id": payload.get("from_topic_id"),
                "to_topic_id": payload.get("to_topic_id"),
                "relation": payload.get("relation"),
            }
        if kind == KnowledgeCandidateType.MISCONCEPTION.value:
            return {"topic_id": payload.get("topic_id"), "misconception_key": payload.get("misconception_key") or payload.get("key")}
        if kind == KnowledgeCandidateType.QUESTION_TYPE.value:
            return {"topic_id": payload.get("topic_id"), "question_type_key": payload.get("question_type_key") or payload.get("key")}
        return {}


__all__ = [
    "KnowledgeCandidateStatus",
    "KnowledgeCandidateType",
    "KnowledgeEvidenceType",
    "KnowledgeQualityStore",
]
