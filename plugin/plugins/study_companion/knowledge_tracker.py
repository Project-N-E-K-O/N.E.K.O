from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import math
import re
from typing import Any

from .fsrs_bridge import FSRSBridge, StudyFsrsRating, create_card, rate_answer
from .knowledge_quality import (
    KnowledgeCandidateStatus,
    KnowledgeCandidateType,
    KnowledgeEvidenceType,
    KnowledgeQualityStore,
)
from .models import json_copy


_LOGGER = logging.getLogger(__name__)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if not math.isfinite(value):
        return minimum
    return max(minimum, min(maximum, value))


def _slug(value: str) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "general"
    asciiish = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if asciiish:
        return asciiish[:80]
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"topic_{digest}"


def _difficulty_to_float(value: object, default: float = 0.5) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    text = str(value).strip()
    is_level_value = number.is_integer() and 1.0 <= number <= 5.0
    is_integer_level = isinstance(value, int)
    is_float_integer_level = isinstance(value, float) and is_level_value
    is_string_integer_level = bool(re.fullmatch(r"[1-5](?:\.0+)?", text)) and is_level_value
    if number > 1.0 or is_integer_level or is_float_integer_level or is_string_integer_level:
        number = number / 5.0
    return _clamp(number, 0.1, 1.0)


def _difficulty_to_level(value: object, default: float = 0.5) -> int:
    normalized = _difficulty_to_float(value, default)
    return max(1, min(5, int(math.floor(normalized * 5.0 + 0.5))))


