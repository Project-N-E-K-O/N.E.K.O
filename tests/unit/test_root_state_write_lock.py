"""Guards for the root_state writer lock and the read-path no-write rule.

Three invariants are pinned here, each with its dual so that "the guard is
green" cannot mean "the guard never ran":

1. writes take ``utils.root_state_lock``; reads never do (and a worker holding
   the lock cannot stall a read);
2. ``build_storage_location_bootstrap_payload`` only persists a reconciled
   ``legacy_cleanup_pending`` when the caller opts in, and every opt-in call
   site sits behind ``_storage_mutation_lock``;
3. the ``delete_storage_migration`` → ``save_storage_policy`` →
   ``set_root_mode`` write sequences stay inside synchronous functions, so no
   await — and therefore no cancellation point — can be introduced between them.
"""
import ast
import asyncio
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import storage_location_router as router_module
from main_routers.shared_state import init_shared_state
from utils import root_state_lock
from utils import storage_location_bootstrap as bootstrap_module
from utils.cloudsave_runtime import (
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    ROOT_MODE_MAINTENANCE_READONLY,
    ROOT_MODE_NORMAL,
    set_root_mode,
)
from utils.cloudsave_runtime import fence as fence_module
from utils.config_manager import ConfigManager
from utils.storage_migration import get_storage_migration_path, save_storage_migration
from utils.storage_policy import get_storage_policy_path, save_storage_policy

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 这几个是 storage 状态的"写原语"。它们两两之间没有 await 是当前实现的硬前提：
# 中间插一个 await 就能被取消停在"检查点已删、root mode 未改"上。
_STORAGE_WRITE_PRIMITIVES = frozenset(
    {
        "delete_storage_migration",
        "save_storage_migration",
        "save_storage_policy",
        "set_root_mode",
        "save_root_state",
        "create_pending_storage_migration",
    }
)


class _RecordingLock:
    """A lock that counts how many times it was entered."""

    def __init__(self) -> None:
        self._inner = threading.RLock()
        self.entered = 0

    def __enter__(self):
        self.entered += 1
        return self._inner.__enter__()

    def __exit__(self, *exc_info):
        return self._inner.__exit__(*exc_info)


def _make_real_config_manager(tmp_path: Path) -> ConfigManager:
    standard_root = tmp_path / "anchor-base"
    with (
        patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_path / "runtime-parent"),
        patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[standard_root]),
    ):
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [standard_root]
    return config_manager


def _build_client(config_manager) -> TestClient:
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=config_manager,
        logger=None,
    )
    app = FastAPI()
    app.include_router(router_module.router)
    return TestClient(app)


