# -*- coding: utf-8 -*-
"""Regression: config reads issued by async code must not run on the event loop.

``get_core_config`` does open()+json.load() on core_config.json and
``get_model_api_config`` resolves everything on top of it. Both are sub-millisecond on a
warm SSD and arbitrarily slow under a mechanical disk or an antivirus scan -- and
main_server / memory_server / agent_server share a single event loop, so one blocking
read stalls all three.
"""
import ast
import asyncio
import time as real_time
from pathlib import Path

import pytest

from utils.config_manager.core_config import CoreConfigMixin


REPO_ROOT = Path(__file__).resolve().parents[2]

# 已转异步的目录：本仓库自有后端代码。plugin/ 与其内部的同步包装函数
# （_get_text_guard_max_length / _start_tts_thread 等）是后续批次，不在此闸门内。
GUARDED_DIRS = ("app", "brain", "main_logic", "main_routers", "memory", "utils")

SYNC_CONFIG_READERS = frozenset({"get_core_config", "get_model_api_config"})

# 异步对偶自身：aget_model_api_config 在调用方已给快照时直接同步解析（此时没有任何 IO），
# 这是它存在的意义，不是违规。
ASYNC_DUALS = frozenset({"aget_core_config", "aget_model_api_config"})

# 心跳间隔与「配置读慢多久」的对比：慢读必须显著超过心跳，否则用例分辨不出阻塞。
_HEARTBEAT_INTERVAL_S = 0.05
_SLOW_READ_S = 0.5
# Windows 定时器精度 ~15ms，正常心跳会落在 50-70ms；留到 200ms 仍远低于 500ms 的慢读。
_MAX_TOLERATED_GAP_S = 0.2


_MINIMAL_CORE_CONFIG = {
    "ENABLE_CUSTOM_API": False,
    "SUMMARY_MODEL": "summary-model",
    "OPENROUTER_API_KEY": "assist-key",
    "OPENROUTER_URL": "https://assist.example/v1",
}


class _SlowReadManager(CoreConfigMixin):
    """A config manager whose only cost is a slow synchronous core_config read."""

    def __init__(self, delay_s: float = _SLOW_READ_S):
        self._delay_s = delay_s
        self.read_count = 0

    def get_core_config(self):
        self.read_count += 1
        real_time.sleep(self._delay_s)
        return dict(_MINIMAL_CORE_CONFIG)


class _ExplodingReadManager(CoreConfigMixin):
    """A config manager that fails the test if core_config.json is read at all."""

    def get_core_config(self):
        raise AssertionError("调用方已提供快照，不应再读一次 core_config.json")


async def _max_heartbeat_gap(body) -> float:
    """Run ``body()`` while a 50ms heartbeat ticks; return the largest tick gap."""
    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat():
        last = real_time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            now = real_time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    # 让心跳先跑几拍，避免把 task 启动本身算进第一个间隔
    await asyncio.sleep(_HEARTBEAT_INTERVAL_S * 3)
    try:
        await body()
    finally:
        stop.set()
        await beat
    return max(gaps)


@pytest.mark.unit
async def test_aget_model_api_config_keeps_the_heartbeat_alive():
    """The async dual offloads the read, so concurrent tasks keep their cadence."""
    manager = _SlowReadManager()

    async def body():
        config = await manager.aget_model_api_config("summary")
        assert config["model"] == "summary-model"
        assert config["base_url"] == "https://assist.example/v1"

    max_gap = await _max_heartbeat_gap(body)
    assert max_gap < _MAX_TOLERATED_GAP_S, f"心跳被卡了 {max_gap:.2f}s，配置读没有真正离开事件循环"
    assert manager.read_count == 1


@pytest.mark.unit
async def test_sync_get_model_api_config_stalls_the_heartbeat():
    """Control case: the harness above must actually be able to see a blocked loop.

    Without this the first test would stay green even if aget_model_api_config silently
    degraded back to a synchronous read.
    """
    manager = _SlowReadManager()

    async def body():
        manager.get_model_api_config("summary")

    max_gap = await _max_heartbeat_gap(body)
    assert max_gap > _MAX_TOLERATED_GAP_S, "同步读没有卡住心跳，说明用例分辨不出阻塞（假绿）"


