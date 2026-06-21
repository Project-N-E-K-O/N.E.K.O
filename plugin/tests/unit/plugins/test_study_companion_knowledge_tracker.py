from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins.study_companion.fsrs_bridge import StudyFsrsRating
from plugin.plugins.study_companion.knowledge_tracker import (
    KnowledgeGraph,
    KnowledgeTracker,
    MasteryTracker,
    _difficulty_to_float,
    _difficulty_to_level,
)
from plugin.plugins.study_companion.store import StudyStore

pytestmark = pytest.mark.unit


class _Logger:
    def __init__(self) -> None:
        self.exceptions: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def warning(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        self.exceptions.append((args, kwargs))
        return None


def _store(tmp_path: Path, logger: Any | None = None) -> StudyStore:
    seed = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )
    store = StudyStore(
        tmp_path / "study.db", tmp_path / "seed.json", logger or _Logger(), seed
    )
    store.open()
    return store


def test_mastery_tracker_levels_confidence_and_false_mastery() -> None:
    tracker = MasteryTracker()
    assert tracker.get_level(0.10) == "未接触"
    assert tracker.get_level(0.35) == "薄弱"
    assert tracker.get_level(0.55) == "进行中"
    assert tracker.get_level(0.75) == "熟练"
    assert tracker.get_level(0.95) == "掌握"

    first = tracker.update("linear_equation", {"verdict": "correct", "difficulty": 0.5})
    repeated = tracker.update(
        "linear_equation",
        {"verdict": "correct", "difficulty": 0.5},
        recent_results=[{"verdict": "correct"} for _ in range(5)],
    )
    shaky = tracker.update(
        "linear_equation",
        {"verdict": "correct", "difficulty": 0.5},
        recent_results=[
            {"verdict": "correct"},
            {"verdict": "wrong"},
            {"verdict": "correct"},
            {"verdict": "wrong"},
            {"verdict": "correct"},
        ],
    )

    assert first.confidence < repeated.confidence
    assert repeated.mastery > first.mastery
    assert "false_mastery" in shaky.flags


def test_difficulty_integer_levels_are_scaled_from_one_to_five() -> None:
    assert _difficulty_to_float(1) == 0.2
    assert _difficulty_to_float(1.0) == 0.2
    assert _difficulty_to_float("1") == 0.2
    assert _difficulty_to_float("1.0") == 0.2
    assert _difficulty_to_float(3) == 0.6
    assert _difficulty_to_float(3.0) == 0.6
    assert _difficulty_to_float(5) == 1.0
    assert _difficulty_to_float(5.0) == 1.0
    assert _difficulty_to_float(0.5) == 0.5
    assert _difficulty_to_level(None) == 3
    assert _difficulty_to_level(0.5) == 3
    assert _difficulty_to_level(2.5) == 3
    assert _difficulty_to_level(3) == 3
    assert _difficulty_to_level(5) == 5


