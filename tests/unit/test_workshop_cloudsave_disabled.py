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


def _stub_config_manager_lock(monkeypatch):
    """Give the transaction a real reentrant lock without booting shared state.

    The route deliberately borrows ConfigManager's own workshop lock — the
    self-healing write inside load_workshop_config takes the same one — so the
    test has to supply something lock-shaped rather than bypass it.
    """
    import threading

    from main_routers.workshop_router import config_files

    lock = threading.RLock()

    class _CM:
        def workshop_config_lock(self):
            return lock

    monkeypatch.setattr(config_files, "get_config_manager", lambda: _CM())
    return lock

@pytest.mark.asyncio
async def test_concurrent_config_saves_do_not_cross_transactions(tmp_path, monkeypatch):
    """Two overlapping /config requests must not read each other's half-state.

    The save now runs in a worker thread, so two of them interleave at the OS
    level. `ensure_workshop_folder_exists` re-reads the config file to decide
    `auto_create_folder`, so without serialization request A's ensure can see
    request B's freshly-saved config and decline to create A's folder while A
    still answers `success`.
    """
    _stub_config_manager_lock(monkeypatch)
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

    from utils import workshop_utils

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


@pytest.mark.asyncio
async def test_a_folder_that_cannot_be_created_is_reported(monkeypatch):
    """Saving a read-only path must not be reported as fully successful.

    ``ensure_workshop_folder_exists`` swallows the creation failure and returns
    False. The config itself did persist, so ``success`` stays True — but the
    response has to say the folder is not usable, or the user is told an
    unusable workshop path was set up fine.
    """
    _stub_config_manager_lock(monkeypatch)
    from main_routers.workshop_router import config_files
    from utils import workshop_utils

    monkeypatch.setattr(workshop_utils, "load_workshop_config", lambda: {})
    monkeypatch.setattr(workshop_utils, "save_workshop_config", lambda cfg: None)
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", lambda folder: False)

    result = await config_files.save_workshop_config_api(
        {"user_mod_folder": "R:/read-only", "auto_create_folder": True}
    )

    assert result["success"] is True, "配置本身确实存下来了"
    assert result["folder_ready"] is False
    assert "warning" in result


@pytest.mark.asyncio
async def test_a_created_folder_reports_ready(monkeypatch):
    _stub_config_manager_lock(monkeypatch)
    from main_routers.workshop_router import config_files
    from utils import workshop_utils

    monkeypatch.setattr(workshop_utils, "load_workshop_config", lambda: {})
    monkeypatch.setattr(workshop_utils, "save_workshop_config", lambda cfg: None)
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", lambda folder: True)

    result = await config_files.save_workshop_config_api(
        {"user_mod_folder": "C:/mods", "auto_create_folder": True}
    )

    assert result["folder_ready"] is True
    assert "warning" not in result


def test_the_self_healing_read_shares_the_transaction_lock():
    """`load_workshop_config` is not read-only; it must take the same lock.

    After a storage migration it rebases paths and saves the result. If that
    write happens outside the lock the POST transaction uses, a concurrent GET
    can read before the transaction and save after it, silently overwriting
    the folder settings the user just submitted while POST reports success.
    """
    import ast
    import inspect
    import textwrap

    from utils.config_manager import workshop as workshop_mixin

    # 方法源码带类体缩进，直接 ast.parse 会 IndentationError。
    tree = ast.parse(
        textwrap.dedent(inspect.getsource(workshop_mixin.WorkshopMixin.load_workshop_config))
    )
    rebase_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_rebase_workshop_config_after_storage_migration"
    ]
    assert rebase_calls, "自愈读的调用点不见了，这条守卫需要跟着更新"

    guarded = {
        call.lineno
        for node in ast.walk(tree) if isinstance(node, ast.With)
        for item in node.items
        if "_workshop_config_lock" in ast.unparse(item.context_expr)
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "_rebase_workshop_config_after_storage_migration"
    }
    unguarded = {call.lineno for call in rebase_calls} - guarded
    assert not unguarded, (
        f"这些自愈读没有进 _workshop_config_lock（相对行号 {sorted(unguarded)}）——"
        "它会写盘，不进锁就能盖掉刚提交的配置"
    )


def test_the_workshop_config_lock_is_reentrant():
    """The transaction holds it and then calls load_workshop_config underneath."""
    import threading

    from utils.config_manager import get_config_manager

    lock = get_config_manager().workshop_config_lock()
    assert isinstance(lock, type(threading.RLock())), (
        "必须是 RLock：事务持着它再调 load_workshop_config，不可重入就是自死锁"
    )