@pytest.mark.unit
async def test_aget_model_api_config_reuses_a_caller_snapshot():
    """Passing an already-read snapshot must skip the file read entirely."""
    manager = _ExplodingReadManager()

    config = await manager.aget_model_api_config(
        "summary", core_config=dict(_MINIMAL_CORE_CONFIG)
    )

    assert config["model"] == "summary-model"
    assert config["api_key"] == "assist-key"
    assert config["base_url"] == "https://assist.example/v1"


@pytest.mark.unit
async def test_a_single_call_reads_core_config_once_even_when_it_recurses():
    """game_main falls through to 'conversation'; that recursion must not re-read the file.

    Without threading the snapshot into the recursive call, one aget_model_api_config
    would pay two core_config.json reads -- and could straddle a concurrent config write.
    """
    manager = _SlowReadManager(delay_s=0.01)

    config = await manager.aget_model_api_config("game_main")

    assert manager.read_count == 1, f"递归回退又读了一次配置（共 {manager.read_count} 次）"
    assert config["base_url"] == "https://assist.example/v1"


# Offload helpers: a sync callable handed to one of these is SUPPOSED to be sync.
_OFFLOAD_FUNCS = frozenset({"to_thread", "run_in_executor", "run_sync"})


def _is_offload_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else None
    )
    return name in _OFFLOAD_FUNCS


