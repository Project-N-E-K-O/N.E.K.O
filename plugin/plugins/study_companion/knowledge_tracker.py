from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import logging
import math
import re
import uuid
from typing import Any, Callable

from .fsrs_bridge import (
    FSRSBridge,
    StudyFsrsCard,
    StudyFsrsRating,
    create_card,
    rate_answer,
    retrievability,
)
from .knowledge_quality import (
    KnowledgeCandidateStatus,
    KnowledgeCandidateType,
    KnowledgeEvidenceType,
    KnowledgeQualityStore,
)
from .memory_text import normalize_tags
from .models import json_copy


_LOGGER = logging.getLogger(__name__)


class _BatchAnswerWriteFailed(RuntimeError):
    def __init__(self, original: Exception) -> None:
        super().__init__(str(original))
        self.original = original


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
    is_string_integer_level = (
        bool(re.fullmatch(r"[1-5](?:\.0+)?", text)) and is_level_value
    )
    if (
        number > 1.0
        or is_integer_level
        or is_float_integer_level
        or is_string_integer_level
    ):
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
        scores = [
            _verdict_score(str(item.get("verdict") or ""), item.get("score"))
            for item in results
        ]
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
        if self.detect_false_mastery_from_values(
            accuracy=accuracy, consistency=consistency
        ):
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

    def detect_false_mastery(
        self, topic_id: str, recent_results: list[dict[str, Any]]
    ) -> bool:
        scores = [
            _verdict_score(str(item.get("verdict") or ""), item.get("score"))
            for item in recent_results
        ]
        if not scores:
            return False
        accuracy = sum(scores) / len(scores)
        variance = sum((score - accuracy) ** 2 for score in scores) / len(scores)
        consistency = _clamp(1.0 - math.sqrt(variance) * 2.0)
        return self.detect_false_mastery_from_values(
            accuracy=accuracy, consistency=consistency
        )

    @staticmethod
    def detect_false_mastery_from_values(
        *, accuracy: float, consistency: float
    ) -> bool:
        return accuracy > 0.6 and consistency < 0.5


