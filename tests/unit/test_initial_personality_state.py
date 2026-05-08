from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import utils.initial_personality_state as initial_personality_state
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


@pytest.mark.unit
def test_initial_personality_state_updates_are_atomic_across_threads(tmp_path, monkeypatch):
    config = DummyConfig(tmp_path)
    real_atomic_write_json = initial_personality_state.atomic_write_json
    status_write_started = threading.Event()
    allow_status_write = threading.Event()

    def delayed_atomic_write_json(path, data, ensure_ascii=False, indent=2):
        if threading.current_thread().name == "status-writer":
            status_write_started.set()
            allow_status_write.wait(timeout=2)
        return real_atomic_write_json(path, data, ensure_ascii=ensure_ascii, indent=indent)

    monkeypatch.setattr(initial_personality_state, "atomic_write_json", delayed_atomic_write_json)

    status_result = {}
    reselect_result = {}

    def write_status():
        status_result["value"] = mark_initial_personality_state(
            "completed",
            config_manager=config,
            now_iso="2026-04-29T12:00:00Z",
        )

    def write_reselect():
        reselect_result["value"] = mark_manual_personality_reselect(
            "小天",
            config_manager=config,
            now_iso="2026-04-29T12:10:00Z",
        )

    status_thread = threading.Thread(target=write_status, name="status-writer")
    reselect_thread = threading.Thread(target=write_reselect, name="reselect-writer")

    status_thread.start()
    assert status_write_started.wait(timeout=1)

    reselect_thread.start()
    time.sleep(0.05)
    allow_status_write.set()

    status_thread.join(timeout=2)
    reselect_thread.join(timeout=2)

    assert not status_thread.is_alive()
    assert not reselect_thread.is_alive()
    assert status_result["value"]["status"] == "completed"
    assert reselect_result["value"]["manual_reselect_character_name"] == "小天"

    reloaded = load_initial_personality_state(config)
    assert reloaded["status"] == "completed"
    assert reloaded["handled_at"] == "2026-04-29T12:00:00Z"
    assert reloaded["manual_reselect_character_name"] == "小天"
    assert reloaded["manual_reselect_requested_at"] == "2026-04-29T12:10:00Z"