def test_knowledge_tracker_on_answer_updates_mastery_wrong_question_and_fsrs(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        result = tracker.on_answer(
            topic_id="quadratic_vertex_form",
            question={
                "question": "写出二次函数顶点式。",
                "answer": "y=a(x-h)^2+k",
                "topic": "二次函数顶点式",
                "difficulty": 3,
            },
            user_answer="y=a(x+h)^2+k",
            eval_result={
                "verdict": "wrong",
                "score": 20,
                "error_type": "sign_reversal",
            },
            mode="teaching",
        )

        assert result["mastery"]["topic_id"] == "quadratic_vertex_form"
        assert result["wrong_question_id"]
        assert store.get_latest_mastery("quadratic_vertex_form") is not None
        assert tracker.get_mastery("二次函数顶点式") == tracker.get_mastery(
            "quadratic_vertex_form"
        )
        assert store.get_fsrs_card("quadratic_vertex_form") is not None
        assert (
            store.list_wrong_questions(topic_id="quadratic_vertex_form")[0][
                "error_type"
            ]
            == "sign_reversal"
        )
        assert tracker.get_review_queue(limit=3)
    finally:
        store.close()


def test_knowledge_tracker_on_answer_uses_batch_writer_for_known_topic(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        original_batch = getattr(store, "batch_write_answer_data", None)
        calls: list[dict[str, Any]] = []

        def batch_write_answer_data(**kwargs):
            calls.append(dict(kwargs))
            if callable(original_batch):
                return original_batch(**kwargs)
            return {"ok": True}

        store.batch_write_answer_data = batch_write_answer_data  # type: ignore[attr-defined, method-assign]

        tracker.on_answer(
            topic_id="quadratic_vertex_form",
            question={
                "question": "q",
                "answer": "a",
                "topic": "quadratic_vertex_form",
                "difficulty": 3,
            },
            user_answer="a",
            eval_result={"verdict": "correct", "score": 95, "error_type": "none"},
            mode="teaching",
            session_id="batch-session",
        )

        assert len(calls) == 1
        assert calls[0]["session_id"] == "batch-session"
        assert calls[0]["mastery_snapshot"]["topic_id"] == "quadratic_vertex_form"
        assert calls[0]["fsrs_card"]["topic_id"] == "quadratic_vertex_form"
    finally:
        store.close()


def test_knowledge_tracker_falls_back_when_batch_writer_fails(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    logger = _Logger()
    try:
        tracker = KnowledgeTracker(store, logger=logger)

        def batch_write_answer_data(**_kwargs):
            raise sqlite3.OperationalError("database is locked")

        store.batch_write_answer_data = batch_write_answer_data  # type: ignore[attr-defined, method-assign]

        result = tracker.on_answer(
            topic_id="quadratic_vertex_form",
            question={
                "question": "q",
                "answer": "a",
                "topic": "quadratic_vertex_form",
                "difficulty": 3,
            },
            user_answer="a",
            eval_result={"verdict": "correct", "score": 95, "error_type": "none"},
            mode="teaching",
            session_id="fallback-session",
        )

        assert result["topic_id"] == "quadratic_vertex_form"
        assert store.list_qa_records_for_topic("quadratic_vertex_form", limit=1)
        assert logger.exceptions
        assert "batch answer write failed" in str(logger.exceptions[-1][0][0])
    finally:
        store.close()


def test_knowledge_tracker_batch_fallback_runs_outside_answer_lock(
    tmp_path: Path,
) -> None:
    class _TrackingLock:
        def __init__(self) -> None:
            self.held = False

        def __enter__(self):
            self.held = True
            return self

        def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
            del exc_type, exc, traceback
            self.held = False
            return False

    store = _store(tmp_path)
    lock = _TrackingLock()
    try:
        tracker = KnowledgeTracker(store)
        legacy_calls: list[dict[str, Any]] = []

        def batch_write_answer_data(**_kwargs):
            raise sqlite3.OperationalError("database is locked")

        def on_answer_legacy(**kwargs):
            assert lock.held is False
            legacy_calls.append(dict(kwargs))
            return {
                "topic_id": kwargs["topic_id"],
                "mastery": {},
                "wrong_question_id": "",
                "fsrs": {},
            }

        store.answer_write_lock = lambda: lock  # type: ignore[method-assign]
        store.batch_write_answer_data = batch_write_answer_data  # type: ignore[attr-defined, method-assign]
        tracker._on_answer_legacy = on_answer_legacy  # type: ignore[method-assign]

        result = tracker.on_answer(
            topic_id="quadratic_vertex_form",
            question={
                "question": "q",
                "answer": "a",
                "topic": "quadratic_vertex_form",
                "difficulty": 3,
            },
            user_answer="a",
            eval_result={"verdict": "correct", "score": 95, "error_type": "none"},
            mode="teaching",
            session_id="fallback-lock-session",
        )

        assert result["topic_id"] == "quadratic_vertex_form"
        assert len(legacy_calls) == 1
    finally:
        store.close()


def test_study_store_batch_write_answer_data_rolls_back_on_error(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.batch_write_answer_data(
                session_id="rollback-session",
                mode="teaching",
                topic_id="missing-topic",
                question={"question": "q"},
                user_answer="a",
                eval_result={"verdict": "correct"},
                response_time_ms=None,
                fsrs_card={"topic_id": "missing-topic", "state": "new"},
                fsrs_rating=3,
            )

        assert store.list_qa_records(limit=10) == []
        assert store.list_sessions(limit=10) == []
    finally:
        store.close()


def test_study_store_batch_write_answer_data_keeps_answer_when_candidates_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = _Logger()
    store = _store(tmp_path, logger)
    try:
        store.ensure_topic(topic_id="candidate_fail_topic", name="Candidate Fail")

        def fail_candidates(*_args, **_kwargs):
            raise sqlite3.OperationalError("candidate write failed")

        monkeypatch.setattr(store, "_batch_write_answer_candidates", fail_candidates)

        result = store.batch_write_answer_data(
            session_id="candidate-failure-session",
            mode="teaching",
            topic_id="candidate_fail_topic",
            question={"question": "q"},
            user_answer="a",
            eval_result={"verdict": "correct"},
            response_time_ms=None,
            positive_candidate_data={
                "item_type": "question_type",
                "dedupe_key": "candidate-fail",
                "payload": {"topic_id": "candidate_fail_topic"},
            },
        )

        assert result["ok"] is True
        assert store.list_qa_records_for_topic("candidate_fail_topic", limit=1)
        assert logger.exceptions
        assert "candidate knowledge write failed" in str(logger.exceptions[-1][0][0])
    finally:
        store.close()


def test_knowledge_graph_reuses_topic_name_index() -> None:
    class _Store:
        def __init__(self) -> None:
            self.list_calls = 0

        def list_topics(self, limit: int = 1000):
            self.list_calls += 1
            return [{"id": "known_topic", "name": "Known Topic"}]

        def list_mastery_overview(self, limit: int = 1000):
            return []

        def get_topic(self, topic_id: str):
            return None

        def find_topic_by_name(self, name: str):
            return None

    store = _Store()
    graph = KnowledgeGraph(store)

    assert graph.discover_candidate("Known Topic appears here") == "known_topic"
    assert graph.discover_candidate("Known Topic appears again") == "known_topic"
    assert store.list_calls == 1


def test_knowledge_graph_prefers_topic_id_over_name_collision() -> None:
    class _Store:
        def list_topics(self, limit: int = 1000):
            return [
                {"id": "collision", "name": "Collision Topic"},
                {"id": "other_topic", "name": "collision"},
            ]

        def list_mastery_overview(self, limit: int = 1000):
            return []

        def get_topic(self, topic_id: str):
            return None

    graph = KnowledgeGraph(_Store())

    assert graph.resolve_known_topic("collision") == "collision"


def test_knowledge_graph_checks_store_when_topic_index_is_truncated() -> None:
    class _Store:
        def __init__(self) -> None:
            self.find_calls: list[str] = []

        def list_topics(self, limit: int = 1000):
            return [
                {"id": f"topic_{index}", "name": f"Topic {index}"}
                for index in range(limit)
            ]

        def count_topics(self) -> int:
            return 1001

        def find_topic_by_name(self, name: str):
            self.find_calls.append(str(name))
            if name == "Late Topic":
                return {"id": "late_topic", "name": "Late Topic"}
            return None

        def get_topic(self, topic_id: str):
            if topic_id == "late_topic":
                return {"id": "late_topic", "name": "Late Topic"}
            return None

        def list_mastery_overview(self, limit: int = 1000):
            return []

    store = _Store()
    graph = KnowledgeGraph(store)

    assert graph.discover_candidate("", {"topic": "Late Topic"}) == "late_topic"
    assert graph.discover_candidate("Late Topic") == "late_topic"
    assert graph.discover_candidate("Before Late Topic after") is None
    assert store.find_calls == [
        "Late Topic",
        "Late Topic",
        "Before Late Topic after",
    ]


def test_knowledge_tracker_batch_gate_requires_full_batch_store_surface() -> None:
    class _PartialStore:
        _supports_batch_answer = True

        def batch_write_answer_data(self, **_kwargs):
            return {"ok": True}

    tracker = KnowledgeTracker.__new__(KnowledgeTracker)
    tracker.store = _PartialStore()

    assert tracker._can_batch_answer_data() is False


def test_quality_warning_failure_counter_resets_after_error_escalation() -> None:
    class _LoggerWithError(_Logger):
        def __init__(self) -> None:
            super().__init__()
            self.errors: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def error(self, *args, **kwargs):
            self.errors.append((args, kwargs))
            return None

    logger = _LoggerWithError()
    tracker = KnowledgeTracker.__new__(KnowledgeTracker)
    tracker._logger = logger
    tracker._quality_warning_failures = 2

    tracker._log_quality_warning("quality warning {}", "failed")

    assert len(logger.errors) == 1
    assert tracker._quality_warning_failures == 0


def test_knowledge_tracker_marks_graph_index_dirty_after_batch_topic_insert(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        assert tracker.graph.discover_candidate(
            "", {"topic": "quadratic_vertex_form"}
        ) == "quadratic_vertex_form"

        tracker.on_answer(
            topic_id="Fresh Runtime Topic",
            question={
                "question": "q",
                "answer": "a",
                "topic": "Fresh Runtime Topic",
            },
            user_answer="a",
            eval_result={"verdict": "correct", "score": 95},
            mode="teaching",
            session_id="fresh-topic-session",
        )

        assert tracker.graph.discover_candidate(
            "", {"topic": "Fresh Runtime Topic"}
        ) == "fresh_runtime_topic"
    finally:
        store.close()


def test_status_summary_due_review_count_is_not_limited(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        due_at = (
            (datetime.now(timezone.utc) - timedelta(days=1))
            .isoformat()
            .replace("+00:00", "Z")
        )
        for index in range(12):
            topic_id = f"review_topic_{index}"
            store.ensure_topic(topic_id=topic_id, name=f"Review Topic {index}")
            card = tracker.fsrs.new_knowledge_card(topic_id).to_dict()
            card["due"] = due_at
            store.upsert_fsrs_card(topic_id=topic_id, card=card, last_rating=3)

        summary = tracker.get_status_summary(limit=8)

        assert len(tracker.get_review_queue(limit=8)) == 8
        assert summary["due_review_count"] == 12
    finally:
        store.close()


def test_knowledge_tracker_persists_runtime_topic_before_tracking(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        result = tracker.on_answer(
            topic_id="Runtime Algebra",
            question={
                "question": "runtime check",
                "answer": "x",
                "topic": "Runtime Algebra",
                "difficulty": 2,
            },
            user_answer="x",
            eval_result={"verdict": "correct", "score": "92/100", "error_type": "none"},
            mode="interactive",
        )

        assert store.get_topic("runtime_algebra") is not None
        assert result["mastery"]["topic_id"] == "runtime_algebra"
        assert store.get_latest_mastery("runtime_algebra") is not None
        assert store.get_fsrs_card("runtime_algebra") is not None
    finally:
        store.close()


def test_review_queue_considers_due_cards_beyond_first_1000_fsrs_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        now = datetime.now(timezone.utc)
        due_at = (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        future_at = (now + timedelta(days=3650)).isoformat().replace("+00:00", "Z")
        reviewed_at = now.isoformat().replace("+00:00", "Z")

        due_topic_id = "zz_due_beyond_1000"
        store.ensure_topic(topic_id=due_topic_id, name="Due Beyond 1000")
        due_card = tracker.fsrs.new_knowledge_card(due_topic_id).to_dict()
        due_card["due"] = due_at
        due_card["last_review"] = (
            (now - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        )
        due_card["stability"] = 2.0
        store.upsert_fsrs_card(topic_id=due_topic_id, card=due_card, last_rating=3)

        for index in range(1000):
            topic_id = f"fresh_topic_{index:04d}"
            store.ensure_topic(topic_id=topic_id, name=f"Fresh Topic {index}")
            card = tracker.fsrs.new_knowledge_card(topic_id).to_dict()
            card["due"] = future_at
            card["last_review"] = reviewed_at
            card["stability"] = 10000.0
            store.upsert_fsrs_card(topic_id=topic_id, card=card, last_rating=3)

        queue = tracker.get_review_queue(limit=1)

        assert [item["topic_id"] for item in queue] == [due_topic_id]
        assert queue[0]["topic"]["name"] == "Due Beyond 1000"
    finally:
        store.close()


def test_memory_card_deck_create_list_and_review_cycle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)

        created = tracker.upsert_memory_card(
            topic_id="phase7_memory_card",
            front="What does spaced repetition optimize?",
            back="The timing of future reviews.",
            subject="learning",
            tags=["phase7", "memory", "phase7"],
        )

        assert created["created"] is True
        assert created["card"]["topic_id"] == "phase7_memory_card"
        assert created["card"]["front"] == "What does spaced repetition optimize?"
        assert created["card"]["back"] == "The timing of future reviews."
        assert created["card"]["tags"] == ["phase7", "memory"]
        assert created["card"]["is_due"] is True

        status = tracker.get_memory_deck_status(limit=5)
        assert status["card_count"] == 1
        assert status["due_count"] == 1
        assert status["due_cards"][0]["topic_id"] == "phase7_memory_card"
        assert (
            tracker.get_review_queue(limit=1)[0]["front"]
            == "What does spaced repetition optimize?"
        )

        reviewed = tracker.review_memory_card(
            topic_id="phase7_memory_card",
            rating="easy",
            answer="It optimizes review timing.",
        )

        assert reviewed["rating"] == 4
        assert reviewed["card"]["front"] == "What does spaced repetition optimize?"
        assert reviewed["card"]["back"] == "The timing of future reviews."
        assert reviewed["card"]["reps"] == 1
        assert tracker.list_memory_cards(limit=5, due_only=True) == []
        assert store.list_review_log(limit=5)[0]["topic_id"] == "phase7_memory_card"
    finally:
        store.close()


def test_memory_deck_summary_provider_overrides_legacy_memory_cards(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        tracker.set_memory_deck_summary_provider(
            lambda *, limit=8: {
                "item_count": 7,
                "due_count": 3,
                "decks": [{"name": "Real Deck"}],
                "due_reviews": [{"item_id": "real-item"}],
                "limit": limit,
            }
        )

        session = tracker.get_session_summary()
        status = tracker.get_status_summary(limit=4)

        assert session["memory_deck"]["item_count"] == 7
        assert session["memory_deck"]["decks"][0]["name"] == "Real Deck"
        assert status["memory_card_count"] == 7
    finally:
        store.close()


def test_memory_deck_summary_provider_falls_back_on_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        tracker.upsert_memory_card(
            topic_id="fallback_topic",
            front="Front",
            back="Back",
        )

        def failing_provider(*, limit: int = 8) -> dict[str, Any]:
            raise RuntimeError("provider unavailable")

        tracker.set_memory_deck_summary_provider(failing_provider)

        status = tracker.get_memory_deck_status(limit=4)

        assert status["card_count"] == 1
        assert status["due_count"] == 1
        assert status["cards"][0]["topic_id"] == "fallback_topic"
    finally:
        store.close()


def test_rating_from_eval_handles_dirty_score_strings() -> None:
    assert (
        KnowledgeTracker._rating_from_eval({"verdict": "correct", "score": "92/100"})
        == StudyFsrsRating.Easy
    )
    assert (
        KnowledgeTracker._rating_from_eval({"verdict": "correct", "score": "92%"})
        == StudyFsrsRating.Easy
    )
    assert (
        KnowledgeTracker._rating_from_eval({"verdict": "correct", "score": "A"})
        == StudyFsrsRating.Easy
    )
    assert (
        KnowledgeTracker._rating_from_eval({"verdict": "correct", "score": "n/a"})
        == StudyFsrsRating.Good
    )


def test_append_only_knowledge_tables_trim_per_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        topic_id = "quadratic_vertex_form"
        store.ensure_session(session_id="trim-session", mode="teaching")
        candidate = store.upsert_candidate_item(
            item_type="question_type",
            payload={"topic_id": topic_id, "question_type": "trim"},
            source="test",
            dedupe_key="trim-question-type",
        )

        for index in range(3):
            store.append_mastery_snapshot(
                {
                    "topic_id": topic_id,
                    "mastery": index / 10,
                    "accuracy": 0.5,
                    "recency": 0.5,
                    "consistency": 0.5,
                    "confidence": 0.5,
                    "level": "test",
                    "attempts": index,
                    "flags": [],
                },
                history_limit=2,
            )
            store.add_qa_record(
                session_id="trim-session",
                topic_id=topic_id,
                question={"question": f"q{index}"},
                user_answer="answer",
                eval_result={"verdict": "correct"},
                mode="teaching",
                history_limit=2,
            )
            store.append_review_log(
                topic_id=topic_id,
                card_id=None,
                rating=3,
                scheduled_days=1,
                actual_days=0,
                history_limit=2,
            )
            store.add_knowledge_evidence(
                item_id=candidate["id"],
                event_type="mentioned",
                weight=0.2,
                context={"index": index},
                history_limit=2,
            )

        with sqlite3.connect(store.db_path) as conn:
            counts = {
                table: conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} = ?",
                    (key,),
                ).fetchone()[0]
                for table, column, key in (
                    ("mastery_snapshots", "topic_id", topic_id),
                    ("qa_records", "topic_id", topic_id),
                    ("review_log", "topic_id", topic_id),
                    ("knowledge_evidence", "item_id", candidate["id"]),
                )
            }

        assert counts == {
            "mastery_snapshots": 2,
            "qa_records": 2,
            "review_log": 2,
            "knowledge_evidence": 2,
        }
    finally:
        store.close()


def test_add_qa_record_trims_unknown_topic_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store.ensure_session(session_id="unknown-trim-session", mode="interactive")
        for index in range(3):
            store.add_qa_record(
                session_id="unknown-trim-session",
                topic_id="",
                question={"question": f"unknown q{index}"},
                user_answer="answer",
                eval_result={"verdict": "correct"},
                mode="interactive",
                history_limit=2,
            )

        with sqlite3.connect(store.db_path) as conn:
            rows = conn.execute(
                """
                SELECT question
                FROM qa_records
                WHERE topic_id IS NULL
                ORDER BY id
                """
            ).fetchall()

        questions = [json.loads(row[0])["question"] for row in rows]
        assert questions == ["unknown q1", "unknown q2"]
        assert [
            item["question"]["question"]
            for item in store.list_qa_records_for_topic("", limit=5)
        ] == [
            "unknown q1",
            "unknown q2",
        ]
    finally:
        store.close()


def test_runtime_topic_answer_records_are_pruned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        store._supports_batch_answer = False
        original_add_qa_record = store.add_qa_record

        def capped_add_qa_record(**kwargs):
            kwargs["history_limit"] = 2
            return original_add_qa_record(**kwargs)

        store.add_qa_record = capped_add_qa_record  # type: ignore[method-assign]
        tracker = KnowledgeTracker(store)
        for index in range(3):
            tracker.on_answer(
                topic_id="",
                question={
                    "topic": "runtime topic",
                    "question": f"q{index}",
                    "answer": "a",
                },
                user_answer="a",
                eval_result={"verdict": "correct", "score": 90},
                mode="interactive",
            )

        with sqlite3.connect(store.db_path) as conn:
            rows = conn.execute(
                """
                SELECT question
                FROM qa_records
                WHERE topic_id = ?
                ORDER BY id
                """,
                ("runtime_topic",),
            ).fetchall()

        questions = [json.loads(row[0])["question"] for row in rows]
        assert store.get_topic("runtime_topic") is not None
        assert questions == ["q1", "q2"]
    finally:
        store.close()


def test_wrong_question_resolves_after_three_delayed_correct_variants(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        wrong_id = tracker.on_answer(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "answer": "斜率", "difficulty": 3},
            user_answer="截距",
            eval_result={
                "verdict": "wrong",
                "score": 10,
                "error_type": "misunderstanding",
            },
            mode="interactive",
        )["wrong_question_id"]
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE wrong_questions SET last_error_at = datetime('now', '-2 days') WHERE id = ?",
                (wrong_id,),
            )

        for _ in range(3):
            tracker.on_answer(
                topic_id="linear_function_kb",
                question={
                    "question": "k 的几何意义是什么？",
                    "answer": "斜率",
                    "difficulty": 3,
                },
                user_answer="斜率",
                eval_result={"verdict": "correct", "score": 90, "error_type": "none"},
                mode="interactive",
            )

        resolved = store.list_wrong_questions(
            topic_id="linear_function_kb", statuses=("resolved",)
        )
        assert resolved and resolved[0]["id"] == wrong_id
        assert resolved[0]["consecutive_correct"] >= 3
    finally:
        store.close()


def test_wrong_question_resolves_with_default_medium_difficulty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        wrong_id = tracker.on_answer(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "answer": "斜率"},
            user_answer="截距",
            eval_result={
                "verdict": "wrong",
                "score": 10,
                "error_type": "misunderstanding",
            },
            mode="interactive",
        )["wrong_question_id"]
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE wrong_questions SET last_error_at = datetime('now', '-2 days') WHERE id = ?",
                (wrong_id,),
            )

        for _ in range(3):
            tracker.on_answer(
                topic_id="linear_function_kb",
                question={"question": "k 的几何意义是什么？", "answer": "斜率"},
                user_answer="斜率",
                eval_result={"verdict": "correct", "score": 90, "error_type": "none"},
                mode="interactive",
            )

        resolved = store.list_wrong_questions(
            topic_id="linear_function_kb", statuses=("resolved",)
        )
        assert resolved and resolved[0]["id"] == wrong_id
        assert resolved[0]["max_correct_difficulty"] == 3
    finally:
        store.close()


def test_easy_level_difficulty_does_not_resolve_wrong_question_as_hard_evidence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        wrong_id = tracker.on_answer(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "answer": "斜率", "difficulty": 3},
            user_answer="截距",
            eval_result={
                "verdict": "wrong",
                "score": 10,
                "error_type": "misunderstanding",
            },
            mode="interactive",
        )["wrong_question_id"]
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE wrong_questions SET last_error_at = datetime('now', '-2 days') WHERE id = ?",
                (wrong_id,),
            )

        for _ in range(3):
            tracker.on_answer(
                topic_id="linear_function_kb",
                question={
                    "question": "k 是什么？",
                    "answer": "斜率",
                    "difficulty": 1.0,
                },
                user_answer="斜率",
                eval_result={"verdict": "correct", "score": 90, "error_type": "none"},
                mode="interactive",
            )

        active = store.list_wrong_questions(
            topic_id="linear_function_kb", statuses=("retrying",)
        )
        assert active and active[0]["id"] == wrong_id
        assert active[0]["max_correct_difficulty"] == 1
        assert (
            store.list_wrong_questions(
                topic_id="linear_function_kb", statuses=("resolved",)
            )
            == []
        )
    finally:
        store.close()


def test_generic_correct_answer_does_not_advance_unrelated_wrong_questions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        first_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "difficulty": 3},
            user_answer="截距",
            expected_answer="斜率",
            error_type="misunderstanding",
            verdict="wrong",
        )
        second_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "b 表示什么？", "difficulty": 3},
            user_answer="斜率",
            expected_answer="截距",
            error_type="symbol_confusion",
            verdict="wrong",
        )
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                "UPDATE wrong_questions SET last_error_at = datetime('now', '-2 days') WHERE id IN (?, ?)",
                (first_id, second_id),
            )

        for _ in range(3):
            store.record_wrong_question_correct(
                topic_id="linear_function_kb",
                error_type="none",
                difficulty=3,
            )

        rows = store.list_wrong_questions(
            topic_id="linear_function_kb", statuses=("active", "retrying", "resolved")
        )
        by_id = {row["id"]: row for row in rows}
        resolved = [row for row in rows if row["status"] == "resolved"]
        untouched = [row for row in rows if row["consecutive_correct"] == 0]

        assert len(resolved) == 1
        assert len(untouched) == 1
        assert {first_id, second_id} == set(by_id)
    finally:
        store.close()


