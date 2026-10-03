import json

import pytest

from utils.click_guide_state import get_click_guide_state, update_click_guide_state


class Config:
    def __init__(self, root):
        self.root = root

    def get_config_path(self, name):
        return str(self.root / name)


@pytest.mark.unit
def test_default_seven_day_and_explicit_click_reactivation_preserve_seven_days(tmp_path):
    config = Config(tmp_path)
    state = get_click_guide_state(config_manager=config)
    assert state["choice"] == "seven-day"
    choice = update_click_guide_state({"action": "choose", "choice": "click", "expectedRevision": 0}, config_manager=config)
    assert choice["state"]["pending"]
    done = update_click_guide_state({"action": "finish", "status": "completed", "expectedRevision": 1}, config_manager=config)
    assert done["state"]["status"] == "completed"
    assert not done["state"]["pending"]
    assert not (tmp_path / "seven_day_tutorial_state.json").exists()


@pytest.mark.unit
@pytest.mark.parametrize("settled", ["completedRounds", "skippedRounds"])
def test_existing_user_is_not_prompted_and_reset_is_independent(tmp_path, settled):
    config = Config(tmp_path)
    old = tmp_path / "seven_day_tutorial_state.json"
    old.write_text(json.dumps({"initialized": True, "revision": 3, "state": {settled: [1, 2]}}))
    before = old.read_bytes()
    state = get_click_guide_state(config_manager=config)
    assert state["choice"] == "seven-day"
    assert not state["pending"]
    reset = update_click_guide_state({"action": "reset", "expectedRevision": 0}, config_manager=config)
    assert reset["state"]["pending"]
    assert reset["state"]["choice"] == "click"
    assert old.read_bytes() == before


@pytest.mark.unit
def test_late_completion_cannot_erase_a_reset_from_another_window(tmp_path):
    config = Config(tmp_path)
    get_click_guide_state(config_manager=config)
    update_click_guide_state({"action": "reset", "expectedRevision": 0}, config_manager=config)
    stale = update_click_guide_state({"action": "finish", "status": "completed", "expectedRevision": 0}, config_manager=config)
    assert stale["ok"] is False
    assert stale["state"]["pending"] is True
    assert stale["state"]["status"] == "unseen"


@pytest.mark.unit
def test_legacy_completed_user_is_not_prompted(tmp_path):
    (tmp_path / "tutorial_prompt.json").write_text(json.dumps({"home_tutorial_completed": True}))
    assert get_click_guide_state(config_manager=Config(tmp_path))["choice"] == "seven-day"


@pytest.mark.unit
def test_invalid_action_does_not_write(tmp_path):
    config = Config(tmp_path)
    before = get_click_guide_state(config_manager=config)
    with pytest.raises(ValueError):
        update_click_guide_state({"action": "finish", "status": "anything", "expectedRevision": 0}, config_manager=config)
    assert get_click_guide_state(config_manager=config) == before


@pytest.mark.unit
def test_read_error_preserves_pending_choice_and_revision(tmp_path, monkeypatch):
    config = Config(tmp_path)
    update_click_guide_state(
        {"action": "choose", "choice": "click", "expectedRevision": 0}, config_manager=config,
    )
    path = tmp_path / "click_guide_state.json"
    before = path.read_bytes()
    original_open = type(path).open

    def locked_open(self, *args, **kwargs):
        if self == path:
            raise PermissionError("temporarily locked")
        return original_open(self, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(type(path), "open", locked_open)
        with pytest.raises(PermissionError):
            get_click_guide_state(config_manager=config)
        with pytest.raises(PermissionError):
            update_click_guide_state({"action": "reset", "expectedRevision": 1}, config_manager=config)
    assert path.read_bytes() == before


@pytest.mark.unit
def test_future_version_is_read_only_and_never_downgraded(tmp_path):
    config = Config(tmp_path)
    path = tmp_path / "click_guide_state.json"
    path.write_text(json.dumps({"version": 2, "choice": "click", "future": {"progress": 7}}))
    before = path.read_bytes()
    state = get_click_guide_state(config_manager=config)
    assert state["choice"] == "seven-day" and state["readOnly"]
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="Unsupported"):
        update_click_guide_state({"action": "reset", "expectedRevision": 0}, config_manager=config)
    assert path.read_bytes() == before


@pytest.mark.unit
@pytest.mark.parametrize("invalid", [
    {"revision": "broken"}, {"revision": True}, {"revision": -1},
    {"choice": "invalid"}, {"status": "invalid"}, {"pending": "true"},
    {"version": True},
])
def test_corrupt_store_recovers_and_remains_writable(tmp_path, invalid):
    state = {"version": 1, "revision": 0, "choice": None,
             "status": "unseen", "pending": False, **invalid}
    (tmp_path / "click_guide_state.json").write_text(json.dumps(state))
    config = Config(tmp_path)
    recovered = get_click_guide_state(config_manager=config)
    assert recovered["revision"] == 0
    result = update_click_guide_state(
        {"action": "choose", "choice": "click", "expectedRevision": 0},
        config_manager=config,
    )
    assert result["ok"] and result["state"]["pending"]


@pytest.mark.unit
def test_missing_fields_recover(tmp_path):
    (tmp_path / "click_guide_state.json").write_text('{"version": 1}')
    state = get_click_guide_state(config_manager=Config(tmp_path))
    assert state == {"version": 1, "revision": 0, "choice": "seven-day",
                     "status": "unseen", "pending": False}


@pytest.mark.unit
def test_choice_timestamp_survives_completion_and_legacy_click_is_migrated(tmp_path, monkeypatch):
    config = Config(tmp_path)
    monkeypatch.setattr("utils.click_guide_state.time", lambda: 1000)
    chosen = update_click_guide_state(
        {"action": "choose", "choice": "click", "expectedRevision": 0}, config_manager=config,
    )["state"]
    assert chosen["selectedAt"] == 1000000
    monkeypatch.setattr("utils.click_guide_state.time", lambda: 2000)
    done = update_click_guide_state(
        {"action": "finish", "status": "completed", "expectedRevision": 1}, config_manager=config,
    )["state"]
    assert done["selectedAt"] == 1000000
    del done["selectedAt"]
    (tmp_path / "click_guide_state.json").write_text(json.dumps(done))
    migrated = get_click_guide_state(config_manager=config)
    assert migrated["selectedAt"] == 2000000
    assert migrated["revision"] == 3
    assert get_click_guide_state(config_manager=config) == migrated
