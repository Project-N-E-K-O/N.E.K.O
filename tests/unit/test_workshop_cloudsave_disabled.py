import asyncio
import json
import os

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
        if str(cfg.get("user_mod_folder", "")).endswith("B"):
            b_saved.set()

    def _ensure(folder, **kwargs):
        if folder.endswith("A"):
            # 让 B 一定先写完，构造出「重读就会读到别人配置」的时刻。
            b_saved.wait(timeout=1.0)
        seen.append(f"{os.path.basename(folder)}:auto={kwargs.get('auto_create')}")
        return True

    monkeypatch.setattr(workshop_utils, "load_workshop_config", _load)
    monkeypatch.setattr(workshop_utils, "save_workshop_config", _save)
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", _ensure)

    a = asyncio.create_task(
        config_files.save_workshop_config_api(
            {"user_mod_folder": os.path.join(os.sep, "tmp", "A"), "auto_create_folder": True}
        )
    )
    await asyncio.sleep(0)
    b = asyncio.create_task(
        config_files.save_workshop_config_api(
            {"user_mod_folder": os.path.join(os.sep, "tmp", "B"), "auto_create_folder": False}
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

    ok = await config_files.save_workshop_config_api({"user_mod_folder": os.path.join(os.sep, "mods")})
    assert ok["success"] is True
    assert saved and saved[-1]["user_mod_folder"] == os.path.join(os.sep, "mods")


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


@pytest.mark.asyncio
async def test_a_relative_folder_is_rejected(monkeypatch):
    """A relative path resolves differently in every consumer; refuse it.

    `ensure_workshop_folder_exists` resolves it against the user's home and can
    report ready, while `get_workshop_path()` hands the raw string to
    `_assert_under_base`, which resolves it against the server's working
    directory. Two different places, and we already told the user it was ready.
    """
    _stub_config_manager_lock(monkeypatch)

    from main_routers.workshop_router import config_files
    from utils import workshop_utils

    saved: list[dict] = []
    monkeypatch.setattr(workshop_utils, "load_workshop_config", lambda: {})
    monkeypatch.setattr(workshop_utils, "save_workshop_config", lambda cfg: saved.append(cfg))
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", lambda f, **kw: True)

    result = await config_files.save_workshop_config_api({"user_mod_folder": "mods"})
    assert result["success"] is False
    assert "绝对路径" in result["error"]
    assert saved == []


@pytest.mark.asyncio
async def test_a_non_boolean_auto_create_is_rejected(monkeypatch):
    """`"false"` is a truthy string; taking it at face value creates folders."""
    _stub_config_manager_lock(monkeypatch)

    from main_routers.workshop_router import config_files
    from utils import workshop_utils

    saved: list[dict] = []
    monkeypatch.setattr(workshop_utils, "load_workshop_config", lambda: {})
    monkeypatch.setattr(workshop_utils, "save_workshop_config", lambda cfg: saved.append(cfg))
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", lambda f, **kw: True)

    result = await config_files.save_workshop_config_api({"auto_create_folder": "false"})
    assert result["success"] is False
    assert "布尔" in result["error"]
    assert saved == []


def test_the_save_and_the_folder_creation_share_one_worker_job():
    """Cancellation must not be able to land between them.

    `asyncio.to_thread` does not stop a worker that already started, but the
    handler stops at the await — so a second `to_thread` for the directory work
    would simply never run, leaving an auto-create configuration persisted with
    its directory missing. Both side effects live in one job.
    """
    import ast
    import inspect
    import textwrap

    from main_routers.workshop_router import config_files

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(config_files.save_workshop_config_api))
    )
    to_thread_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "to_thread"
    ]
    assert len(to_thread_calls) == 1, (
        f"保存与建目录必须在同一个 worker job 里，现在有 {len(to_thread_calls)} 次 to_thread"
    )
    inner = _fn(tree, "_apply_config_transaction")
    called = {
        c.func.id for c in ast.walk(inner)
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
    }
    assert {"save_workshop_config", "ensure_workshop_folder_exists"} <= called


