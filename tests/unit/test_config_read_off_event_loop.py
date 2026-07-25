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


def _sync_config_reads_inside_async_defs(path: Path) -> list[tuple[int, str, str]]:
    """Return (lineno, called name, enclosing async def) for un-awaited sync config reads.

    Uses the AST rather than line matching: a call split across lines is invisible to grep,
    and only the AST tells us whether the nearest enclosing function is an ``async def``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str, str]] = []
    # 栈里同时压 def / async def / lambda：lambda 体是同步可调用对象，
    # 里面的调用并不发生在 async def 的执行流上。
    stack: list[tuple[ast.AST, bool]] = []

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.AsyncFunctionDef):
            stack.append((node, True))
            for child in ast.iter_child_nodes(node):
                walk(child)
            stack.pop()
            return
        if isinstance(node, (ast.FunctionDef, ast.Lambda)):
            stack.append((node, False))
            for child in ast.iter_child_nodes(node):
                walk(child)
            stack.pop()
            return
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None
            )
            enclosing = getattr(stack[-1][0], "name", "<lambda>") if stack else ""
            # aget_model_api_config 在拿到快照时直接同步解析（无 IO），那是它的实现本体，不是违规。
            if name in SYNC_CONFIG_READERS and stack and stack[-1][1] and enclosing not in ASYNC_DUALS:
                hits.append((node.lineno, name, enclosing))
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree)
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
                    f"async def {enclosing} -> {name}()"
                )

    assert not offenders, (
        "以下 async def 里直接调用了同步配置读，请改用 aget_core_config / "
        "aget_model_api_config:\n  " + "\n  ".join(offenders)
    )