def _offloaded_callable_names(tree: ast.AST) -> set[str]:
    """Names passed bare to to_thread/run_in_executor anywhere in the module.

    ``await asyncio.to_thread(self._blocking_helper)`` means _blocking_helper is
    meant to run on a worker thread, so a sync config read inside it is correct.

    A bare name is not an identity: two same-named ``def``s in one module cannot be
    told apart statically, so exempting by name would let the NON-offloaded twin
    through. Such ambiguous names are therefore dropped from the exemption set --
    fail closed, so the gate reports and a human decides, rather than going quiet.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not _is_offload_call(node):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name):
                names.add(arg.id)
            elif isinstance(arg, ast.Attribute):
                names.add(arg.attr)

    counts: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            counts[node.name] = counts.get(node.name, 0) + 1
    return {n for n in names if counts.get(n, 0) <= 1}


def _sync_config_reads_inside_async_defs(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, called name, enclosing function) for sync config reads on the loop.

    Uses the AST rather than line matching: a call split across lines is invisible to
    grep, and only the AST can tell whether the call actually executes on the event loop.

    A nested sync ``def`` / ``lambda`` defined inside an ``async def`` still runs on the
    loop when it is called there, so it INHERITS the async context rather than clearing
    it. The one exception is a callable handed to ``to_thread`` / ``run_in_executor``:
    that one genuinely runs on a worker thread, and a sync read inside it is correct.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offloaded = _offloaded_callable_names(tree)
    hits: list[tuple[int, str, str]] = []

    def walk(node: ast.AST, on_loop: bool, enclosing: str) -> None:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda)):
            name = getattr(node, "name", "<lambda>")
            if isinstance(node, ast.AsyncFunctionDef):
                inner = True
            elif name in offloaded:
                inner = False
            else:
                inner = on_loop
            for child in ast.iter_child_nodes(node):
                walk(child, inner, name)
            return

        if isinstance(node, ast.Call):
            func = node.func
            called = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None
            )
            # aget_model_api_config 拿到快照后直接同步解析（无 IO），那是它的实现本体，不是违规。
            if called in SYNC_CONFIG_READERS and on_loop and enclosing not in ASYNC_DUALS:
                hits.append((node.lineno, called, enclosing))
            offload = _is_offload_call(node)
            for arg in node.args:
                walk(arg, False if (offload and isinstance(arg, ast.Lambda)) else on_loop, enclosing)
            for kw in node.keywords:
                walk(kw.value, on_loop, enclosing)
            walk(node.func, on_loop, enclosing)
            return

        for child in ast.iter_child_nodes(node):
            walk(child, on_loop, enclosing)

    walk(tree, False, "<module>")
    return hits


@pytest.mark.unit
def test_async_code_never_calls_the_sync_config_readers():
    """Static gate: no ``async def`` in backend code may read config synchronously."""
    offenders: list[str] = []
    for directory in GUARDED_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for lineno, name, enclosing in _sync_config_reads_inside_async_defs(path):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT).as_posix()}:{lineno} "
                    f"in {enclosing}() -> {name}()"
                )

    assert not offenders, (
        "以下位置在事件循环上直接调用了同步配置读（async def 本体，或它内部会被同步"
        "调用的嵌套闭包），请改用 aget_core_config / aget_model_api_config；确实要在"
        "工作线程里同步读的，走 asyncio.to_thread:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_offload_exemption_is_dropped_for_ambiguous_names(tmp_path):
    """Two same-named nested defs, one offloaded: the other must NOT inherit the exemption.

    A bare name is not an identity. Exempting by name alone would let the twin that
    really is called on the loop hide a sync config read behind its offloaded sibling.
    """
    src = '''
import asyncio

async def offloads_it():
    def _resolve():
        return cm.get_model_api_config('summary')
    return await asyncio.to_thread(_resolve)

async def calls_it_on_the_loop():
    def _resolve():
        return cm.get_model_api_config('summary')
    return _resolve()
'''
    path = tmp_path / "ambiguous.py"
    path.write_text(src, encoding="utf-8")

    hits = _sync_config_reads_inside_async_defs(path)

    assert hits, "同名双胞胎里未卸载的那个被静默豁免了（按名字豁免的假绿）"


@pytest.mark.unit
def test_offload_exemption_still_holds_for_an_unambiguous_name(tmp_path):
    """Control: the ordinary single-definition offload must stay exempt."""
    src = '''
import asyncio

async def offloads_it():
    def _resolve():
        return cm.get_model_api_config('summary')
    return await asyncio.to_thread(_resolve)
'''
    path = tmp_path / "unambiguous.py"
    path.write_text(src, encoding="utf-8")

    assert _sync_config_reads_inside_async_defs(path) == [], "合法的 to_thread 卸载被误报"


class _MigratingManager(CoreConfigMixin):
    """Exercises only the openclawUrl 8089 -> 8088 write-back inside get_core_config."""

    def __init__(self, stored: dict, on_read=None):
        self.stored = stored
        self._on_read = on_read
        self.saved: list[dict] = []

    def load_json_config(self, filename, default_value=None):
        # 先让并发保存落地，再返回内容：模拟「worker 原始读已陈旧 → /core_api 保存
        # 落盘 → 迁移在锁内重读」这条时序，也正是重读能救回来的那条。
        if self._on_read is not None:
            self._on_read()
        return dict(self.stored)

    def save_json_config(self, filename, data):
        self.stored = dict(data)
        self.saved.append(dict(data))


@pytest.mark.unit
def test_openclaw_migration_does_not_clobber_a_concurrent_save():
    """The read-path migration must patch fresh content, not write back a stale snapshot.

    get_core_config is the only read that also writes (one-shot legacy port migration).
    Now that it runs under to_thread, a /core_api save can land between this worker's
    read and its write; saving the stale snapshot would silently drop the user's update.
    """
    manager = _MigratingManager({"openclawUrl": "http://127.0.0.1:8089"})

    # 模拟并发保存：worker 重读配置文件的那一刻，用户刚存下新的 API key
    def _concurrent_save():
        manager.stored = dict(manager.stored, coreApiKey="freshly-saved-key")

    manager._on_read = _concurrent_save
    manager._persist_openclaw_port_migration("http://127.0.0.1:8088")

    assert manager.stored["openclawUrl"] == "http://127.0.0.1:8088", "迁移没有落盘"
    assert manager.stored.get("coreApiKey") == "freshly-saved-key", (
        "并发保存的 coreApiKey 被陈旧快照顶掉了"
    )


@pytest.mark.unit
def test_openclaw_migration_skips_when_another_worker_already_migrated():
    """Double-checked under the lock: a second worker must not rewrite the file."""
    manager = _MigratingManager({"openclawUrl": "http://127.0.0.1:8088"})

    manager._persist_openclaw_port_migration("http://127.0.0.1:8088")

    assert manager.saved == [], "端口已是 8088 仍然又写了一次盘"
