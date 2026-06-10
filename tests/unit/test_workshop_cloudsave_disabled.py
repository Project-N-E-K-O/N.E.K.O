import pytest

from utils.cloudsave_runtime import CLOUDSAVE_DISABLED_ENV


class _ForbiddenTombstoneConfig:
    CHARACTER_TOMBSTONES_STATE_VERSION = 1

    def load_character_tombstones_state(self):
        raise AssertionError("disabled cloudsave workshop path should not read tombstone state")

    def save_character_tombstones_state(self, _payload):
        raise AssertionError("disabled cloudsave workshop path should not save tombstone state")


@pytest.mark.unit
def test_workshop_deleted_name_load_skips_state_when_cloudsave_is_disabled(monkeypatch):
    from main_routers.workshop_router import _load_deleted_character_names, _session_deleted_names

    calls = []

    class _TrackingConfig(_ForbiddenTombstoneConfig):
        def load_character_tombstones_state(self):
            calls.append("load")
            return {"version": 1, "tombstones": [{"character_name": "不应读取"}]}

    monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "local_state_unavailable")
    _session_deleted_names.clear()
    _session_deleted_names.add("本会话删除角色")

    assert _load_deleted_character_names(_TrackingConfig()) == {"本会话删除角色"}
    assert calls == []
    _session_deleted_names.clear()


@pytest.mark.unit
def test_workshop_tombstone_cleanup_skips_state_when_cloudsave_is_disabled(monkeypatch):
    from main_routers.workshop_router import _remove_deleted_character_tombstones, _session_deleted_names

    monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "local_state_unavailable")
    _session_deleted_names.clear()
    _session_deleted_names.update({"已删除角色", "保留角色"})

    assert _remove_deleted_character_tombstones(_ForbiddenTombstoneConfig(), ["已删除角色"]) == ["已删除角色"]
    assert _session_deleted_names == {"保留角色"}
    _session_deleted_names.clear()


@pytest.mark.unit
def test_workshop_tombstone_write_skips_state_when_cloudsave_is_disabled(monkeypatch):
    from main_routers.workshop_router import _write_deleted_character_tombstone, _session_deleted_names

    def _forbidden_builder(_config_mgr, _name):
        raise AssertionError("disabled cloudsave workshop path should not build tombstone state")

    monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "local_state_unavailable")
    _session_deleted_names.clear()

    assert _write_deleted_character_tombstone(
        _ForbiddenTombstoneConfig(),
        "已删除角色",
        _forbidden_builder,
    ) is False
    assert _session_deleted_names == {"已删除角色"}
    _session_deleted_names.clear()


@pytest.mark.unit
def test_workshop_tombstone_write_still_saves_when_cloudsave_is_enabled(monkeypatch):
    from main_routers.workshop_router import _write_deleted_character_tombstone, _session_deleted_names

    saved_payloads = []

    class _Config:
        def save_character_tombstones_state(self, payload):
            saved_payloads.append(payload)

    def _builder(_config_mgr, name):
        return {"version": 1, "tombstones": [{"character_name": name}]}

    monkeypatch.delenv(CLOUDSAVE_DISABLED_ENV, raising=False)
    _session_deleted_names.clear()

    assert _write_deleted_character_tombstone(_Config(), "恢复角色", _builder) is True
    assert saved_payloads == [{"version": 1, "tombstones": [{"character_name": "恢复角色"}]}]
    assert _session_deleted_names == {"恢复角色"}
    _session_deleted_names.clear()
