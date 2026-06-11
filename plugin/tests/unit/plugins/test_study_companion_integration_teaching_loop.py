from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion.knowledge_tracker import KnowledgeTracker
from plugin.plugins.study_companion.mode_manager import MODE_TEACHING
from plugin.plugins.study_companion.store import StudyStore

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        return None


def _store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return store


def test_integration_teaching_answer_updates_tracking_and_next_question_context(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    try:
        tracker = KnowledgeTracker(store)
        store.ensure_topic(
            topic_id="calculus-derivative",
            name="Derivative",
            subject="math",
            chapter="calculus",
            difficulty=0.6,
        )

        result = tracker.on_answer(
            topic_id="calculus-derivative",
            question={
                "question": "What does a derivative measure?",
                "answer": "instantaneous change",
                "difficulty": 3,
            },
            user_answer="change",
            eval_result={
                "verdict": "partial",
                "score": 60,
                "error_type": "missing_precision",
                "feedback": "Need instantaneous.",
            },
            mode=MODE_TEACHING,
            session_id="session-1",
        )
        next_params = tracker.get_next_question_params("calculus-derivative")

        assert result["mastery"]["topic_id"] == "calculus-derivative"
        assert result["wrong_question_id"]
        assert next_params["target_topic"]["id"] == "calculus-derivative"
        assert next_params["retry_wrong_question"]["error_type"] == "missing_precision"
    finally:
        store.close()