@pytest.mark.asyncio
async def test_a_transient_read_failure_falls_back_to_the_last_good_config(tmp_path, monkeypatch):
    """A Windows sharing violation must not silently swap in the default root.

    Persistence runs in a worker now, so an event-loop read can land mid
    `os.replace`. The loop is not allowed to back off (that would stall it), so
    the read raises — and returning defaults there would hand `upload` and
    `publish` the default workshop root while the user's config is perfectly
    fine on disk.
    """
    from utils.config_manager import workshop as workshop_mixin

    config_path = tmp_path / "workshop_config.json"
    config_path.write_text(
        json.dumps({"user_mod_folder": "/user/mods", "auto_create_folder": True}),
        encoding="utf-8",
    )

    class _CM(workshop_mixin.WorkshopMixin):
        def __init__(self):
            import threading

            self._workshop_config_lock = threading.RLock()
            self.workshop_dir = tmp_path / "default"

        def get_workshop_config_path(self):
            return config_path

        def _rebase_workshop_config_after_storage_migration(self, config):
            return config

    cm = _CM()
    assert cm.load_workshop_config()["user_mod_folder"] == "/user/mods"

    busy = PermissionError(13, "Access is denied")
    busy.winerror = 32
    monkeypatch.setattr(
        workshop_mixin, "read_json_tolerating_replace",
        lambda *a, **kw: (_ for _ in ()).throw(busy),
    )

    fallback = cm.load_workshop_config()
    assert fallback["user_mod_folder"] == "/user/mods", (
        "瞬时读失败退回了默认配置——upload/publish 会拿着默认工坊根目录干活"
    )


def test_the_replace_tolerant_read_never_sleeps_on_the_event_loop():
    """Same rule as the write side: no backoff on the loop, ever."""
    import ast
    import inspect
    import textwrap

    from utils import file_utils

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(file_utils.read_json_tolerating_replace))
    )
    guards = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "running_on_event_loop"
    ]
    sleeps = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "sleep"
    ]
    assert sleeps, "这条守卫是冲着退避去的，没有 sleep 就该更新它"
    assert guards, (
        "读重试没有事件循环守卫 —— get_workshop_path() 这类同步读就挂在 async "
        "handler 上，会在循环上睡满退避预算"
    )


def test_a_save_refreshes_the_last_good_config(tmp_path, monkeypatch):
    """The fallback must not hand back the state from before this very save.

    `POST /config` loads the old config, writes the new one, and the cache is
    only refreshed on successful *reads* — so a transient read failure right
    after would return the pre-change configuration, which is harder to spot
    than falling back to defaults.
    """
    from utils.config_manager import workshop as workshop_mixin

    config_path = tmp_path / "workshop_config.json"
    config_path.write_text(json.dumps({"user_mod_folder": "/old"}), encoding="utf-8")

    class _CM(workshop_mixin.WorkshopMixin):
        def __init__(self):
            import threading

            self._workshop_config_lock = threading.RLock()
            self.workshop_dir = tmp_path / "default"

        def get_workshop_config_path(self):
            return config_path

        def get_runtime_config_path(self, name):
            return config_path

        def ensure_config_directory(self):
            return None

        def _rebase_workshop_config_after_storage_migration(self, config):
            return config

    cm = _CM()
    assert cm.load_workshop_config()["user_mod_folder"] == "/old"
    monkeypatch.setattr(
        "utils.cloudsave_runtime.assert_cloudsave_writable", lambda *a, **kw: None
    )
    cm.save_workshop_config({"user_mod_folder": "/new"})

    busy = PermissionError(13, "Access is denied")
    busy.winerror = 32
    monkeypatch.setattr(
        workshop_mixin, "read_json_tolerating_replace",
        lambda *a, **kw: (_ for _ in ()).throw(busy),
    )

    assert cm.load_workshop_config()["user_mod_folder"] == "/new", (
        "读失败回落到了保存**之前**的配置"
    )


@pytest.mark.asyncio
async def test_a_blank_folder_value_is_rejected(monkeypatch):
    """`"   "` must not slip past the absolute-path check and get persisted."""
    _stub_config_manager_lock(monkeypatch)

    from main_routers.workshop_router import config_files
    from utils import workshop_utils

    saved: list[dict] = []
    monkeypatch.setattr(workshop_utils, "load_workshop_config", lambda: {})
    monkeypatch.setattr(workshop_utils, "save_workshop_config", lambda cfg: saved.append(cfg))
    monkeypatch.setattr(workshop_utils, "ensure_workshop_folder_exists", lambda f, **kw: True)

    result = await config_files.save_workshop_config_api({"user_mod_folder": "   "})
    assert result["success"] is False
    assert "空白" in result["error"]
    assert saved == []


