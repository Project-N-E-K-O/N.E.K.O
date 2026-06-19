from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion.mode_manager import (
    MODE_COMPANION,
    MODE_TEACHING,
    ModeManager,
)
from plugin.plugins.study_companion.models import StudyConfig
from plugin.plugins.study_companion.service import build_status_payload
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.store import StudyStore

pytestmark = pytest.mark.unit


class _Logger:
    def warning(self, *args: object, **kwargs: object) -> None:
        return None


def _store(tmp_path: Path) -> StudyStore:
    store = StudyStore(tmp_path / "study.db", tmp_path / "seed.json", _Logger())
    store.open()
    return store


def test_integration_mode_switch_persists_context_and_status_payload(tmp_path: Path) -> None:
    store = _store(tmp_path)
    try:
        config = StudyConfig(mode=MODE_COMPANION, default_mode=MODE_COMPANION)
        state = build_initial_state(mode=config.mode)
        state.current_question = {"question": "What is a derivative?", "topic": "calculus"}
        state.recent_learning_events = [{"kind": "question_generate", "topic": "calculus"}]
        manager = ModeManager(current_mode=state.active_mode)

        switched = manager.switch_to(MODE_TEACHING, "user_intent", now=1000.0, language="en")
        state.active_mode = switched["new_mode"]
        state.mode_started_at = switched["checkpoint"]["mode_started_at"]
        state.checkpoint = switched["checkpoint"]
        store.save_config(config)
        store.save_state(state)

        loaded = store.load_state(build_initial_state(mode=config.mode))
        payload = build_status_payload(config=config, state=loaded)

        assert switched["changed"] is True
        assert payload["active_mode"] == MODE_TEACHING
        assert loaded.current_question["topic"] == "calculus"
        assert "current_question" not in payload
        assert payload["recent_learning_events"][0]["kind"] == "question_generate"
    finally:
        store.close()