def test_correct_answer_advances_only_one_wrong_question_per_error_type(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        first_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "first misconception", "difficulty": 3},
            user_answer="wrong",
            expected_answer="right",
            error_type="misunderstanding",
            verdict="wrong",
        )
        second_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "second misconception", "difficulty": 3},
            user_answer="wrong",
            expected_answer="right",
            error_type="misunderstanding",
            verdict="wrong",
        )

        store.record_wrong_question_correct(
            topic_id="linear_function_kb",
            error_type="misunderstanding",
            difficulty=3,
        )

        rows = store.list_wrong_questions(
            topic_id="linear_function_kb", statuses=("active", "retrying")
        )
        by_id = {row["id"]: row for row in rows}
        advanced = [row for row in rows if row["consecutive_correct"] == 1]
        untouched = [row for row in rows if row["consecutive_correct"] == 0]

        assert len(advanced) == 1
        assert len(untouched) == 1
        assert {first_id, second_id} == set(by_id)
    finally:
        store.close()


def test_get_retry_wrong_question_matches_correct_selection_order(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        retry_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "retry candidate", "difficulty": 3},
            user_answer="wrong",
            expected_answer="right",
            error_type="misunderstanding",
            verdict="wrong",
        )
        active_id = store.add_wrong_question(
            topic_id="linear_function_kb",
            question={"question": "recent active", "difficulty": 3},
            user_answer="wrong",
            expected_answer="right",
            error_type="misunderstanding",
            verdict="wrong",
        )
        with sqlite3.connect(store.db_path) as conn:
            conn.execute(
                """
                UPDATE wrong_questions
                SET status = 'retrying',
                    last_retry_at = datetime('now', '-1 day'),
                    created_at = datetime('now', '-2 days'),
                    updated_at = datetime('now', '-2 days')
                WHERE id = ?
                """,
                (retry_id,),
            )
            conn.execute(
                """
                UPDATE wrong_questions
                SET created_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (active_id,),
            )

        assert store.get_retry_wrong_question("linear_function_kb")["id"] == retry_id

        store.record_wrong_question_correct(
            topic_id="linear_function_kb",
            error_type="misunderstanding",
            difficulty=3,
        )
        rows = store.list_wrong_questions(
            topic_id="linear_function_kb", statuses=("active", "retrying")
        )
        by_id = {row["id"]: row for row in rows}
        assert by_id[retry_id]["consecutive_correct"] == 1
        assert by_id[active_id]["consecutive_correct"] == 0
    finally:
        store.close()


def test_knowledge_seed_loads_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first_count = store.count_topics()
        assert first_count > 0
        loaded_again = store.load_knowledge_seed()
        assert loaded_again == first_count
        assert store.count_topics() == first_count
    finally:
        store.close()


def test_knowledge_seed_and_topic_upsert_tolerate_bad_numeric_fields(
    tmp_path: Path,
) -> None:
    knowledge_seed = tmp_path / "bad_knowledge_seed.json"
    knowledge_seed.write_text(
        json.dumps(
            {
                "subject": "math",
                "topics": [
                    {
                        "id": "bad_numeric_topic",
                        "name": "Bad Numeric Topic",
                        "stage": "senior_high",
                        "depth": "not-an-int",
                        "difficulty": "not-a-float",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    store = StudyStore(
        tmp_path / "study.db", tmp_path / "seed.json", _Logger(), knowledge_seed
    )
    store.open()
    try:
        topic = store.get_topic("bad_numeric_topic")
        assert topic is not None
        assert topic["stage"] == "senior_high"
        assert topic["depth"] == 1
        assert topic["difficulty"] == 0.5

        store.upsert_topic(
            {
                "id": "bad_runtime_topic",
                "name": "Bad Runtime Topic",
                "stage": "college",
                "depth": "still-not-an-int",
                "difficulty": "still-not-a-float",
            }
        )
        runtime_topic = store.get_topic("bad_runtime_topic")
        assert runtime_topic is not None
        assert runtime_topic["stage"] == "college"
        assert runtime_topic["depth"] == 1
        assert runtime_topic["difficulty"] == 0.5
        assert [item["id"] for item in store.list_topics(stage="college")] == [
            "bad_runtime_topic"
        ]

        store.upsert_topic(
            {
                "id": "zero_numeric_topic",
                "name": "Zero Numeric Topic",
                "depth": 0,
                "difficulty": 0.0,
            }
        )
        zero_topic = store.get_topic("zero_numeric_topic")
        assert zero_topic is not None
        assert zero_topic["depth"] == 0
        assert zero_topic["difficulty"] == 0.0
    finally:
        store.close()