def _called_name(call: ast.Call) -> str:
    """``f(...)`` -> ``"f"``; ``a.b.f(...)`` -> ``"f"``; anything else -> ``""``."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _own_nodes(scope: ast.AST):
    """Descendants of ``scope`` excluding anything inside a nested function.

    Nested defs are their own scope — a closure's re-read does not make its
    enclosing route safe, and vice versa.
    """
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from _own_nodes(child)


def _is_root_state_write(call: ast.Call) -> bool:
    return _called_name(call) == "save_root_state"


def _is_root_state_read(call: ast.Call) -> bool:
    name = _called_name(call)
    if name in {"load_root_state", "get_root_state"}:
        return True
    # 间接读，例如 self._load_json_file(self.root_state_path, default_value={})。
    # 只认名字里带 load/read 的，免得把 _save_local_state_json_file(self.root_state_path…)
    # 也算成读。
    lowered = name.lower()
    if "load" not in lowered and "read" not in lowered:
        return False
    operands = list(call.args) + [keyword.value for keyword in call.keywords]
    return any(
        isinstance(operand, ast.Attribute) and operand.attr == "root_state_path"
        for operand in operands
    )


_SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", "tests", "build", "dist",
    ".claude", ".codex-tmp", "frontend", "deps", "docs",
}

# 这三条 AST 护栏靠 _iter_project_python_files 供料，扫不到文件就等于全绿。
# 主仓库当前有 1000+ 个在扫描范围内的 .py，取一个远低于它、又远高于 0 的下界。
_MIN_SCANNED_FILES = 200


def _iter_project_python_files():
    """Every non-test project .py file, discovered rather than listed.

    A hardcoded list would only ever cover the call sites that existed when it
    was written; the whole point of these guards is to catch the next one.

    ⚠️ The skip list is matched against the path **relative to the repo root**,
    never the absolute path. Matching the absolute path made every one of these
    guards scan zero files when the checkout itself lives under a directory
    named in the list — which is exactly what happened in a
    ``.claude/worktrees/...`` worktree: locally vacuous, green in CI (whose
    checkout path has no such component), so nothing ever reported it.
    """
    for path in _REPO_ROOT.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.relative_to(_REPO_ROOT).parts):
            continue
        yield path


def _project_python_files() -> list[Path]:
    """``_iter_project_python_files`` plus the "did we actually scan anything" check."""
    paths = list(_iter_project_python_files())
    assert len(paths) >= _MIN_SCANNED_FILES, (
        f"只扫到 {len(paths)} 个文件（下界 {_MIN_SCANNED_FILES}）——发现逻辑坏了，"
        "下面所有 AST 护栏都会假绿"
    )
    return paths


# ── 1. 写者拿锁 / 读者不拿锁（对偶） ──────────────────────────────────


@pytest.mark.unit
def test_save_root_state_takes_the_writer_lock(tmp_path, monkeypatch):
    config_manager = _make_real_config_manager(tmp_path)
    recorder = _RecordingLock()
    monkeypatch.setattr(root_state_lock, "_ROOT_STATE_WRITE_LOCK", recorder)

    config_manager.save_root_state(config_manager.build_default_root_state())

    assert recorder.entered >= 1, (
        "save_root_state 没拿写者锁：两个写者会各自读到同一份 pre-image，"
        "后写的那个把先写的字段整份盖掉"
    )


@pytest.mark.unit
def test_load_root_state_does_not_take_the_writer_lock(tmp_path, monkeypatch):
    config_manager = _make_real_config_manager(tmp_path)
    config_manager.save_root_state(config_manager.build_default_root_state())

    recorder = _RecordingLock()
    monkeypatch.setattr(root_state_lock, "_ROOT_STATE_WRITE_LOCK", recorder)

    config_manager.load_root_state()

    assert recorder.entered == 0, (
        "读路径拿了写者锁。存储页在按 STORAGE_STATUS_POLL_INTERVAL_MS 轮询 GET /status，"
        "写在工作线程里持锁时这个读就会被卡住——阻塞经由锁原路传回事件循环"
    )


@pytest.mark.unit
def test_read_is_not_blocked_while_a_worker_holds_the_writer_lock(tmp_path):
    """The interleaving is forced, not hoped for: probability finds nothing here."""
    config_manager = _make_real_config_manager(tmp_path)
    config_manager.save_root_state(config_manager.build_default_root_state())

    holding = threading.Event()
    release = threading.Event()
    hold_seconds = 3.0

    def _hold_the_lock() -> None:
        with root_state_lock.root_state_transaction():
            holding.set()
            release.wait(hold_seconds)

    worker = threading.Thread(target=_hold_the_lock, daemon=True)
    worker.start()
    try:
        assert holding.wait(5), "worker 没能拿到锁"
        started = time.perf_counter()
        state = config_manager.load_root_state()
        elapsed = time.perf_counter() - started
    finally:
        release.set()
        worker.join(timeout=5)

    # join 超时只是返回，不会让用例红——显式断言线程真的收了
    assert not worker.is_alive()
    assert isinstance(state, dict)
    assert elapsed < hold_seconds / 2, (
        f"读路径等了 {elapsed:.3f}s，说明它在等工作线程手里那把写者锁"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifecycle_transaction_hands_off_to_to_thread_only():
    """Inherited workers may write, while an unrelated thread stays blocked."""
    inherited_entered = threading.Event()
    unrelated_started = threading.Event()
    unrelated_entered = threading.Event()

    def _inherited_writer() -> None:
        with root_state_lock.root_state_transaction():
            inherited_entered.set()

    def _unrelated_writer() -> None:
        unrelated_started.set()
        with root_state_lock.root_state_transaction():
            unrelated_entered.set()

    unrelated = threading.Thread(target=_unrelated_writer)
    with root_state_lock.root_state_lifecycle_transaction():
        await asyncio.wait_for(asyncio.to_thread(_inherited_writer), timeout=1)
        assert inherited_entered.is_set()

        unrelated.start()
        assert unrelated_started.wait(1)
        assert not unrelated_entered.wait(0.1)

    unrelated.join(timeout=1)
    assert not unrelated.is_alive()
    assert unrelated_entered.is_set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifecycle_transaction_keeps_loop_live_for_unrelated_worker():
    """A worker may wait for the lifecycle lock without blocking its event loop."""
    begin_unrelated = asyncio.Event()
    unrelated_started = threading.Event()
    unrelated_entered = threading.Event()

    def _unrelated_writer() -> None:
        unrelated_started.set()
        with root_state_lock.root_state_transaction():
            unrelated_entered.set()

    async def _unrelated_request() -> None:
        await begin_unrelated.wait()
        await asyncio.to_thread(_unrelated_writer)

    unrelated_request = asyncio.create_task(_unrelated_request())
    with root_state_lock.root_state_lifecycle_transaction():
        begin_unrelated.set()
        assert await asyncio.to_thread(unrelated_started.wait, 1)
        await asyncio.sleep(0.05)
        assert not unrelated_entered.is_set()

    await asyncio.wait_for(unrelated_request, timeout=1)
    assert unrelated_entered.is_set()


# ── 2. 读路径不写 root_state（对偶 + 调用点） ─────────────────────────


@pytest.mark.unit
def test_bootstrap_payload_does_not_persist_reconcile_by_default(tmp_path, monkeypatch):
    config_manager = _make_real_config_manager(tmp_path)
    base_state = dict(config_manager.build_default_root_state())
    base_state["legacy_cleanup_pending"] = False
    config_manager.save_root_state(base_state)

    monkeypatch.setattr(bootstrap_module, "_derive_legacy_cleanup_pending", lambda **_kwargs: True)

    payload = bootstrap_module.build_storage_location_bootstrap_payload(config_manager)

    assert payload["legacy_cleanup_pending"] is True, "派生值应该照常出现在 payload 里"
    assert config_manager.load_root_state().get("legacy_cleanup_pending") is False, (
        "默认参数下把 reconcile 落盘了：这条路径挂在被持续轮询的 GET /status 上，"
        "会跟变更路由的回滚互相盖"
    )


@pytest.mark.unit
def test_bootstrap_payload_persists_reconcile_when_opted_in(tmp_path, monkeypatch):
    config_manager = _make_real_config_manager(tmp_path)
    base_state = dict(config_manager.build_default_root_state())
    base_state["legacy_cleanup_pending"] = False
    config_manager.save_root_state(base_state)

    monkeypatch.setattr(bootstrap_module, "_derive_legacy_cleanup_pending", lambda **_kwargs: True)

    bootstrap_module.build_storage_location_bootstrap_payload(config_manager, persist_reconcile=True)

    assert config_manager.load_root_state().get("legacy_cleanup_pending") is True, (
        "显式 opt-in 也没落盘，那 reconcile 这条自愈路径就整个没了"
    )


@pytest.mark.unit
def test_storage_location_read_routes_leave_root_state_untouched(tmp_path, monkeypatch):
    """Drive the real read endpoints, not just the helper they call.

    Testing only ``build_storage_location_bootstrap_payload``'s default would
    stay green if a route started passing ``persist_reconcile=True``.
    """
    config_manager = _make_real_config_manager(tmp_path)
    monkeypatch.setattr(bootstrap_module, "_derive_legacy_cleanup_pending", lambda **_kwargs: True)

    read_paths = sorted(
        route.path
        for route in router_module.router.routes
        if "GET" in getattr(route, "methods", set()) and "{" not in route.path
    )
    assert read_paths, "一条 GET 路由都没发现，说明发现逻辑坏了，不是真的没有"

    client = _build_client(config_manager)
    for path in read_paths + ["/api/storage/location/exit"]:
        base_state = dict(config_manager.build_default_root_state())
        base_state["legacy_cleanup_pending"] = False
        config_manager.save_root_state(base_state)

        if path.endswith("/exit"):
            response = client.post(path, headers={"X-Neko-Storage-Action": "exit"})
        else:
            response = client.get(path)

        # 500 = 未处理异常，说明路由根本没跑通；其余状态码（含 /exit 在没有
        # shutdown 回调时的 503）都算真的跑到了业务分支。
        assert response.status_code != 500, f"{path} -> {response.status_code}"
        assert config_manager.load_root_state().get("legacy_cleanup_pending") is False, (
            f"{path} 在读路径上写了 root_state"
        )


@pytest.mark.unit
def test_persist_reconcile_opt_in_only_happens_behind_the_mutation_lock():
    """Every ``persist_reconcile=True`` must sit in a ``*_locked`` helper.

    ``_storage_mutation_lock`` is only ever taken by the thin route wrappers that
    delegate to ``_..._locked``; that naming is the machine-checkable shape of
    "this call holds the lock".
    """
    offenders: list[str] = []
    for path in _project_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        enclosing: list[ast.AST] = []

        def _visit(node: ast.AST) -> None:
            is_function = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            if is_function:
                enclosing.append(node)
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg != "persist_reconcile":
                        continue
                    if not (isinstance(keyword.value, ast.Constant) and keyword.value.value is True):
                        continue
                    owner = enclosing[-1].name if enclosing else "<module>"
                    if not owner.endswith("_locked"):
                        offenders.append(
                            f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno} in {owner}()"
                        )
            for child in ast.iter_child_nodes(node):
                _visit(child)
            if is_function:
                enclosing.pop()

        _visit(tree)

    assert not offenders, (
        "这些调用点在没拿 _storage_mutation_lock 的地方要求把 reconcile 落盘：\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_stale_mode_recovery_keeps_a_concurrent_writers_fields(tmp_path, monkeypatch):
    """The caller's pre-image is read outside the lock; the write must not use it."""
    config_manager = _make_real_config_manager(tmp_path)
    initial = dict(config_manager.build_default_root_state())
    initial["mode"] = ROOT_MODE_MAINTENANCE_READONLY
    initial["last_known_good_root"] = "OLD"
    config_manager.save_root_state(initial)

    # 调用方（bootstrap）在拿锁之前读到的那一份
    stale_pre_image = config_manager.load_root_state()

    # 另一个写者在"调用方读完"和"自愈写入"之间提交了一轮
    committed = dict(config_manager.load_root_state())
    committed["last_known_good_root"] = "NEW"
    config_manager.save_root_state(committed)

    monkeypatch.setattr(fence_module, "_should_preserve_write_blocking_mode", lambda *_a, **_k: False)
    monkeypatch.setattr(fence_module, "_process_holds_cloud_apply_lock", lambda: False)
    monkeypatch.setattr(fence_module, "acquire_cloud_apply_lock", lambda _cm: True)
    monkeypatch.setattr(fence_module, "release_cloud_apply_lock", lambda _cm: None)

    recovered, did_recover = fence_module._recover_stale_write_blocking_mode(
        config_manager, stale_pre_image
    )

    assert did_recover is True
    assert recovered["mode"] == ROOT_MODE_NORMAL
    on_disk = config_manager.load_root_state()
    assert on_disk["mode"] == ROOT_MODE_NORMAL, "自愈没生效"
    assert on_disk["last_known_good_root"] == "NEW", (
        "自愈把并发写者已提交的字段盖回了锁外读到的旧值"
    )


