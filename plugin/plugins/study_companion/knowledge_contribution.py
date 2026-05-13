from __future__ import annotations

import hashlib
import re
from typing import Any

from .knowledge_quality import KnowledgeCandidateType, KnowledgeEvidenceType
from .models import StudyConfig, json_copy


_SENSITIVE_KEY_RE = re.compile(
    r"(ocr|raw|reply|feedback|expected_answer|user_answer|answer_text|conversation|transcript|source_text|input_text)",
    re.IGNORECASE,
)
_TEXT_KEY_RE = re.compile(r"(^|_)(question|text|answer|prompt|content)(_|$)", re.IGNORECASE)
_ALLOWED_TEXT_KEYS = {"question_type_key", "answer_improved_count"}


def _normalized(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _safe_key(value: object) -> str:
    text = _normalized(value)
    if not text:
        return ""
    if len(text) <= 40 and re.fullmatch(r"[a-z0-9_.:-]+", text):
        return text
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _anonymous_topic_key(value: object, *, fallback: object = "") -> str:
    text = _normalized(value) or _normalized(fallback)
    if not text:
        return ""
    return f"topic:{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def _score_bucket(score: object) -> str:
    try:
        value = float(score)
    except (TypeError, ValueError, OverflowError):
        value = 0.0
    if value < 0.0:
        return "negative"
    if value < 0.35:
        return "low"
    if value < 0.72:
        return "medium"
    return "high"


class PublicGraphContributionBuilder:
    def __init__(self, store: Any, config: StudyConfig | None = None) -> None:
        self._store = store
        self._config = config or StudyConfig()

    def build_anonymous_stats(self, min_sample_count: int = 3) -> list[dict[str, Any]]:
        minimum = max(1, int(min_sample_count or 3))
        candidates = self._store.list_candidate_items(limit=5000)
        stats: list[dict[str, Any]] = []
        for candidate in candidates:
            payload = self._anonymous_payload(candidate)
            if not payload:
                continue
            self.assert_no_raw_learning_text(payload)
            outcome = self._outcome(candidate)
            sample_count = int(candidate.get("evidence_count") or 0)
            stat = {
                "stat_type": str(candidate.get("item_type") or ""),
                "stat_key": self._stat_key(candidate, payload),
                "payload": payload,
                "sample_count": sample_count,
                "outcome": outcome,
                "min_sample_met": sample_count >= minimum,
            }
            self._store.upsert_anonymous_knowledge_stat(
                stat_type=stat["stat_type"],
                stat_key=stat["stat_key"],
                payload=payload,
                sample_count=sample_count,
                outcome=outcome,
                min_sample_met=bool(stat["min_sample_met"]),
            )
            stats.append(stat)
        return stats

    def enqueue_snapshot(self, stats: list[dict[str, Any]]) -> dict[str, Any]:
        eligible = [json_copy(item) for item in stats if bool(item.get("min_sample_met"))]
        if not eligible:
            return {"queued": False, "status": "empty", "count": 0, "queue_item": {}}
        for item in eligible:
            self.assert_no_raw_learning_text(item)
        if not bool(self._config.knowledge_contribution_opt_in):
            return {
                "queued": False,
                "status": "preview",
                "count": len(eligible),
                "queue_item": {},
                "reason": "knowledge_contribution_opt_in_disabled",
            }
        queue_item = self._store.enqueue_knowledge_contribution_snapshot(
            stats=eligible,
            status="queued_for_upload",
        )
        return {
            "queued": True,
            "status": "queued_for_upload",
            "count": len(eligible),
            "queue_item": queue_item,
        }

    def clear_queue(self) -> int:
        return self._store.clear_knowledge_contribution_queue()

    def list_queue(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._store.list_knowledge_contribution_queue(limit=limit)

    def assert_no_raw_learning_text(self, payload: Any) -> None:
        def _scan(value: Any, path: str = "") -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    key_text = str(key)
                    child_path = f"{path}.{key_text}" if path else key_text
                    if _SENSITIVE_KEY_RE.search(key_text):
                        raise ValueError(f"anonymous knowledge payload contains sensitive key: {child_path}")
                    if key_text not in _ALLOWED_TEXT_KEYS and _TEXT_KEY_RE.search(key_text):
                        raise ValueError(f"anonymous knowledge payload contains raw text key: {child_path}")
                    _scan(child, child_path)
                return
            if isinstance(value, list):
                for index, child in enumerate(value):
                    _scan(child, f"{path}[{index}]")
                return
            if isinstance(value, str) and len(value) > 80:
                raise ValueError(f"anonymous knowledge payload contains long free text at {path or '<root>'}")

        _scan(payload)

    def preview(self, *, limit: int = 100) -> dict[str, Any]:
        stats = self.build_anonymous_stats(self._config.knowledge_contribution_min_sample_count)
        return {
            "stats": stats[: max(1, int(limit))],
            "summary": self._store.anonymous_knowledge_stats_summary(),
            "queue": self.list_queue(limit=limit),
            "opt_in": bool(self._config.knowledge_contribution_opt_in),
        }

    @staticmethod
    def _anonymous_payload(candidate: dict[str, Any]) -> dict[str, Any]:
        item_type = str(candidate.get("item_type") or "")
        source = dict(candidate.get("payload") or {})
        base = {
            "evidence_count": int(candidate.get("evidence_count") or 0),
            "positive_count": int(candidate.get("positive_count") or 0),
            "negative_count": int(candidate.get("negative_count") or 0),
            "conflict_count": int(candidate.get("conflict_count") or 0),
            "score_bucket": _score_bucket(candidate.get("score")),
        }
        if item_type == KnowledgeCandidateType.TOPIC.value:
            topic_id = str(source.get("topic_id") or source.get("id") or "").strip()
            base["topic_id"] = _anonymous_topic_key(topic_id, fallback=candidate.get("dedupe_key") or candidate.get("id"))
            if not base["topic_id"]:
                return {}
            return base
        if item_type == KnowledgeCandidateType.EDGE.value:
            edge = {
                "from_topic_id": _anonymous_topic_key(source.get("from_topic_id")),
                "to_topic_id": _anonymous_topic_key(source.get("to_topic_id")),
                "relation": _safe_key(source.get("relation")),
            }
            if not edge["from_topic_id"] or not edge["to_topic_id"] or not edge["relation"]:
                return {}
            base["edge"] = edge
            return base
        if item_type == KnowledgeCandidateType.MISCONCEPTION.value:
            topic_id = _anonymous_topic_key(source.get("topic_id"))
            key = _safe_key(source.get("misconception_key") or source.get("key") or source.get("text"))
            if not topic_id or not key:
                return {}
            base["topic_id"] = topic_id
            base["misconception_key"] = key
            return base
        if item_type == KnowledgeCandidateType.QUESTION_TYPE.value:
            topic_id = _anonymous_topic_key(source.get("topic_id"))
            key = _safe_key(source.get("question_type_key") or source.get("key") or source.get("type") or source.get("text"))
            if not topic_id or not key:
                return {}
            base["topic_id"] = topic_id
            base["question_type_key"] = key
            return base
        return {}

    def _outcome(self, candidate: dict[str, Any]) -> dict[str, int]:
        evidence = self._store.list_knowledge_evidence(str(candidate.get("id") or ""), limit=5000)
        return {
            "answer_improved_count": sum(1 for item in evidence if item.get("event_type") == KnowledgeEvidenceType.ANSWER_IMPROVED.value),
            "review_retained_count": sum(1 for item in evidence if item.get("event_type") == KnowledgeEvidenceType.REVIEW_RETAINED.value),
            "rejected_count": sum(1 for item in evidence if item.get("event_type") == KnowledgeEvidenceType.USER_REJECTED.value),
        }

    @staticmethod
    def _stat_key(candidate: dict[str, Any], payload: dict[str, Any]) -> str:
        item_type = str(candidate.get("item_type") or "")
        if item_type == KnowledgeCandidateType.EDGE.value:
            edge = dict(payload.get("edge") or {})
            return f"{edge.get('from_topic_id')}:{edge.get('to_topic_id')}:{edge.get('relation')}"
        if item_type == KnowledgeCandidateType.MISCONCEPTION.value:
            return f"{payload.get('topic_id')}:{payload.get('misconception_key')}"
        if item_type == KnowledgeCandidateType.QUESTION_TYPE.value:
            return f"{payload.get('topic_id')}:{payload.get('question_type_key')}"
        return str(payload.get("topic_id") or candidate.get("id") or "")


__all__ = ["PublicGraphContributionBuilder"]