def _verdict_score(verdict: str, score: object = None) -> float:
    normalized = str(verdict or "").strip().lower()
    if normalized == "correct":
        return 1.0
    if normalized == "partial":
        return 0.55
    if normalized in {"wrong", "dont_know"}:
        return 0.0
    try:
        return _clamp(float(score) / 100.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _score_percent(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return number * 100.0 if 0.0 <= number <= 1.0 else number

    text = str(value or "").strip()
    if not text:
        return 0.0

    letter_scores = {
        "a+": 98.0,
        "a": 95.0,
        "a-": 90.0,
        "b+": 88.0,
        "b": 85.0,
        "b-": 80.0,
        "c+": 78.0,
        "c": 75.0,
        "c-": 70.0,
        "d": 65.0,
        "f": 0.0,
    }
    lowered = text.lower()
    if lowered in letter_scores:
        return letter_scores[lowered]

    try:
        if "/" in text:
            numerator_text, denominator_text = text.split("/", 1)
            numerator = float(numerator_text.strip())
            denominator = float(denominator_text.strip())
            return 0.0 if denominator == 0 else (numerator / denominator) * 100.0
        if text.endswith("%"):
            return float(text[:-1].strip())
        number = float(text)
        return number * 100.0 if 0.0 <= number <= 1.0 else number
    except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        _LOGGER.warning("Failed to parse study evaluation score %r: %s", value, exc)
        return 0.0


@dataclass(slots=True)
class MasterySnapshot:
    topic_id: str
    mastery: float
    accuracy: float
    recency: float
    consistency: float
    confidence: float
    level: str
    attempts: int
    flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic_id": self.topic_id,
            "mastery": self.mastery,
            "accuracy": self.accuracy,
            "recency": self.recency,
            "consistency": self.consistency,
            "confidence": self.confidence,
            "level": self.level,
            "attempts": self.attempts,
            "flags": list(self.flags),
        }


class MasteryTracker:
    def get_level(self, mastery: float) -> str:
        value = _clamp(float(mastery or 0.0))
        if value < 0.20:
            return "未接触"
        if value < 0.40:
            return "薄弱"
        if value < 0.60:
            return "进行中"
        if value < 0.80:
            return "熟练"
        return "掌握"

    def update(
        self,
        topic_id: str,
        result: dict[str, Any],
        *,
        recent_results: list[dict[str, Any]] | None = None,
    ) -> MasterySnapshot:
        results = list(recent_results or [])[-9:] + [dict(result or {})]
        scores = [_verdict_score(str(item.get("verdict") or ""), item.get("score")) for item in results]
        attempts = len(scores)
        accuracy = sum(scores) / max(1, attempts)
        variance = sum((score - accuracy) ** 2 for score in scores) / max(1, attempts)
        consistency = _clamp(1.0 - math.sqrt(variance) * 2.0)
        confidence = _clamp(1.0 - math.exp(-attempts / 5.0))
        recency = 1.0
        difficulty = _difficulty_to_float(result.get("difficulty"), 0.5)
        difficulty_bonus = 0.9 + difficulty * 0.2
        raw_mastery = accuracy * recency * (0.6 + 0.4 * consistency) * difficulty_bonus
        mastery = _clamp(raw_mastery * (0.55 + 0.45 * confidence))
        flags: list[str] = []
        if self.detect_false_mastery_from_values(accuracy=accuracy, consistency=consistency):
            flags.append("false_mastery")
        if attempts < 3:
            flags.append("low_confidence")
        return MasterySnapshot(
            topic_id=topic_id,
            mastery=round(mastery, 4),
            accuracy=round(_clamp(accuracy), 4),
            recency=round(recency, 4),
            consistency=round(consistency, 4),
            confidence=round(confidence, 4),
            level=self.get_level(mastery),
            attempts=attempts,
            flags=flags,
        )

    def detect_false_mastery(self, topic_id: str, recent_results: list[dict[str, Any]]) -> bool:
        scores = [_verdict_score(str(item.get("verdict") or ""), item.get("score")) for item in recent_results]
        if not scores:
            return False
        accuracy = sum(scores) / len(scores)
        variance = sum((score - accuracy) ** 2 for score in scores) / len(scores)
        consistency = _clamp(1.0 - math.sqrt(variance) * 2.0)
        return self.detect_false_mastery_from_values(accuracy=accuracy, consistency=consistency)

    @staticmethod
    def detect_false_mastery_from_values(*, accuracy: float, consistency: float) -> bool:
        return accuracy > 0.6 and consistency < 0.5


class KnowledgeGraph:
    def __init__(self, store: Any) -> None:
        self._store = store
        self._quality = KnowledgeQualityStore(store)

    def get_ready_topics(self, mastered: set[str]) -> list[str]:
        ready: list[str] = []
        mastery_by_topic = {
            item.get("topic_id"): float(item.get("mastery") or 0.0)
            for item in self._store.list_mastery_overview(limit=1000)
        }
        for topic in self._store.list_topics(limit=1000):
            topic_id = str(topic.get("id") or "")
            if topic_id in mastered:
                continue
            prerequisites = topic.get("prerequisites") if isinstance(topic.get("prerequisites"), list) else []
            if all(mastery_by_topic.get(str(req.get("id") or ""), 0.0) >= float(req.get("required_mastery") or 0.0) for req in prerequisites if isinstance(req, dict)):
                ready.append(topic_id)
        return ready

    def find_blocker(self, topic_id: str) -> list[str]:
        topic = self._store.get_topic(topic_id)
        if not topic:
            return []
        mastery_by_topic = {
            item.get("topic_id"): float(item.get("mastery") or 0.0)
            for item in self._store.list_mastery_overview(limit=1000)
        }
        blockers: list[str] = []
        for req in topic.get("prerequisites") or []:
            if not isinstance(req, dict):
                continue
            req_id = str(req.get("id") or "")
            if mastery_by_topic.get(req_id, 0.0) < float(req.get("required_mastery") or 0.0):
                blockers.append(req_id)
        return blockers

    def discover_candidate(self, text: str, context: dict[str, Any] | None = None) -> str | None:
        topic = str((context or {}).get("topic") or "").strip()
        if topic:
            known = self._store.get_topic(topic) or self._store.find_topic_by_name(topic)
            if known:
                return str(known.get("id") or "")
            candidate = self._quality.upsert_candidate(
                KnowledgeCandidateType.TOPIC.value,
                {
                    "topic_id": _slug(topic),
                    "name": topic,
                    "subject": str((context or {}).get("subject") or "math"),
                },
                source=str((context or {}).get("source") or "llm"),
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context=context or {},
            )
            return str(candidate.get("id") or "")
        normalized = str(text or "").strip()
        if not normalized:
            return None
        for known in self._store.list_topics(limit=1000):
            name = str(known.get("name") or "")
            if name and name in normalized:
                return str(known.get("id") or "")
        first = next((line.strip() for line in normalized.splitlines() if line.strip()), "")
        if not first:
            return None
        candidate = self._quality.upsert_candidate(
            KnowledgeCandidateType.TOPIC.value,
            {
                "topic_id": _slug(first),
                "name": first[:120],
                "subject": str((context or {}).get("subject") or "math"),
            },
            source=str((context or {}).get("source") or "llm"),
            evidence_type=KnowledgeEvidenceType.MENTIONED.value,
            context=context or {},
        )
        return str(candidate.get("id") or "")


class WrongQuestionStore:
    def __init__(self, store: Any) -> None:
        self._store = store

    def add(
        self,
        *,
        topic_id: str,
        question: dict[str, Any],
        user_answer: str,
        expected_answer: str,
        error_type: str,
        verdict: str,
    ) -> str:
        return self._store.add_wrong_question(
            topic_id=topic_id,
            question=question,
            user_answer=user_answer,
            expected_answer=expected_answer,
            error_type=error_type or "unknown",
            verdict=verdict,
        )

    def get_retry(self, topic_id: str) -> dict[str, Any] | None:
        return self._store.get_retry_wrong_question(topic_id)

    def generate_variant(self, wq: dict[str, Any], attempt: int) -> dict[str, Any]:
        question = dict(wq.get("question") or {})
        question["variant_of"] = wq.get("id")
        question["variant_attempt"] = int(attempt)
        question["focus_error_type"] = wq.get("error_type")
        return question

    def mark_resolved(self, question_id: str) -> None:
        self._store.mark_wrong_question_resolved(question_id)


class KnowledgeTracker:
    def __init__(self, store: Any, *, retention_target: float = 0.90, logger: Any | None = None) -> None:
        self.store = store
        self.mastery = MasteryTracker()
        self.graph = KnowledgeGraph(store)
        self.quality = KnowledgeQualityStore(store)
        self.wrong_store = WrongQuestionStore(store)
        self.fsrs = FSRSBridge(retention_target=retention_target)
        self._logger = logger

    def on_answer(
        self,
        *,
        topic_id: str,
        question: dict[str, Any],
        user_answer: str,
        eval_result: dict[str, Any],
        mode: str,
        session_id: str = "default",
        response_time_ms: int | None = None,
    ) -> dict[str, Any]:
        topic_id = self._ensure_topic(topic_id, question=question, eval_result=eval_result)
        question_payload = dict(question or {})
        question_payload.setdefault("topic", topic_id)
        difficulty = _difficulty_to_float(question_payload.get("difficulty"), 0.5)
        verdict = str(eval_result.get("verdict") or "").strip().lower()
        error_type = str(eval_result.get("error_type") or "").strip() or "unknown"
        is_known_topic = bool(self.store.get_topic(topic_id))
        qa_topic_id = topic_id if is_known_topic else ""
        self.store.ensure_session(session_id=session_id, mode=mode)
        self.store.add_qa_record(
            session_id=session_id,
            topic_id=qa_topic_id,
            question=question_payload,
            user_answer=user_answer,
            eval_result=eval_result,
            mode=mode,
            response_time_ms=response_time_ms,
        )

        recent = self.store.list_qa_records_for_topic(topic_id, limit=10) if qa_topic_id else []
        if not is_known_topic:
            if verdict in {"wrong", "partial", "dont_know"}:
                self._record_error_candidates(
                    topic_id=topic_id,
                    question=question_payload,
                    eval_result=eval_result,
                    error_type=error_type,
                    verdict=verdict,
                )
            elif verdict == "correct":
                self._record_positive_question_type(topic_id=topic_id, question=question_payload)
            return {
                "topic_id": topic_id,
                "mastery": {},
                "wrong_question_id": "",
                "fsrs": {},
            }
        recent_results = [dict(item.get("eval_result") or {}) for item in recent[:-1]]
        mastery_result = {
            "verdict": verdict,
            "score": eval_result.get("score"),
            "difficulty": difficulty,
            "response_time_ms": response_time_ms,
        }
        snapshot = self.mastery.update(topic_id, mastery_result, recent_results=recent_results)
        self.store.append_mastery_snapshot(snapshot.to_dict())

        wrong_question_id = ""
        if verdict in {"wrong", "partial", "dont_know"}:
            wrong_question_id = self.wrong_store.add(
                topic_id=topic_id,
                question=question_payload,
                user_answer=user_answer,
                expected_answer=str(question_payload.get("answer") or eval_result.get("expected_answer") or ""),
                error_type=error_type,
                verdict=verdict,
            )
            self._record_error_candidates(
                topic_id=topic_id,
                question=question_payload,
                eval_result=eval_result,
                error_type=error_type,
                verdict=verdict,
            )
        elif verdict == "correct":
            self.store.record_wrong_question_correct(
                topic_id=topic_id,
                error_type=error_type,
                difficulty=_difficulty_to_level(difficulty),
            )
            self._record_positive_question_type(topic_id=topic_id, question=question_payload)

        card_row = self.store.get_fsrs_card(topic_id)
        card = card_row.get("card") if card_row else create_card(topic_id).to_dict()
        rating = self._rating_from_eval(eval_result)
        updated_card, schedule = rate_answer(card, rating)
        self.store.upsert_fsrs_card(topic_id=topic_id, card=updated_card.to_dict(), last_rating=int(rating))
        self.store.append_review_log(
            topic_id=topic_id,
            card_id=int((card_row or {}).get("id") or 0) or None,
            rating=int(rating),
            scheduled_days=int(round(updated_card.scheduled_days)),
            actual_days=int(round(updated_card.elapsed_days)),
        )

        return {
            "topic_id": topic_id,
            "mastery": snapshot.to_dict(),
            "wrong_question_id": wrong_question_id,
            "fsrs": schedule,
        }

    def get_next_question_params(self, topic_id: str = "") -> dict[str, Any]:
        resolved = self._resolve_topic_id(topic_id)
        weak_topics = self.get_weak_topics(limit=5)
        review_queue = self.get_review_queue(limit=5)
        topic = self.store.get_topic(resolved) if resolved else None
        latest = self.store.get_latest_mastery(resolved) if resolved else None
        mastery_value = float((latest or {}).get("mastery") or 0.0)
        if mastery_value < 0.35:
            difficulty = 2
        elif mastery_value < 0.65:
            difficulty = 3
        else:
            difficulty = 4
        blockers = self.graph.find_blocker(resolved) if resolved else []
        retry = self.wrong_store.get_retry(resolved) if resolved else None
        candidate_evidence = self.quality.prompt_evidence_summary(topic_id=resolved, limit=5)
        for item in candidate_evidence:
            try:
                self.quality.add_evidence(
                    str(item.get("id") or ""),
                    KnowledgeEvidenceType.USED_IN_PROMPT.value,
                    0.35,
                    {"source": "question_prompt", "topic_id": resolved},
                )
            except Exception as exc:
                self._log_quality_warning("record prompt candidate evidence failed: {}", exc)
        return {
            "target_topic_id": resolved,
            "target_topic": topic or {},
            "suggested_difficulty": difficulty,
            "mastery": latest or {},
            "weak_topics": weak_topics,
            "due_reviews": review_queue,
            "blockers": blockers,
            "retry_wrong_question": retry or {},
            "candidate_evidence": candidate_evidence,
            "prompt_guidance": self._question_guidance(mastery_value, blockers=blockers, retry=retry),
        }

    def get_session_summary(self) -> dict[str, Any]:
        return {
            "mastery_overview": self.store.list_mastery_overview(limit=10),
            "weak_topics": self.get_weak_topics(limit=8),
            "review_queue": self.get_review_queue(limit=8),
            "wrong_questions": self.store.list_wrong_questions(limit=8, statuses=("active", "retrying")),
            "candidate_evidence": self.quality.prompt_evidence_summary(limit=8),
        }

    def get_review_queue(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.store.list_fsrs_cards(limit=None)
        reviews = self.fsrs.get_due_reviews([row["card"] for row in rows])
        result: list[dict[str, Any]] = []
        for item in reviews[: max(1, int(limit))]:
            topic = self.store.get_topic(str(item.get("topic_id") or "")) or {}
            result.append({**item, "topic": topic})
        return result

    def count_due_reviews(self) -> int:
        rows = self.store.list_fsrs_cards(limit=None)
        return len(self.fsrs.get_due_reviews([row["card"] for row in rows]))

    def get_weak_topics(self, limit: int = 5) -> list[dict[str, Any]]:
        overview = self.store.list_mastery_overview(limit=1000)
        weak = [
            item
            for item in overview
            if float(item.get("mastery") or 0.0) < 0.60 or "false_mastery" in (item.get("flags") or [])
        ]
        weak.sort(key=lambda item: (float(item.get("mastery") or 0.0), -float(item.get("confidence") or 0.0)))
        return weak[: max(1, int(limit))]

    def count_weak_topics(self) -> int:
        overview = self.store.list_mastery_overview(limit=5000)
        return sum(
            1
            for item in overview
            if float(item.get("mastery") or 0.0) < 0.60 or "false_mastery" in (item.get("flags") or [])
        )

    def get_status_summary(self, *, limit: int = 8) -> dict[str, Any]:
        overview = self.store.list_mastery_overview(limit=limit)
        return {
            "topic_count": self.store.count_topics(),
            "tracked_topic_count": self.store.count_tracked_mastery_topics(),
            "average_mastery": round(float(self.store.average_latest_mastery()), 4),
            "weak_topic_count": self.count_weak_topics(),
            "due_review_count": self.count_due_reviews(),
            "last_updated_at": overview[0].get("updated_at") if overview else "",
            "candidate_quality": self.quality.status_summary(limit=limit),
        }

    def _ensure_topic(self, topic_id: str, *, question: dict[str, Any], eval_result: dict[str, Any]) -> str:
        raw_topic = str(topic_id or question.get("topic") or eval_result.get("topic") or "").strip()
        resolved = self._resolve_topic_id(raw_topic)
        topic_name = str(question.get("topic") or eval_result.get("topic") or resolved or "general").strip()
        if not resolved:
            resolved = _slug(topic_name)
        if not self.store.get_topic(resolved):
            self.quality.upsert_candidate(
                KnowledgeCandidateType.TOPIC.value,
                {
                    "topic_id": resolved,
                    "name": topic_name or resolved,
                    "subject": str(question.get("subject") or "math"),
                },
                source=str(eval_result.get("source") or "answer_tracking"),
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context={"topic_id": resolved, "mode": eval_result.get("mode") or ""},
            )
        self.store.ensure_topic(
            topic_id=resolved,
            name=topic_name or resolved,
            subject=str(question.get("subject") or "math"),
            chapter=str(question.get("chapter") or question.get("topic") or "runtime"),
            difficulty=_difficulty_to_float(question.get("difficulty"), 0.5),
        )
        return resolved

    def _resolve_topic_id(self, topic: str) -> str:
        value = str(topic or "").strip()
        if not value:
            return ""
        if self.store.get_topic(value):
            return value
        existing = self.store.find_topic_by_name(value)
        if existing:
            return str(existing.get("id") or "")
        return _slug(value)

    def _record_error_candidates(
        self,
        *,
        topic_id: str,
        question: dict[str, Any],
        eval_result: dict[str, Any],
        error_type: str,
        verdict: str,
    ) -> None:
        key = _slug(error_type or "unknown")
        try:
            self.quality.upsert_candidate(
                KnowledgeCandidateType.MISCONCEPTION.value,
                {
                    "topic_id": topic_id,
                    "misconception_key": key,
                    "error_type": error_type,
                },
                source="answer_evaluation",
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context={"verdict": verdict, "score": eval_result.get("score")},
            )
        except Exception as exc:
            self._log_quality_warning("record misconception candidate failed: {}", exc)
        question_type = self._question_type_key(question)
        try:
            self.quality.upsert_candidate(
                KnowledgeCandidateType.QUESTION_TYPE.value,
                {
                    "topic_id": topic_id,
                    "question_type_key": question_type,
                    "difficulty": question.get("difficulty"),
                },
                source="answer_evaluation",
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context={"verdict": verdict, "error_type": error_type},
            )
        except Exception as exc:
            self._log_quality_warning("record question type candidate failed: {}", exc)

    def _record_positive_question_type(self, *, topic_id: str, question: dict[str, Any]) -> None:
        try:
            candidate = self.quality.upsert_candidate(
                KnowledgeCandidateType.QUESTION_TYPE.value,
                {
                    "topic_id": topic_id,
                    "question_type_key": self._question_type_key(question),
                    "difficulty": question.get("difficulty"),
                },
                source="answer_evaluation",
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context={"verdict": "correct"},
            )
            self.quality.add_evidence(
                str(candidate.get("id") or ""),
                KnowledgeEvidenceType.ANSWER_IMPROVED.value,
                1.0,
                {"source": "answer_evaluation", "topic_id": topic_id},
            )
        except Exception as exc:
            self._log_quality_warning("record positive question type candidate failed: {}", exc)

    @staticmethod
    def _question_type_key(question: dict[str, Any]) -> str:
        explicit = str(question.get("question_type") or question.get("type") or "").strip()
        if explicit:
            return _slug(explicit)
        difficulty = question.get("difficulty")
        if difficulty not in (None, ""):
            return f"difficulty_{_difficulty_to_level(difficulty)}"
        return "general"

    def _log_quality_warning(self, message: str, *args: Any) -> None:
        warning = getattr(self._logger, "warning", None)
        if callable(warning):
            try:
                warning(message, *args)
            except Exception:
                pass

    @staticmethod
    def _rating_from_eval(eval_result: dict[str, Any]) -> StudyFsrsRating:
        verdict = str(eval_result.get("verdict") or "").strip().lower()
        error_type = str(eval_result.get("error_type") or "").strip().lower()
        score = _score_percent(eval_result.get("score"))
        if verdict in {"wrong", "dont_know"} or error_type in {"concept_error", "misconception", "guess", "concept_missing"}:
            return StudyFsrsRating.Again
        if verdict == "partial" or error_type in {"calculation_error", "missing_step", "step_skipped"}:
            return StudyFsrsRating.Hard
        if verdict == "correct" and score >= 92:
            return StudyFsrsRating.Easy
        return StudyFsrsRating.Good

    @staticmethod
    def _question_guidance(mastery: float, *, blockers: list[str], retry: dict[str, Any] | None) -> str:
        if retry:
            return "Use a variant of the active wrong question and check whether the same error type reappears."
        if blockers:
            return "Ask a prerequisite check before the target topic."
        if mastery < 0.35:
            return "Use a direct recall or single-step question with a visible hint."
        if mastery < 0.65:
            return "Use a medium practice question that isolates one concept."
        return "Use a harder transfer question or mixed-topic application."


__all__ = [
    "KnowledgeGraph",
    "KnowledgeTracker",
    "MasterySnapshot",
    "MasteryTracker",
    "WrongQuestionStore",
]