@pytest.mark.unit
def test_cloudsave_bootstrap_keeps_a_write_that_lands_mid_flight(tmp_path, monkeypatch):
    """Guard the caller too, not just the helper Greptile pointed at.

    ``bootstrap_local_cloudsave_environment`` loads root_state, does a pile of
    work, then edits and saves it. Fixing only
    ``_recover_stale_write_blocking_mode`` would have been pointless: this
    function overwrites with its own stale pre-image two lines later.
    """
    from utils.cloudsave_runtime import bootstrap as cloudsave_bootstrap

    config_manager = _make_real_config_manager(tmp_path)
    base = dict(config_manager.build_default_root_state())
    base["last_migration_source"] = "OLD"
    config_manager.save_root_state(base)

    def _commit_midway(cm):
        # 强制交错：bootstrap 已经读过 root_state、还没写回去，此刻另一个写者提交一轮
        state = dict(cm.load_root_state())
        state["last_migration_source"] = "NEW"
        cm.save_root_state(state)
        return {
            "migrated": False,
            "source": "",
            "copied_paths": [],
            "backup_path": "",
            "repair_reason": "",
            "result": "",
        }

    monkeypatch.setattr(
        cloudsave_bootstrap, "import_legacy_runtime_root_if_needed", _commit_midway
    )

    cloudsave_bootstrap.bootstrap_local_cloudsave_environment(config_manager)

    assert config_manager.load_root_state()["last_migration_source"] == "NEW", (
        "bootstrap 用锁外读到的 pre-image 把中途提交的那一轮盖掉了"
    )


