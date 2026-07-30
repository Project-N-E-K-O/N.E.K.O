import asyncio

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


def test_workshop_utils_reexports_the_config_saver():
    """POST /api/steam/workshop/config imports its saver from utils.workshop_utils.

    That module re-exports the config_manager helpers, and `save_workshop_config`
    was missing from the list — so the handler's own `from utils.workshop_utils
    import ... save_workshop_config ...` raised ImportError on every request,
    was swallowed by the handler's `except Exception`, and the endpoint answered
    HTTP 200 with `{"success": false}` while never writing a single byte.
    """
    from utils import workshop_utils

    assert hasattr(workshop_utils, "save_workshop_config"), (
        "save_workshop_config 必须能从 utils.workshop_utils 导入 —— "
        "保存 workshop 配置的接口就是从这里拿它的"
    )


def test_the_workshop_config_route_can_import_what_it_uses():
    """The route's own import line must actually resolve.

    Pinned as the route writes it (a local import inside the handler), so a
    future re-shuffle of utils.workshop_utils breaks this test instead of
    silently turning the endpoint into a no-op again.
    """
    from utils.workshop_utils import (  # noqa: F401
        ensure_workshop_folder_exists,
        load_workshop_config,
        save_workshop_config,
    )


@pytest.mark.asyncio
async def test_concurrent_config_saves_do_not_cross_transactions(tmp_path, monkeypatch):
    """Two overlapping /config requests must not read each other's half-state.

    The save now runs in a worker thread, so two of them interleave at the OS
    level. `ensure_workshop_folder_exists` re-reads the config file to decide
    `auto_create_folder`, so without serialization request A's ensure can see
    request B's freshly-saved config and decline to create A's folder while A
    still answers `success`.
    """
    import threading

    from main_routers.workshop_router import config_files

    stored: dict = {"auto_create_folder": True}
    order: list[str] = []
    b_saved = threading.Event()

    def _load():
        return dict(stored)

    def _save(cfg):
        stored.clear()
        stored.update(cfg)
        if cfg.get("user_mod_folder") == "B":
            b_saved.set()

    def _ensure(folder):
        # A 到这里时故意让 B 有机会先写完；没有锁的话 A 就会读到 B 的配置。
        if folder == "A":
            b_saved.wait(timeout=1.0)
        order.append(f"ensure:{folder}:auto={_load().get('auto_create_folder')}")
        return True

    import utils.workshop_utils as workshop_utils

    monkeypatch.setattr(workshop_utils, "load_workshop_config", _load)
    monkeypatch.setattr(workshop_utils, "save_workshop_config", _save)
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", _ensure)

    a = asyncio.create_task(
        config_files.save_workshop_config_api(
            {"user_mod_folder": "A", "auto_create_folder": True}
        )
    )
    await asyncio.sleep(0)
    b = asyncio.create_task(
        config_files.save_workshop_config_api(
            {"user_mod_folder": "B", "auto_create_folder": False}
        )
    )
    await asyncio.gather(a, b)

    a_ensures = [entry for entry in order if entry.startswith("ensure:A")]
    assert a_ensures == ["ensure:A:auto=True"], (
        f"A 的 ensure 读到了别人的配置：{order}"
    )
