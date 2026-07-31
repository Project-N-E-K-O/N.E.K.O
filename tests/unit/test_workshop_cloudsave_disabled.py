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
async def test_a_transaction_hands_ensure_its_own_policy(tmp_path, monkeypatch):
    """The auto-create decision must come from the transaction, not a reload.

    `ensure_workshop_folder_exists` used to re-read the config file to decide
    `auto_create`, so an overlapping request could flip it in between: A saves
    auto_create=true for folder A, B saves auto_create=false, A's ensure reads
    B's config and declines to create A — while A answers success.

    The policy is now decided under the lock and passed in explicitly, which is
    also what lets the (possibly very slow) directory work happen outside the
    lock. Asserted on the argument ensure actually receives.
    """
    _stub_config_manager_lock(monkeypatch)
    import threading

    from main_routers.workshop_router import config_files
    from utils import workshop_utils

    stored: dict = {"auto_create_folder": True}
    seen: list[str] = []
    b_saved = threading.Event()

    def _load():
        return dict(stored)

    def _save(cfg):
        stored.clear()
        stored.update(cfg)
        if cfg.get("user_mod_folder") == "B":
            b_saved.set()

    def _ensure(folder, **kwargs):
        if folder == "A":
            # 让 B 一定先写完，构造出「重读就会读到别人配置」的时刻。
            b_saved.wait(timeout=1.0)
        seen.append(f"{folder}:auto={kwargs.get('auto_create')}")
        return True

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

    assert "A:auto=True" in seen, (
        f"A 的 ensure 拿到的不是本次事务定下的策略：{seen}"
    )
    assert not any(entry.startswith("B:") for entry in seen), (
        "auto_create=false 的那次不该去建目录"
    )


def _mixin_tree():
    import ast
    import inspect
    import textwrap

    from utils.config_manager import workshop as workshop_mixin

    return ast.parse(textwrap.dedent(inspect.getsource(workshop_mixin.WorkshopMixin)))


def _fn(tree, name):
    import ast

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"找不到 {name}，这条守卫需要跟着更新")


def test_every_write_of_the_workshop_config_holds_the_lock():
    """A save must never race the POST transaction's read-modify-write.

    Checked as "every save_workshop_config call site is inside the lock"
    rather than by naming the known writers — a new one added later has to
    fail this instead of slipping through.
    """
    import ast

    tree = _mixin_tree()
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "save_workshop_config":
            continue  # 叶子写入本身，由调用方持锁
        saves = {
            call.lineno for call in ast.walk(fn)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "save_workshop_config"
        }
        if not saves:
            continue
        guarded = {
            call.lineno
            for node in ast.walk(fn) if isinstance(node, ast.With)
            for item in node.items
            if "_workshop_config_lock" in ast.unparse(item.context_expr)
            for call in ast.walk(node)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "save_workshop_config"
        }
        if saves - guarded:
            offenders.append(f"{fn.name}(相对行 {sorted(saves - guarded)})")

    assert not offenders, (
        f"这些地方在锁外写 workshop 配置：{offenders} —— "
        "读改写不整段持锁就能盖掉刚提交的配置"
    )


def test_the_config_read_path_never_takes_the_lock():
    """The dual invariant, and the one that keeps biting.

    `get_workshop_path()` goes through `load_workshop_config()`, and several
    async handlers (voice_refs upload/remove, publish) call it straight from
    the event loop. If that read had to acquire the lock, any worker holding
    it across an fsync — or across a makedirs on a network path — would stall
    the whole loop. The self-healing write serializes itself instead, inside
    `_rebase_workshop_config_after_storage_migration`.
    """
    import ast

    tree = _mixin_tree()
    load_fn = _fn(tree, "load_workshop_config")
    locked = [
        node.lineno
        for node in ast.walk(load_fn) if isinstance(node, ast.With)
        for item in node.items
        if "_workshop_config_lock" in ast.unparse(item.context_expr)
    ]
    # 「文件不存在」那条分支写默认配置，持锁是对的；有文件的读路径不许持锁。
    read_branch = _fn(tree, "_read_workshop_config_file")
    read_locked = [
        node.lineno
        for node in ast.walk(read_branch) if isinstance(node, ast.With)
        for item in node.items
        if "_workshop_config_lock" in ast.unparse(item.context_expr)
    ]
    assert read_locked == [], "裸读路径不许拿锁——事件循环上的 get_workshop_path() 会跟着等"
    assert len(locked) <= 1, (
        f"load_workshop_config 里出现了不止一处持锁（相对行 {locked}）——"
        "有文件的读路径必须保持无锁"
    )