@pytest.mark.unit
def test_every_locked_write_reloads_root_state_inside_the_block():
    """A transaction that writes must also read *inside* the block.

    Taking the lock only serializes writers. If the value being written was
    derived from a pre-image loaded before the lock, the write still clobbers
    whatever another writer committed in between — the lock makes it orderly,
    not correct. Greptile caught exactly this in
    ``_recover_stale_write_blocking_mode`` after the first round of this PR.

    Deliberate snapshot restores (``_restore_storage_mutation_state``, the
    ``/restart`` rollback) are outside this rule by construction: they do not
    open a transaction at all, they lean on the lock inside ``save_root_state``,
    and writing a stale pre-image is precisely their job.
    """
    offenders: list[str] = []

    for path in _project_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.With):
                continue
            opens_transaction = any(
                isinstance(item.context_expr, ast.Call)
                and _called_name(item.context_expr) == "root_state_transaction"
                for item in node.items
            )
            if not opens_transaction:
                continue

            # 用同一对 shape 谓词，别退回按名字比对：storage_roots 那处的读是
            # self._load_json_file(self.root_state_path, ...)，名字对不上但确实是读。
            calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
            if any(_is_root_state_write(c) for c in calls) and not any(
                _is_root_state_read(c) for c in calls
            ):
                offenders.append(
                    f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno}"
                )

    assert not offenders, (
        "这些 root_state_transaction 块里写了盘却没在块内重读——写回去的是锁外读到的"
        "pre-image，会把这期间别人提交的字段整份盖掉：\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_read_modify_write_of_root_state_happens_inside_one_transaction():
    """A function that both reads and writes root_state must do both under the lock.

    The sibling guard above only inspects code already inside a transaction, so a
    site that never opens one is invisible to it — which is exactly how
    ``_persist_selected_root_unavailable_recovery_state`` slipped through (it read
    the file directly, then called ``save_root_state``, whose internal lock covers
    only the write). This rule keys on shape, not on a list of names:

    * reads-and-writes  -> both must sit in one ``root_state_transaction()`` block
    * writes only       -> a snapshot restore / a create-if-missing default; the
                           stale pre-image *is* the payload, so there is nothing
                           to re-read and nothing to guard
    * reads only        -> harmless
    """
    offenders: list[str] = []

    for path in _project_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            own = list(_own_nodes(node))
            calls = [n for n in own if isinstance(n, ast.Call)]
            if not any(_is_root_state_write(c) for c in calls):
                continue
            if not any(_is_root_state_read(c) for c in calls):
                continue

            guarded = False
            for block in own:
                if not isinstance(block, ast.With):
                    continue
                if not any(
                    isinstance(item.context_expr, ast.Call)
                    and _called_name(item.context_expr) == "root_state_transaction"
                    for item in block.items
                ):
                    continue
                inner = [n for n in _own_nodes(block) if isinstance(n, ast.Call)]
                if any(_is_root_state_read(c) for c in inner) and any(
                    _is_root_state_write(c) for c in inner
                ):
                    guarded = True
                    break

            if not guarded:
                offenders.append(
                    f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno} {node.name}()"
                )

    assert not offenders, (
        "这些函数既读又写 root_state，但读和写没有落在同一个 root_state_transaction() "
        "块里——锁只包住写的话，另一个写者提交的字段会被这份 pre-image 盖掉：\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_startup_marker_keeps_its_eligibility_check_in_the_writing_scope():
    """Offloading the write must not leave its precondition behind on the loop.

    ``should_write_root_mode_normal_after_startup`` decides whether the startup
    marker may be written. While that decision and the write sat next to each
    other on the event loop they were indivisible; putting the write in a worker
    inserted an await between them, so a storage mutation worker could commit
    ``ROOT_MODE_MAINTENANCE_READONLY`` in the gap and get stomped back to normal.

    The rule is shape-based: any scope that does the check *and* the write itself
    must hold both inside one ``root_state_transaction()``. In practice that means
    nobody hand-rolls the pair — they call ``mark_startup_successful``, which is
    the single scope that legitimately does both (under the lock). Sites that only
    read the predicate, or only write, are untouched.
    """
    check = "should_write_root_mode_normal_after_startup"
    offenders: list[str] = []

    for path in _project_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for scope in ast.walk(tree):
            if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            own_calls = [n for n in _own_nodes(scope) if isinstance(n, ast.Call)]
            names = {_called_name(c) for c in own_calls}
            if not ({check, "set_root_mode"} <= names):
                continue

            guarded = False
            for block in _own_nodes(scope):
                if not isinstance(block, ast.With):
                    continue
                if not any(
                    isinstance(item.context_expr, ast.Call)
                    and _called_name(item.context_expr) == "root_state_transaction"
                    for item in block.items
                ):
                    continue
                inner = {
                    _called_name(c)
                    for c in _own_nodes(block)
                    if isinstance(c, ast.Call)
                }
                if {check, "set_root_mode"} <= inner:
                    guarded = True
                    break
            if not guarded:
                offenders.append(
                    f"{path.relative_to(_REPO_ROOT).as_posix()}:{scope.lineno} {scope.name}()"
                )

    assert not offenders, (
        "这些作用域自己拼了「判定 + 写启动标记」，却没把两步收进同一个 "
        "root_state_transaction()。中间没有 await 只挡得住同一循环上的协程，挡不住"
        "工作线程——存储变更路由的写现在就在工作线程上。改调 mark_startup_successful："
        "\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_rollback_on_exception_also_rolls_back_on_cancellation():
    """If a try bothers to roll back, it must roll back on cancellation too.

    ``CancelledError`` is a ``BaseException``, so ``except Exception`` does not
    see it. Any async ``try`` that undoes its work in an ``except Exception``
    handler therefore has a hole: a client disconnect skips exactly the cleanup
    the handler exists for. Scoped to async bodies — synchronous code cannot be
    cancelled this way.
    """
    rollback_calls = {
        "_restore_storage_mutation_state",
        "save_root_state",
        "save_storage_migration",
        "delete_storage_migration",
    }
    offenders: list[str] = []

    for path in _project_python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue

        for scope in ast.walk(tree):
            if not isinstance(scope, ast.AsyncFunctionDef):
                continue
            # 回滚 handler 内部那些 best-effort 的 try/except 不算：它们已经在异常
            # 处理里了，再要求它们处理取消既没有意义也无处可退。
            inside_handler = {
                nested
                for node in ast.walk(scope)
                if isinstance(node, ast.ExceptHandler)
                for nested in ast.walk(node)
                if isinstance(nested, ast.Try)
            }
            for node in _own_nodes(scope):
                if not isinstance(node, ast.Try) or node in inside_handler:
                    continue
                rolls_back = any(
                    _called_name(c) in rollback_calls
                    for handler in node.handlers
                    for c in ast.walk(handler)
                    if isinstance(c, ast.Call)
                )
                if not rolls_back:
                    continue
                # CancelledError 显式接，或者干脆接 BaseException——两者都能兜住取消。
                handles_cancel = any(
                    "CancelledError" in ast.dump(handler.type)
                    or "BaseException" in ast.dump(handler.type)
                    for handler in node.handlers
                    if handler.type is not None
                )
                if not handles_cancel:
                    offenders.append(
                        f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno} in {scope.name}()"
                    )

    assert not offenders, (
        "这些 async try 在 except 里做了回滚，却没有处理 CancelledError——"
        "它是 BaseException，except Exception 接不住，客户端断连就正好跳过回滚：\n  "
        + "\n  ".join(offenders)
    )


# ── 3. offload 不许把 _storage_mutation_lock 提前让出去 ───────────────


@pytest.mark.unit
async def test_cancelled_storage_job_waits_for_the_worker_before_unwinding():
    """Cancellation must not hand the mutation lock to the next request mid-write.

    ``asyncio.to_thread`` cancellation only cancels the awaiting future; the
    worker keeps going. If the await returned immediately, the route's
    ``async with _storage_mutation_lock`` would unwind while the worker was
    still writing, and a second mutation could interleave with it. Before these
    writes moved off the loop they were uncancellable, so this restores what the
    lock used to guarantee.
    """
    started = threading.Event()
    finished = threading.Event()

    def _job() -> str:
        started.set()
        time.sleep(0.3)
        finished.set()
        return "done"

    task = asyncio.ensure_future(router_module._run_locked_storage_job(_job))
    assert await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set(), (
        "取消让 await 立刻返回了——工作线程还在写，_storage_mutation_lock 已经松开，"
        "下一个变更请求会跟它在同一批文件上交错"
    )


@pytest.mark.unit
async def test_repeatedly_cancelled_storage_job_still_waits_for_the_worker():
    """One suppressed cancellation is not enough.

    A request cancellation followed by a server-shutdown cancellation lands the
    second ``CancelledError`` on the handler's own await. Suppressing a single
    one and unwinding would release the mutation lock with the worker still
    writing — the exact hole this helper exists to close.
    """
    started = threading.Event()
    finished = threading.Event()

    def _job() -> str:
        started.set()
        time.sleep(0.4)
        finished.set()
        return "done"

    task = asyncio.ensure_future(router_module._run_locked_storage_job(_job))
    assert await asyncio.get_running_loop().run_in_executor(None, started.wait, 5)

    task.cancel()
    # 让 handler 真的跑到"等 worker"那一步，第二次取消才打得中
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert finished.is_set(), (
        "第二次取消让 await 提前返回了——worker 还在写，锁已经松开"
    )


@pytest.mark.unit
async def test_storage_snapshot_waits_for_cloud_fence_transaction(tmp_path, monkeypatch):
    """A rollback snapshot must never capture a cloud fence's temporary mode."""
    config_manager = _make_real_config_manager(tmp_path)
    anchor_root = Path(config_manager.anchor_root)
    set_root_mode(config_manager, ROOT_MODE_NORMAL)

    fence_entered = threading.Event()
    release_fence = threading.Event()
    fence_errors: list[Exception] = []

    def _hold_cloud_fence() -> None:
        try:
            with fence_module.cloud_apply_fence(
                config_manager,
                mode=ROOT_MODE_BOOTSTRAP_IMPORTING,
                reason="test_snapshot_transaction",
            ):
                fence_entered.set()
                if not release_fence.wait(5):
                    raise TimeoutError("test did not release cloud fence")
        except Exception as exc:
            fence_errors.append(exc)

    fence_thread = threading.Thread(target=_hold_cloud_fence)
    fence_thread.start()
    assert await asyncio.get_running_loop().run_in_executor(None, fence_entered.wait, 5)

    transaction_attempted = threading.Event()
    snapshot_attempted = threading.Event()
    real_transaction = router_module.root_state_transaction
    real_snapshot = router_module._snapshot_storage_mutation_state

    @contextmanager
    def _observed_transaction():
        transaction_attempted.set()
        with real_transaction():
            yield

    def _observed_snapshot(*args, **kwargs):
        snapshot_attempted.set()
        return real_snapshot(*args, **kwargs)

    monkeypatch.setattr(router_module, "root_state_transaction", _observed_transaction)
    monkeypatch.setattr(router_module, "_snapshot_storage_mutation_state", _observed_snapshot)

    snapshot: dict[str, object] = {}
    task = asyncio.create_task(
        router_module._apply_storage_mutation_writes(
            config_manager,
            anchor_root=anchor_root,
            snapshot_out=snapshot,
            write=lambda: set_root_mode(config_manager, ROOT_MODE_MAINTENANCE_READONLY),
        )
    )
    assert await asyncio.get_running_loop().run_in_executor(None, transaction_attempted.wait, 5)
    assert not snapshot_attempted.is_set(), (
        "snapshot ran before cloud_apply_fence released its root-state transaction"
    )

    release_fence.set()
    try:
        await task
    finally:
        release_fence.set()
        fence_thread.join(timeout=5)

    assert not fence_thread.is_alive()
    assert not fence_errors
    assert snapshot_attempted.is_set()
    assert snapshot["root_state"]["mode"] == ROOT_MODE_NORMAL
    assert config_manager.load_root_state()["mode"] == ROOT_MODE_MAINTENANCE_READONLY


@pytest.mark.unit
def test_empty_snapshot_never_deletes_storage_state(tmp_path):
    """An un-taken snapshot must not be replayed as "these files did not exist".

    ``_restore_storage_mutation_state`` reads a missing ``migration`` / ``policy``
    key as proof the file was absent and deletes it. A real snapshot always has
    all three keys, so an empty dict can only mean the snapshot itself failed —
    in which case no write happened and there is nothing to roll back.
    """
    config_manager = _make_real_config_manager(tmp_path)
    anchor_root = Path(config_manager.anchor_root)

    save_storage_policy(
        config_manager,
        selected_root=config_manager.app_docs_dir,
        anchor_root=anchor_root,
        selection_source="test",
    )
    save_storage_migration(
        config_manager,
        {"status": "completed", "source_root": "", "target_root": ""},
        anchor_root=anchor_root,
    )
    policy_path = get_storage_policy_path(config_manager, anchor_root=anchor_root)
    migration_path = get_storage_migration_path(config_manager, anchor_root=anchor_root)
    assert policy_path.exists() and migration_path.exists()

    router_module._restore_storage_mutation_state(config_manager, {}, anchor_root=anchor_root)

    assert policy_path.exists(), "空快照回滚把存储策略文件 unlink 了"
    assert migration_path.exists(), "空快照回滚把迁移检查点删了"


# ── 4. 写序列保持原子（不许被 await 切开） ────────────────────────────


@pytest.mark.unit
def test_storage_write_primitives_never_sit_directly_in_an_async_body():
    """Keep the write sequences indivisible by keeping them out of async bodies.

    Event-loop callers must submit synchronous root-state writers through
    ``_run_locked_storage_job``. Otherwise an unrelated cloud lifecycle fence can
    make the route wait on a thread lock from the event loop, or an ``RLock`` can
    admit the unrelated coroutine as if it were the lifecycle owner.
    """
    source_path = _REPO_ROOT / "main_routers" / "storage_location_router.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    offenders: list[str] = []

    def _walk(node: ast.AST, *, async_owner: str | None, in_except: bool) -> None:
        if isinstance(node, ast.AsyncFunctionDef):
            for child in ast.iter_child_nodes(node):
                _walk(child, async_owner=node.name, in_except=False)
            return
        if isinstance(node, ast.FunctionDef):
            # 同步函数体就是我们想要的形状：里面插不进 await
            for child in ast.iter_child_nodes(node):
                _walk(child, async_owner=None, in_except=False)
            return
        if isinstance(node, ast.ExceptHandler):
            for child in ast.iter_child_nodes(node):
                _walk(child, async_owner=async_owner, in_except=True)
            return
        if isinstance(node, ast.Call) and async_owner is not None:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            persist_reconcile = name == "build_storage_location_bootstrap_payload" and any(
                keyword.arg == "persist_reconcile"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in node.keywords
            )
            if (
                name in _STORAGE_WRITE_PRIMITIVES
                or name == "_restore_storage_mutation_state"
                or persist_reconcile
            ):
                offenders.append(f"{name}() at line {node.lineno} in async {async_owner}()")
        for child in ast.iter_child_nodes(node):
            _walk(child, async_owner=async_owner, in_except=in_except)

    for top in ast.iter_child_nodes(tree):
        _walk(top, async_owner=None, in_except=False)

    assert not offenders, (
        "这些 storage 写原语直接躺在 async 函数体里，等于给写序列留了插 await 的位置：\n  "
        + "\n  ".join(offenders)
    )