def test_the_missing_config_branch_is_lock_free_on_the_loop():
    """The first POST /config holds the lock while creating the file.

    Until it commits, the target does not exist — so a loop-side
    `get_workshop_path()` takes the "missing" branch, and if that branch waits
    on the writer lock the whole loop waits with it. Returning defaults needs
    no mutual exclusion.
    """
    import ast
    import inspect
    import textwrap

    from utils.config_manager import workshop as workshop_mixin

    tree = ast.parse(
        textwrap.dedent(inspect.getsource(workshop_mixin.WorkshopMixin.load_workshop_config))
    )
    guards = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "running_on_event_loop"
    ]
    assert guards, (
        "缺配置分支没有事件循环守卫 —— 首次 POST /config 创建文件期间，"
        "循环上的 get_workshop_path() 会卡在写者锁上"
    )


def test_an_in_flight_read_cannot_clobber_the_cache_with_a_stale_snapshot(tmp_path, monkeypatch):
    """A read that started before a save must not cache its pre-save snapshot.

    The read runs outside the lock, so it can begin before `POST /config`
    commits and return after it. Letting it refresh last-known-good would make
    a later transient-read fallback hand back the configuration from *before*
    the change — harder to notice than falling back to defaults.
    """
    from utils.config_manager import workshop as workshop_mixin

    config_path = tmp_path / "workshop_config.json"
    config_path.write_text(json.dumps({"user_mod_folder": "/old"}), encoding="utf-8")

    class _CM(workshop_mixin.WorkshopMixin):
        def __init__(self):
            import threading

            self._workshop_config_lock = threading.RLock()
            self.workshop_dir = tmp_path / "default"

        def get_workshop_config_path(self):
            return config_path

        def get_runtime_config_path(self, name):
            return config_path

        def ensure_config_directory(self):
            return None

        def _rebase_workshop_config_after_storage_migration(self, config):
            return config

    cm = _CM()
    monkeypatch.setattr(
        "utils.cloudsave_runtime.assert_cloudsave_writable", lambda *a, **kw: None
    )

    # 模拟「读开始 → 中途发生一次 save → 读才回来」：读到的是旧内容。
    def _slow_read(*args, **kwargs):
        cm.save_workshop_config({"user_mod_folder": "/new"})
        return {"user_mod_folder": "/old"}

    monkeypatch.setattr(workshop_mixin, "read_json_tolerating_replace", _slow_read)
    assert cm.load_workshop_config()["user_mod_folder"] == "/old"

    busy = PermissionError(13, "Access is denied")
    busy.winerror = 32
    monkeypatch.setattr(
        workshop_mixin, "read_json_tolerating_replace",
        lambda *a, **kw: (_ for _ in ()).throw(busy),
    )
    assert cm.load_workshop_config()["user_mod_folder"] == "/new", (
        "在飞的旧读把缓存盖回了保存之前的配置"
    )


def test_the_fallback_does_not_mask_a_genuinely_broken_config(tmp_path, monkeypatch):
    """Only the transient replace-busy error may fall back to the cache.

    Masking every failure means malformed JSON or a revoked permission leaves
    upload and publish silently working against the previous workshop root
    forever, instead of surfacing the broken configuration.
    """
    from utils.config_manager import workshop as workshop_mixin

    config_path = tmp_path / "workshop_config.json"
    config_path.write_text(json.dumps({"user_mod_folder": "/good"}), encoding="utf-8")

    class _CM(workshop_mixin.WorkshopMixin):
        def __init__(self):
            import threading

            self._workshop_config_lock = threading.RLock()
            self.workshop_dir = tmp_path / "default"

        def get_workshop_config_path(self):
            return config_path

        def _rebase_workshop_config_after_storage_migration(self, config):
            return config

    cm = _CM()
    assert cm.load_workshop_config()["user_mod_folder"] == "/good"

    monkeypatch.setattr(
        workshop_mixin, "read_json_tolerating_replace",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("malformed json")),
    )
    broken = cm.load_workshop_config()
    assert "user_mod_folder" not in broken, (
        "配置真坏了却拿缓存盖住了——upload/publish 会一直对着旧根目录干活"
    )
    assert broken["default_workshop_folder"] == str(cm.workshop_dir)