def test_the_workshop_config_lock_is_reentrant():
    """The transaction holds it and then calls load_workshop_config underneath."""
    import threading

    from utils.config_manager import get_config_manager

    lock = get_config_manager().workshop_config_lock()
    assert isinstance(lock, type(threading.RLock())), (
        "必须是 RLock：事务持着它再调 load_workshop_config，不可重入就是自死锁"
    )


def test_a_path_naming_a_file_is_not_a_ready_folder(tmp_path):
    """`exists()` is true for regular files; the workshop root must be a dir.

    Reporting ready for a file persists it as the workshop root, and every
    later `os.path.join(root, 'WorkshopExport')` fails against it.
    """
    from utils.workshop_utils import ensure_workshop_folder_exists

    a_file = tmp_path / "not-a-folder.txt"
    a_file.write_text("x", encoding="utf-8")

    assert ensure_workshop_folder_exists(str(a_file), auto_create=True) is False

    a_dir = tmp_path / "real-folder"
    a_dir.mkdir()
    assert ensure_workshop_folder_exists(str(a_dir), auto_create=True) is True

    fresh = tmp_path / "created-on-demand"
    assert ensure_workshop_folder_exists(str(fresh), auto_create=True) is True
    assert fresh.is_dir()

    blocked = tmp_path / "not-allowed"
    assert ensure_workshop_folder_exists(str(blocked), auto_create=False) is False
    assert not blocked.exists()


@pytest.mark.asyncio
async def test_a_non_string_folder_value_is_rejected_before_it_is_persisted(monkeypatch):
    """A `{}` or list for a folder field must not reach the config file.

    Persisting it corrupts the config: `get_workshop_path()` later hands the
    object straight to `os.path.join()` in every workshop handler, and the user
    cannot recover without editing the file by hand.
    """
    _stub_config_manager_lock(monkeypatch)

    from main_routers.workshop_router import config_files
    from utils import workshop_utils

    saved: list[dict] = []
    monkeypatch.setattr(workshop_utils, "load_workshop_config", lambda: {})
    monkeypatch.setattr(workshop_utils, "save_workshop_config", lambda cfg: saved.append(cfg))
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", lambda f, **kw: True)

    for bad in ({}, ["a"], 5):
        result = await config_files.save_workshop_config_api({"user_mod_folder": bad})
        assert result["success"] is False, f"{bad!r} 被接受了"
        assert "user_mod_folder" in result["error"]

    assert saved == [], "校验失败的请求不该写盘"

    ok = await config_files.save_workshop_config_api({"user_mod_folder": "C:/mods"})
    assert ok["success"] is True
    assert saved and saved[-1]["user_mod_folder"] == "C:/mods"


@pytest.mark.asyncio
async def test_the_self_healing_write_is_skipped_on_the_event_loop(monkeypatch, tmp_path):
    """`get_workshop_path()` runs on the loop; its self-heal must not take the lock.

    The rebase only fires after a storage migration, but when it does the write
    would acquire the same lock a worker may hold across an fsync — stalling the
    whole loop. The corrected paths are still returned to this caller; only the
    persistence waits for a reader that runs off-loop.
    """
    from utils.config_manager import workshop as workshop_mixin

    class _CM(workshop_mixin.WorkshopMixin):
        def __init__(self):
            import threading

            self._workshop_config_lock = threading.RLock()
            self.app_docs_dir = str(tmp_path)
            self.saves: list[dict] = []

        def load_root_state(self):
            return {"last_migration_source": str(tmp_path / "old")}

        def _read_workshop_config_file(self):
            # 必须返回真东西：否则没有守卫时也会在这里抛、被外层 except 吞掉，
            # 两种实现都「没落盘」，用例就分辨不出来了。
            return {"user_mod_folder": "old"}

        def save_workshop_config(self, config):
            self.saves.append(dict(config))

    cm = _CM()
    monkeypatch.setattr(
        "utils.storage_path_rewrite.rebase_runtime_bound_workshop_config_paths",
        lambda cfg, **kw: {**cfg, "user_mod_folder": "rebased"},
    )

    rebased = cm._rebase_workshop_config_after_storage_migration({"user_mod_folder": "old"})

    assert rebased["user_mod_folder"] == "rebased", "调用方仍然要拿到修正后的路径"
    assert cm.saves == [], "在事件循环上不许落盘——那会去抢 worker 可能持着的锁"
