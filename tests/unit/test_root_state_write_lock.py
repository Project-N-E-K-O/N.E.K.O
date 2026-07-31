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
from utils.config_manager import ConfigManager

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


# ── 3. 写序列保持原子（不许被 await 切开） ────────────────────────────


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
