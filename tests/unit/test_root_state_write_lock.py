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
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import storage_location_router as router_module
from main_routers.shared_state import init_shared_state
from utils import root_state_lock
from utils import storage_location_bootstrap as bootstrap_module
from utils.cloudsave_runtime import ROOT_MODE_MAINTENANCE_READONLY, ROOT_MODE_NORMAL
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


def _iter_project_python_files():
    """Every non-test project .py file, discovered rather than listed.

    A hardcoded list would only ever cover the call sites that existed when it
    was written; the whole point of these guards is to catch the next one.
    """
    skip_parts = {
        ".git", ".venv", "node_modules", "__pycache__", "tests", "build", "dist",
        ".claude", ".codex-tmp", "frontend", "deps", "docs",
    }
    for path in _REPO_ROOT.rglob("*.py"):
        if any(part in skip_parts for part in path.parts):
            continue
        yield path


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
    for path in _iter_project_python_files():
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
    reads = {"load_root_state", "get_root_state"}
    writes = {"save_root_state"}
    offenders: list[str] = []

    for path in _iter_project_python_files():
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

            called = {
                _called_name(child)
                for child in ast.walk(node)
                if isinstance(child, ast.Call)
            }
            if called & writes and not (called & reads):
                offenders.append(
                    f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno}"
                )

    assert not offenders, (
        "这些 root_state_transaction 块里写了盘却没在块内重读——写回去的是锁外读到的"
        "pre-image，会把这期间别人提交的字段整份盖掉：\n  " + "\n  ".join(offenders)
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

    Rollbacks are the one exception and they are recognised by shape rather than
    by name: they all live inside an ``except`` handler and are deliberately
    synchronous, because awaiting in an except handler makes the rollback itself
    cancellable (``CancelledError`` is a ``BaseException``, so the surrounding
    ``except Exception`` would not catch it).
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
        if isinstance(node, ast.Call) and async_owner is not None and not in_except:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name in _STORAGE_WRITE_PRIMITIVES:
                offenders.append(f"{name}() at line {node.lineno} in async {async_owner}()")
        for child in ast.iter_child_nodes(node):
            _walk(child, async_owner=async_owner, in_except=in_except)

    for top in ast.iter_child_nodes(tree):
        _walk(top, async_owner=None, in_except=False)

    assert not offenders, (
        "这些 storage 写原语直接躺在 async 函数体里，等于给写序列留了插 await 的位置：\n  "
        + "\n  ".join(offenders)
    )