class KnowledgeGraph:
    _TOPIC_INDEX_LIMIT = 1000

    def __init__(self, store: Any) -> None:
        self._store = store
        self._quality = KnowledgeQualityStore(store)
        self._topic_id_index: set[str] = set()
        self._topic_name_index: dict[str, str] = {}
        self._index_truncated = False
        self._index_dirty = True

    def _ensure_index(self) -> None:
        """Refresh the in-memory topic-name index from the store when needed."""
        if not self._index_dirty:
            return
        self._topic_id_index.clear()
        self._topic_name_index.clear()
        self._index_truncated = False
        index_limit = self._TOPIC_INDEX_LIMIT
        topics = self._store.list_topics(limit=index_limit)
        if len(topics) >= index_limit:
            self._index_truncated = True
            if callable(getattr(self._store, "count_topics", None)):
                try:
                    total = int(self._store.count_topics())
                except Exception:
                    total = index_limit + 1
                self._index_truncated = total > index_limit
                if self._index_truncated:
                    _LOGGER.warning(
                        "KnowledgeGraph topic index limited to %s of %s topics",
                        index_limit,
                        total,
                    )
        for topic in topics:
            topic_id = str(topic.get("id") or "").strip()
            name = str(topic.get("name") or "").strip()
            if topic_id:
                self._topic_id_index.add(topic_id)
            if name and topic_id:
                self._topic_name_index[name] = topic_id
        self._index_dirty = False

    def mark_dirty(self) -> None:
        self._index_dirty = True

    def resolve_known_topic(self, topic: str) -> str:
        value = str(topic or "").strip()
        if not value:
            return ""
        self._ensure_index()
        if value in self._topic_id_index:
            return value
        indexed = self._topic_name_index.get(value, "")
        if indexed:
            return indexed
        if self._index_truncated:
            return self._resolve_store_topic(value)
        return ""

    def _resolve_store_topic(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        get_topic = getattr(self._store, "get_topic", None)
        if callable(get_topic):
            try:
                topic = get_topic(text)
            except Exception:
                topic = None
            if topic:
                return str(topic.get("id") or text)
        find_topic_by_name = getattr(self._store, "find_topic_by_name", None)
        if callable(find_topic_by_name):
            try:
                topic = find_topic_by_name(text)
            except Exception:
                topic = None
            if topic:
                return str(topic.get("id") or "")
        return ""

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
            prerequisites = (
                topic.get("prerequisites")
                if isinstance(topic.get("prerequisites"), list)
                else []
            )
            if all(
                mastery_by_topic.get(str(req.get("id") or ""), 0.0)
                >= float(req.get("required_mastery") or 0.0)
                for req in prerequisites
                if isinstance(req, dict)
            ):
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
            if mastery_by_topic.get(req_id, 0.0) < float(
                req.get("required_mastery") or 0.0
            ):
                blockers.append(req_id)
        return blockers

    def discover_candidate(
        self, text: str, context: dict[str, Any] | None = None
    ) -> str | None:
        self._ensure_index()
        topic = str((context or {}).get("topic") or "").strip()
        if topic:
            known_id = self.resolve_known_topic(topic)
            if known_id:
                return known_id
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
        if self._index_truncated:
            known_id = self._resolve_store_topic(normalized)
            if known_id:
                return known_id
            first = next(
                (line.strip() for line in normalized.splitlines() if line.strip()), ""
            )
            if first and first != normalized:
                known_id = self._resolve_store_topic(first)
                if known_id:
                    return known_id
            return None
        for name, topic_id in self._topic_name_index.items():
            if name and name in normalized:
                return topic_id
        first = next(
            (line.strip() for line in normalized.splitlines() if line.strip()), ""
        )
        if not first:
            return None
        if self._index_truncated:
            known_id = self._resolve_store_topic(first)
            if known_id:
                return known_id
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
    def __init__(
        self, store: Any, *, retention_target: float = 0.90, logger: Any | None = None
    ) -> None:
        self.store = store
        self.mastery = MasteryTracker()
        self.graph = KnowledgeGraph(store)
        self.quality = KnowledgeQualityStore(store)
        self.wrong_store = WrongQuestionStore(store)
        self.fsrs = FSRSBridge(retention_target=retention_target)
        self._logger = logger
        self._memory_deck_summary_provider: Callable[..., dict[str, Any]] | None = None
        self._quality_warning_failures = 0

    def set_memory_deck_summary_provider(
        self, provider: Callable[..., dict[str, Any]] | None
    ) -> None:
        self._memory_deck_summary_provider = provider

    def get_mastery(self, topic_id: str) -> float:
        resolved = self._resolve_topic_id(topic_id)
        latest = self.store.get_latest_mastery(resolved) if resolved else None
        return float((latest or {}).get("mastery") or 0.0)

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
        if not self._can_batch_answer_data():
            return self._on_answer_legacy(
                topic_id=topic_id,
                question=question,
                user_answer=user_answer,
                eval_result=eval_result,
                mode=mode,
                session_id=session_id,
                response_time_ms=response_time_ms,
            )

        answer_lock = self.store.answer_write_lock()
        try:
            with answer_lock:
                return self._on_answer_batch(
                    topic_id=topic_id,
                    question=question,
                    user_answer=user_answer,
                    eval_result=eval_result,
                    mode=mode,
                    session_id=session_id,
                    response_time_ms=response_time_ms,
                )
        except _BatchAnswerWriteFailed as exc:
            self._log_exception(
                "study batch answer write failed; falling back to legacy path: {}",
                exc.original,
            )
            return self._on_answer_legacy(
                topic_id=topic_id,
                question=question,
                user_answer=user_answer,
                eval_result=eval_result,
                mode=mode,
                session_id=session_id,
                response_time_ms=response_time_ms,
            )

    def _on_answer_batch(
        self,
        *,
        topic_id: str,
        question: dict[str, Any],
        user_answer: str,
        eval_result: dict[str, Any],
        mode: str,
        session_id: str,
        response_time_ms: int | None,
    ) -> dict[str, Any]:
        (
            topic_id,
            topic_existed,
            topic_upsert_data,
            topic_candidate_data,
        ) = self._prepare_answer_topic_data(
            topic_id, question=question, eval_result=eval_result
        )
        question_payload = dict(question or {})
        question_payload.setdefault("topic", topic_id)
        difficulty = _difficulty_to_float(question_payload.get("difficulty"), 0.5)
        verdict = str(eval_result.get("verdict") or "").strip().lower()
        error_type = str(eval_result.get("error_type") or "").strip() or "unknown"
        answer_state = self.store.load_answer_write_state(topic_id, recent_limit=10)
        recent = answer_state.get("recent") if topic_existed else []
        card_row = answer_state.get("fsrs_card") if topic_existed else None
        recent_results = [dict(item.get("eval_result") or {}) for item in recent]
        mastery_result = {
            "verdict": verdict,
            "score": eval_result.get("score"),
            "difficulty": difficulty,
            "response_time_ms": response_time_ms,
        }
        snapshot = self.mastery.update(
            topic_id, mastery_result, recent_results=recent_results
        )

        wrong_question_data: dict[str, Any] | None = None
        error_candidates: list[dict[str, Any]] = []
        positive_candidate: dict[str, Any] | None = None
        if verdict in {"wrong", "partial", "dont_know"}:
            wrong_question_data = {
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "question": question_payload,
                "user_answer": user_answer,
                "expected_answer": str(
                    question_payload.get("answer")
                    or eval_result.get("expected_answer")
                    or ""
                ),
                "error_type": error_type,
                "verdict": verdict,
            }
            error_candidates = self._build_error_candidate_data(
                topic_id=topic_id,
                question=question_payload,
                eval_result=eval_result,
                error_type=error_type,
                verdict=verdict,
            )
        elif verdict == "correct":
            positive_candidate = self._build_positive_candidate_data(
                topic_id=topic_id, question=question_payload
            )

        card = card_row.get("card") if card_row else create_card(topic_id).to_dict()
        rating = self._rating_from_eval(eval_result)
        updated_card, schedule = rate_answer(card, rating)
        try:
            batch_result = self.store.batch_write_answer_data(
                session_id=session_id,
                mode=mode,
                topic_id=topic_id,
                question=question_payload,
                user_answer=user_answer,
                eval_result=eval_result,
                response_time_ms=response_time_ms,
                mastery_snapshot=snapshot.to_dict(),
                wrong_question_data=wrong_question_data,
                fsrs_card=updated_card.to_dict(),
                fsrs_rating=int(rating),
                review_log_data={
                    "topic_id": topic_id,
                    "card_id": int((card_row or {}).get("id") or 0) or None,
                    "rating": int(rating),
                    "scheduled_days": int(round(updated_card.scheduled_days)),
                    "actual_days": int(round(updated_card.elapsed_days)),
                },
                error_candidate_data=error_candidates,
                positive_candidate_data=positive_candidate,
                topic_upsert_data=topic_upsert_data,
                topic_candidate_data=topic_candidate_data,
            )
        except Exception as exc:
            raise _BatchAnswerWriteFailed(exc) from exc
        if topic_upsert_data:
            self.graph.mark_dirty()
        if verdict == "correct":
            self.store.record_wrong_question_correct(
                topic_id=topic_id,
                error_type=error_type,
                difficulty=_difficulty_to_level(difficulty),
            )

        return {
            "topic_id": topic_id,
            "mastery": snapshot.to_dict(),
            "wrong_question_id": str(batch_result.get("wrong_question_id") or ""),
            "fsrs": schedule,
        }

    def _can_batch_answer_data(self) -> bool:
        if not bool(getattr(self.store, "_supports_batch_answer", False)):
            return False
        return all(
            callable(getattr(self.store, name, None))
            for name in (
                "batch_write_answer_data",
                "answer_write_lock",
                "load_answer_write_state",
            )
        )

    def _on_answer_legacy(
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
        topic_id = self._ensure_topic(
            topic_id, question=question, eval_result=eval_result
        )
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

        recent = (
            self.store.list_qa_records_for_topic(topic_id, limit=10)
            if qa_topic_id
            else []
        )
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
                self._record_positive_question_type(
                    topic_id=topic_id, question=question_payload
                )
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
        snapshot = self.mastery.update(
            topic_id, mastery_result, recent_results=recent_results
        )
        self.store.append_mastery_snapshot(snapshot.to_dict())

        wrong_question_id = ""
        if verdict in {"wrong", "partial", "dont_know"}:
            wrong_question_id = self.wrong_store.add(
                topic_id=topic_id,
                question=question_payload,
                user_answer=user_answer,
                expected_answer=str(
                    question_payload.get("answer")
                    or eval_result.get("expected_answer")
                    or ""
                ),
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
            self._record_positive_question_type(
                topic_id=topic_id, question=question_payload
            )

        card_row = self.store.get_fsrs_card(topic_id)
        card = card_row.get("card") if card_row else create_card(topic_id).to_dict()
        rating = self._rating_from_eval(eval_result)
        updated_card, schedule = rate_answer(card, rating)
        self.store.upsert_fsrs_card(
            topic_id=topic_id, card=updated_card.to_dict(), last_rating=int(rating)
        )
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

    def _prepare_answer_topic_data(
        self, topic_id: str, *, question: dict[str, Any], eval_result: dict[str, Any]
    ) -> tuple[str, bool, dict[str, Any] | None, dict[str, Any] | None]:
        resolved, topic_name, existing = self._resolve_and_identify_topic(
            topic_id, question=question, eval_result=eval_result
        )
        topic_data = None
        topic_candidate = None
        if not existing:
            topic_data = {
                "id": resolved,
                "name": topic_name or resolved,
                "subject": str(question.get("subject") or "math"),
                "chapter": str(
                    question.get("chapter") or question.get("topic") or "runtime"
                ),
                "depth": 2,
                "difficulty": _difficulty_to_float(question.get("difficulty"), 0.5),
                "prerequisites": [],
                "related": [],
                "typical_misconceptions": [],
                "source": "runtime",
            }
            topic_candidate = self._build_candidate_write_data(
                item_type=KnowledgeCandidateType.TOPIC.value,
                payload={
                    "topic_id": resolved,
                    "name": topic_name or resolved,
                    "subject": str(question.get("subject") or "math"),
                },
                source=str(eval_result.get("source") or "answer_tracking"),
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context={"topic_id": resolved, "mode": eval_result.get("mode") or ""},
                weight=0.2,
            )
        return resolved, existing, topic_data, topic_candidate

    def _build_error_candidate_data(
        self,
        *,
        topic_id: str,
        question: dict[str, Any],
        eval_result: dict[str, Any],
        error_type: str,
        verdict: str,
    ) -> list[dict[str, Any]]:
        key = _slug(error_type or "unknown")
        return [
            self._build_candidate_write_data(
                item_type=KnowledgeCandidateType.MISCONCEPTION.value,
                payload={
                    "topic_id": topic_id,
                    "misconception_key": key,
                    "error_type": error_type,
                },
                source="answer_evaluation",
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context={"verdict": verdict, "score": eval_result.get("score")},
                weight=0.2,
            ),
            self._build_candidate_write_data(
                item_type=KnowledgeCandidateType.QUESTION_TYPE.value,
                payload={
                    "topic_id": topic_id,
                    "question_type_key": self._question_type_key(question),
                    "difficulty": question.get("difficulty"),
                },
                source="answer_evaluation",
                evidence_type=KnowledgeEvidenceType.MENTIONED.value,
                context={"verdict": verdict, "error_type": error_type},
                weight=0.2,
            ),
        ]

    def _build_positive_candidate_data(
        self, *, topic_id: str, question: dict[str, Any]
    ) -> dict[str, Any]:
        candidate = self._build_candidate_write_data(
            item_type=KnowledgeCandidateType.QUESTION_TYPE.value,
            payload={
                "topic_id": topic_id,
                "question_type_key": self._question_type_key(question),
                "difficulty": question.get("difficulty"),
            },
            source="answer_evaluation",
            evidence_type=KnowledgeEvidenceType.MENTIONED.value,
            context={"verdict": "correct"},
            weight=0.2,
        )
        candidate["evidence"].append(
            {
                "event_type": KnowledgeEvidenceType.ANSWER_IMPROVED.value,
                "weight": 1.0,
                "context": {"source": "answer_evaluation", "topic_id": topic_id},
            }
        )
        return candidate

    def _build_candidate_write_data(
        self,
        *,
        item_type: str,
        payload: dict[str, Any],
        source: str,
        evidence_type: str,
        context: dict[str, Any],
        weight: float,
    ) -> dict[str, Any]:
        clean_payload = json_copy(payload or {})
        return {
            "id": str(uuid.uuid4()),
            "item_type": item_type,
            "payload": clean_payload,
            "source": source,
            "dedupe_key": KnowledgeQualityStore._dedupe_key(item_type, clean_payload),
            "evidence": [
                {
                    "event_type": evidence_type,
                    "weight": weight,
                    "context": context,
                }
            ],
        }

    def get_next_question_params(
        self, topic_id: str = "", *, record_prompt_usage: bool = True
    ) -> dict[str, Any]:
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
        candidate_evidence = self.quality.prompt_evidence_summary(
            topic_id=resolved, limit=5
        )
        if record_prompt_usage:
            self.record_prompt_usage_for_question_params(
                {"target_topic_id": resolved, "candidate_evidence": candidate_evidence}
            )
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
            "prompt_guidance": self._question_guidance(
                mastery_value, blockers=blockers, retry=retry
            ),
        }

    def preview_next_question_params(self, topic_id: str = "") -> dict[str, Any]:
        return self.get_next_question_params(topic_id, record_prompt_usage=False)

    def record_prompt_usage_for_question_params(
        self, params: dict[str, Any] | None
    ) -> int:
        payload = dict(params or {})
        topic_id = str(payload.get("target_topic_id") or "").strip()
        recorded = 0
        seen_ids: set[str] = set()
        for item in payload.get("candidate_evidence") or []:
            evidence_id = str((item or {}).get("id") or "").strip()
            if not evidence_id or evidence_id in seen_ids:
                continue
            seen_ids.add(evidence_id)
            try:
                self.quality.add_evidence(
                    evidence_id,
                    KnowledgeEvidenceType.USED_IN_PROMPT.value,
                    0.35,
                    {"source": "question_prompt", "topic_id": topic_id},
                )
                recorded += 1
            except Exception as exc:
                self._log_quality_warning(
                    "record prompt candidate evidence failed: {}", exc
                )
        return recorded

    def get_session_summary(self) -> dict[str, Any]:
        return {
            "mastery_overview": self.store.list_mastery_overview(limit=10),
            "weak_topics": self.get_weak_topics(limit=8),
            "review_queue": self.get_review_queue(limit=8),
            "memory_deck": self.get_memory_deck_status(limit=8),
            "wrong_questions": self.store.list_wrong_questions(
                limit=8, statuses=("active", "retrying")
            ),
            "candidate_evidence": self.quality.prompt_evidence_summary(limit=8),
        }

    def upsert_memory_card(
        self,
        *,
        front: str,
        back: str,
        topic_id: str = "",
        subject: str = "memory",
        chapter: str = "memory_deck",
        difficulty: object = 0.5,
        tags: object = None,
        source: str = "manual",
    ) -> dict[str, Any]:
        front_text = str(front or "").strip()
        back_text = str(back or "").strip()
        if not front_text:
            raise ValueError("memory card front is required")
        if not back_text:
            raise ValueError("memory card back is required")

        resolved = self._resolve_topic_id(str(topic_id or "").strip() or front_text)
        if not resolved:
            resolved = _slug(front_text)
        self.store.ensure_topic(
            topic_id=resolved,
            name=front_text[:80] or resolved,
            subject=str(subject or "memory"),
            chapter=str(chapter or "memory_deck"),
            difficulty=_difficulty_to_float(difficulty, 0.5),
        )

        existing = self.store.get_fsrs_card(resolved)
        base = StudyFsrsCard.from_dict(
            existing.get("card") if existing else create_card(resolved).to_dict()
        )
        card = StudyFsrsCard.from_dict(
            {
                **base.to_dict(),
                "topic_id": resolved,
                "card_type": "memory",
                "front": front_text,
                "back": back_text,
                "source": str(source or "manual").strip() or "manual",
                "tags": normalize_tags(tags),
            }
        )
        self.store.upsert_fsrs_card(
            topic_id=resolved,
            card=card.to_dict(),
            last_rating=int((existing or {}).get("last_rating") or 0),
        )
        saved = self.store.get_fsrs_card(resolved)
        if saved is None:
            raise RuntimeError("memory card upsert failed")
        return {
            "created": existing is None,
            "card": self._memory_card_payload(saved),
        }

    def list_memory_cards(
        self,
        *,
        limit: int = 50,
        due_only: bool = False,
        include_topic_cards: bool = False,
    ) -> list[dict[str, Any]]:
        rows = self._memory_card_rows(include_topic_cards=include_topic_cards)
        due_items = self.fsrs.get_due_reviews([row["card"] for row in rows])
        due_by_topic = {str(item.get("topic_id") or ""): item for item in due_items}
        if due_only:
            row_by_topic = {str(row.get("topic_id") or ""): row for row in rows}
            ordered_rows = [
                row_by_topic[str(item.get("topic_id") or "")]
                for item in due_items
                if str(item.get("topic_id") or "") in row_by_topic
            ]
        else:
            ordered_rows = rows
        safe_limit = max(1, int(limit or 50))
        return [
            self._memory_card_payload(
                row, due_item=due_by_topic.get(str(row.get("topic_id") or ""))
            )
            for row in ordered_rows[:safe_limit]
        ]

    def get_memory_deck_status(self, *, limit: int = 8) -> dict[str, Any]:
        if self._memory_deck_summary_provider is not None:
            try:
                return self._memory_deck_summary_provider(limit=limit)
            except Exception as exc:
                self._log_quality_warning(
                    "Memory deck summary provider failed: %s", exc
                )
        rows = self._memory_card_rows(include_topic_cards=False)
        due_count = len(self.fsrs.get_due_reviews([row["card"] for row in rows]))
        return {
            "card_count": len(rows),
            "due_count": due_count,
            "cards": self.list_memory_cards(limit=limit, due_only=False),
            "due_cards": self.list_memory_cards(limit=limit, due_only=True),
        }

    def review_memory_card(
        self,
        *,
        topic_id: str,
        rating: str | int,
        answer: str = "",
    ) -> dict[str, Any]:
        resolved = self._resolve_topic_id(topic_id)
        if not resolved:
            raise ValueError("memory card topic_id is required")
        card_row = self.store.get_fsrs_card(resolved)
        if card_row is None:
            raise ValueError("memory card not found")
        selected = self._rating_from_review(rating)
        updated_card, schedule = rate_answer(card_row["card"], selected)
        self.store.upsert_fsrs_card(
            topic_id=resolved, card=updated_card.to_dict(), last_rating=int(selected)
        )
        self.store.append_review_log(
            topic_id=resolved,
            card_id=int(card_row.get("id") or 0) or None,
            rating=int(selected),
            scheduled_days=int(round(updated_card.scheduled_days)),
            actual_days=int(round(updated_card.elapsed_days)),
        )
        saved = self.store.get_fsrs_card(resolved)
        if saved is None:
            raise RuntimeError("memory card review failed")
        return {
            "topic_id": resolved,
            "rating": int(selected),
            "answer": str(answer or ""),
            "schedule": schedule,
            "card": self._memory_card_payload(saved),
        }

    def get_review_queue(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.store.list_fsrs_cards(limit=None)
        reviews = self.fsrs.get_due_reviews([row["card"] for row in rows])
        result: list[dict[str, Any]] = []
        for item in reviews[: max(1, int(limit))]:
            topic = self.store.get_topic(str(item.get("topic_id") or "")) or {}
            result.append(
                {**item, **self._card_metadata(item.get("card") or {}), "topic": topic}
            )
        return result

    def count_due_reviews(self) -> int:
        rows = self.store.list_fsrs_cards(limit=None)
        return len(self.fsrs.get_due_reviews([row["card"] for row in rows]))

    def get_weak_topics(self, limit: int = 5) -> list[dict[str, Any]]:
        overview = self.store.list_mastery_overview(limit=1000)
        weak = [
            item
            for item in overview
            if float(item.get("mastery") or 0.0) < 0.60
            or "false_mastery" in (item.get("flags") or [])
        ]
        weak.sort(
            key=lambda item: (
                float(item.get("mastery") or 0.0),
                -float(item.get("confidence") or 0.0),
            )
        )
        return weak[: max(1, int(limit))]

    def count_weak_topics(self) -> int:
        overview = self.store.list_mastery_overview(limit=5000)
        return sum(
            1
            for item in overview
            if float(item.get("mastery") or 0.0) < 0.60
            or "false_mastery" in (item.get("flags") or [])
        )

    def get_status_summary(self, *, limit: int = 8) -> dict[str, Any]:
        overview = self.store.list_mastery_overview(limit=limit)
        memory_deck = self.get_memory_deck_status(limit=limit)
        return {
            "topic_count": self.store.count_topics(),
            "tracked_topic_count": self.store.count_tracked_mastery_topics(),
            "average_mastery": round(float(self.store.average_latest_mastery()), 4),
            "weak_topic_count": self.count_weak_topics(),
            "due_review_count": self.count_due_reviews(),
            "memory_card_count": int(
                memory_deck.get("card_count") or memory_deck.get("item_count") or 0
            ),
            "last_updated_at": overview[0].get("updated_at") if overview else "",
            "candidate_quality": self.quality.status_summary(limit=limit),
        }

    def _memory_card_rows(self, *, include_topic_cards: bool) -> list[dict[str, Any]]:
        rows = self.store.list_fsrs_cards(limit=None)
        if include_topic_cards:
            return rows
        return [
            row
            for row in rows
            if str((row.get("card") or {}).get("card_type") or "topic") == "memory"
        ]

    def _memory_card_payload(
        self, row: dict[str, Any], *, due_item: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        card = StudyFsrsCard.from_dict(row.get("card") or {})
        topic_id = str(row.get("topic_id") or card.topic_id or "")
        topic = self.store.get_topic(topic_id) or {}
        resolved_due_item = due_item
        if resolved_due_item is None:
            due_reviews = self.fsrs.get_due_reviews([card])
            resolved_due_item = due_reviews[0] if due_reviews else None
        retrieval = (
            float(resolved_due_item.get("retrievability"))
            if resolved_due_item
            else retrievability(card)
        )
        return {
            "id": int(row.get("id") or 0),
            "topic_id": topic_id,
            "topic": topic,
            "front": card.front,
            "back": card.back,
            "tags": list(card.tags),
            "source": card.source,
            "card_type": card.card_type,
            "due": card.due,
            "is_due": resolved_due_item is not None,
            "retrievability": round(retrieval, 4),
            "stability": card.stability,
            "difficulty": card.difficulty,
            "state": card.state,
            "scheduled_days": card.scheduled_days,
            "reps": card.reps,
            "lapses": card.lapses,
            "last_review": card.last_review,
            "created_at": card.created_at,
            "updated_at": str(row.get("updated_at") or ""),
            "last_rating": int(row.get("last_rating") or 0),
            "priority": float((resolved_due_item or {}).get("priority") or 0.0),
            "overdue_days": float((resolved_due_item or {}).get("overdue_days") or 0.0),
            "card": card.to_dict(),
        }

    @staticmethod
    def _card_metadata(card_payload: object) -> dict[str, Any]:
        if not isinstance(card_payload, dict):
            return {}
        card = StudyFsrsCard.from_dict(card_payload)
        return {
            "card_type": card.card_type,
            "front": card.front,
            "back": card.back,
            "source": card.source,
            "tags": list(card.tags),
        }

    def _ensure_topic(
        self, topic_id: str, *, question: dict[str, Any], eval_result: dict[str, Any]
    ) -> str:
        resolved, topic_name, topic_existed = self._resolve_and_identify_topic(
            topic_id, question=question, eval_result=eval_result
        )
        if not topic_existed:
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
        if not topic_existed:
            self.graph.mark_dirty()
        return resolved

    def _resolve_and_identify_topic(
        self, topic_id: str, *, question: dict[str, Any], eval_result: dict[str, Any]
    ) -> tuple[str, str, bool]:
        raw_topic = str(
            topic_id or question.get("topic") or eval_result.get("topic") or ""
        ).strip()
        resolved = self._resolve_topic_id(raw_topic)
        topic_name = str(
            question.get("topic") or eval_result.get("topic") or resolved or "general"
        ).strip()
        if not resolved:
            resolved = _slug(topic_name)
        return resolved, topic_name, bool(self.store.get_topic(resolved))

    def _resolve_topic_id(self, topic: str) -> str:
        value = str(topic or "").strip()
        if not value:
            return ""
        indexed = self.graph.resolve_known_topic(value)
        if indexed:
            return indexed
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

    def _record_positive_question_type(
        self, *, topic_id: str, question: dict[str, Any]
    ) -> None:
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
            self._log_quality_warning(
                "record positive question type candidate failed: {}", exc
            )

    @staticmethod
    def _question_type_key(question: dict[str, Any]) -> str:
        explicit = str(
            question.get("question_type") or question.get("type") or ""
        ).strip()
        if explicit:
            return _slug(explicit)
        difficulty = question.get("difficulty")
        if difficulty not in (None, ""):
            return f"difficulty_{_difficulty_to_level(difficulty)}"
        return "general"

    def _log_quality_warning(self, message: str, *args: Any) -> None:
        self._quality_warning_failures += 1
        if self._quality_warning_failures >= 3:
            error = getattr(self._logger, "error", None)
            if callable(error):
                try:
                    error(message, *args)
                    self._quality_warning_failures = 0
                    return
                except Exception:
                    pass
        warning = getattr(self._logger, "warning", None)
        if callable(warning):
            try:
                warning(message, *args)
            except Exception:
                pass

    def _log_exception(self, message: str, *args: Any) -> None:
        exception = getattr(self._logger, "exception", None)
        if callable(exception):
            try:
                exception(message, *args)
                return
            except Exception:
                pass
        try:
            _LOGGER.exception(message.format(*args))
        except Exception:
            _LOGGER.exception(message)

    @staticmethod
    def _rating_from_review(value: str | int) -> StudyFsrsRating:
        if isinstance(value, str):
            normalized = value.strip().lower()
            aliases = {
                "again": StudyFsrsRating.Again,
                "forgot": StudyFsrsRating.Again,
                "fail": StudyFsrsRating.Again,
                "wrong": StudyFsrsRating.Again,
                "不会": StudyFsrsRating.Again,
                "忘记": StudyFsrsRating.Again,
                "hard": StudyFsrsRating.Hard,
                "partial": StudyFsrsRating.Hard,
                "difficult": StudyFsrsRating.Hard,
                "困难": StudyFsrsRating.Hard,
                "模糊": StudyFsrsRating.Hard,
                "good": StudyFsrsRating.Good,
                "ok": StudyFsrsRating.Good,
                "remembered": StudyFsrsRating.Good,
                "记得": StudyFsrsRating.Good,
                "easy": StudyFsrsRating.Easy,
                "perfect": StudyFsrsRating.Easy,
                "简单": StudyFsrsRating.Easy,
                "熟悉": StudyFsrsRating.Easy,
            }
            if normalized in aliases:
                return aliases[normalized]
        try:
            return StudyFsrsRating(int(value))
        except (TypeError, ValueError):
            return StudyFsrsRating.Good

    @staticmethod
    def _rating_from_eval(eval_result: dict[str, Any]) -> StudyFsrsRating:
        verdict = str(eval_result.get("verdict") or "").strip().lower()
        error_type = str(eval_result.get("error_type") or "").strip().lower()
        score = _score_percent(eval_result.get("score"))
        if verdict in {"wrong", "dont_know"} or error_type in {
            "concept_error",
            "misconception",
            "guess",
            "concept_missing",
        }:
            return StudyFsrsRating.Again
        if verdict == "partial" or error_type in {
            "calculation_error",
            "missing_step",
            "step_skipped",
        }:
            return StudyFsrsRating.Hard
        if verdict == "correct" and score >= 92:
            return StudyFsrsRating.Easy
        return StudyFsrsRating.Good

    @staticmethod
    def _question_guidance(
        mastery: float, *, blockers: list[str], retry: dict[str, Any] | None
    ) -> str:
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
