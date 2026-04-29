from __future__ import annotations

from pathlib import Path

import pytest

from utils.initial_personality_state import (
    clear_manual_personality_reselect,
    get_initial_personality_state_path,
    load_initial_personality_state,
    mark_manual_personality_reselect,
    mark_initial_personality_state,
)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Pure helper tests do not need the repo-level mock memory server."""
    yield


class DummyConfig:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.local_state_dir = self.root / "state"
        self.local_state_dir.mkdir(parents=True, exist_ok=True)

    def ensure_local_state_directory(self) -> bool:
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True


@pytest.mark.unit
def test_initial_personality_state_defaults_to_pending(tmp_path):
    config = DummyConfig(tmp_path)

    state = load_initial_personality_state(config)

    assert state["version"] == 1
    assert state["status"] == "pending"
    assert state["handled_at"] == ""
    assert get_initial_personality_state_path(config) == tmp_path / "state" / "initial_personality_prompt.json"


@pytest.mark.unit
def test_mark_initial_personality_state_persists_completed_and_skipped(tmp_path):
    config = DummyConfig(tmp_path)

    completed = mark_initial_personality_state(
        "completed",
        config_manager=config,
        now_iso="2026-04-29T12:00:00Z",
    )
    skipped = mark_initial_personality_state(
        "skipped",
        config_manager=config,
        now_iso="2026-04-29T12:05:00Z",
    )

    assert completed["status"] == "completed"
    assert skipped["status"] == "skipped"

    reloaded = load_initial_personality_state(config)
    assert reloaded["status"] == "skipped"
    assert reloaded["handled_at"] == "2026-04-29T12:05:00Z"


@pytest.mark.unit
def test_manual_personality_reselect_tracks_current_character_request(tmp_path):
    config = DummyConfig(tmp_path)

    requested = mark_manual_personality_reselect(
        "小天",
        config_manager=config,
        now_iso="2026-04-29T12:10:00Z",
    )

    assert requested["manual_reselect_character_name"] == "小天"
    assert requested["manual_reselect_requested_at"] == "2026-04-29T12:10:00Z"

    cleared = clear_manual_personality_reselect(config_manager=config)
    assert cleared["manual_reselect_character_name"] == ""
    assert cleared["manual_reselect_requested_at"] == ""
