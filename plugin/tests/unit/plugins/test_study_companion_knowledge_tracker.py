from __future__ import annotations

import sqlite3
from pathlib import Path

from plugin.plugins.study_companion.knowledge_tracker import KnowledgeTracker, MasteryTracker
from plugin.plugins.study_companion.store import StudyStore


class _Logger:
    def warning(self, *args, **kwargs):
        return None


def _store(tmp_path: Path) -> StudyStore:
    seed = Path(__file__).resolve().parents[3] / "plugins" / "study_companion" / "static" / "knowledge_graph_seed.json"
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger(), seed)
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


def test_knowledge_tracker_on_answer_updates_mastery_wrong_question_and_fsrs(tmp_path: Path) -> None:
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
            eval_result={"verdict": "wrong", "score": 20, "error_type": "sign_reversal"},
            mode="teaching",
        )

        assert result["mastery"]["topic_id"] == "quadratic_vertex_form"
        assert result["wrong_question_id"]
        assert store.get_latest_mastery("quadratic_vertex_form") is not None
        assert store.get_fsrs_card("quadratic_vertex_form") is not None
        assert store.list_wrong_questions(topic_id="quadratic_vertex_form")[0]["error_type"] == "sign_reversal"
        assert tracker.get_review_queue(limit=3)
    finally:
        store.close()


def test_wrong_question_resolves_after_three_delayed_correct_variants(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        wrong_id = tracker.on_answer(
            topic_id="linear_function_kb",
            question={"question": "k 表示什么？", "answer": "斜率", "difficulty": 3},
            user_answer="截距",
            eval_result={"verdict": "wrong", "score": 10, "error_type": "misunderstanding"},
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
                question={"question": "k 的几何意义是什么？", "answer": "斜率", "difficulty": 3},
                user_answer="斜率",
                eval_result={"verdict": "correct", "score": 90, "error_type": "none"},
                mode="interactive",
            )

        resolved = store.list_wrong_questions(topic_id="linear_function_kb", statuses=("resolved",))
        assert resolved and resolved[0]["id"] == wrong_id
        assert resolved[0]["consecutive_correct"] >= 3
    finally:
        store.close()


def test_knowledge_seed_loads_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        first_count = store.count_topics()
        assert 120 <= first_count <= 150
        loaded_again = store.load_knowledge_seed()
        assert loaded_again == first_count
        assert store.count_topics() == first_count
    finally:
        store.close()
