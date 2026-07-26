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
import json
import time as real_time
from pathlib import Path

import pytest

from utils.config_manager import core_config as core_config_module
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


def _scope_offload_facts(fn: ast.AST) -> tuple[set[str], set[str]]:
    """Within ONE function body: names handed to an offload helper, and names called directly.

    Scoped deliberately. A bare name is not an identity — two modules, or even two
    functions, can each have a ``_resolve``. Pairing the offload with the definition
    inside the same body is what makes the exemption refer to a specific def.

    Nested function bodies are not descended into: their own offloads belong to their
    own scope, and are collected when the walker reaches them.
    """
    offloaded: set[str] = set()
    called: set[str] = set()

    def scan(node: ast.AST, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # 子作用域自己算
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else (
                    func.id if isinstance(func, ast.Name) else None
                )
                if name in _OFFLOAD_FUNCS:
                    for arg in child.args:
                        if isinstance(arg, ast.Name):
                            offloaded.add(arg.id)
                        elif isinstance(arg, ast.Attribute):
                            offloaded.add(arg.attr)
                elif name:
                    called.add(name)
            scan(child, False)

    scan(fn, True)
    return offloaded, called


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
    hits: list[tuple[int, str, str]] = []

    def walk(node: ast.AST, on_loop: bool, enclosing: str, exempt: frozenset[str]) -> None:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda)):
            name = getattr(node, "name", "<lambda>")
            if isinstance(node, ast.AsyncFunctionDef):
                inner = True
            elif name in exempt:
                inner = False
            else:
                inner = on_loop
            # 本作用域自己的卸载事实：同名同时被直调的，豁免撤销（fail-closed）——
            # 一个 _resolve 既能 to_thread(_resolve) 又能在协程里 _resolve()。
            offloaded, called = _scope_offload_facts(node)
            child_exempt = frozenset(offloaded - called)
            for child in ast.iter_child_nodes(node):
                walk(child, inner, name, child_exempt)
            return

        if isinstance(node, ast.Call):
            func = node.func
            called_name = func.attr if isinstance(func, ast.Attribute) else (
                func.id if isinstance(func, ast.Name) else None
            )
            # aget_model_api_config 拿到快照后直接同步解析（无 IO），那是它的实现本体，不是违规。
            if called_name in SYNC_CONFIG_READERS and on_loop and enclosing not in ASYNC_DUALS:
                hits.append((node.lineno, called_name, enclosing))
            offload = _is_offload_call(node)
            for arg in node.args:
                # lambda 直接作实参时按节点身份豁免，不经名字
                walk(arg, False if (offload and isinstance(arg, ast.Lambda)) else on_loop,
                     enclosing, exempt)
            for kw in node.keywords:
                walk(kw.value, on_loop, enclosing, exempt)
            walk(node.func, on_loop, enclosing, exempt)
            return

        for child in ast.iter_child_nodes(node):
            walk(child, on_loop, enclosing, exempt)

    walk(tree, False, "<module>", frozenset())
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
def test_offload_exemption_is_scoped_to_the_defining_function(tmp_path):
    """A same-named nested def in ANOTHER coroutine must not inherit the exemption.

    The exemption is paired with the definition inside one function body; a bare name
    is not an identity, so two coroutines can each have their own ``_resolve``.
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
    path = tmp_path / "scoped.py"
    path.write_text(src, encoding="utf-8")

    hits = _sync_config_reads_inside_async_defs(path)

    assert [h[2] for h in hits] == ["_resolve"], f"应只报直调那个 _resolve，实得 {hits}"
    assert hits[0][0] == 11, f"报错行应指向直调那侧的读，实得 {hits}"


@pytest.mark.unit
def test_offload_exemption_does_not_leak_into_a_nested_scope(tmp_path):
    """An inner coroutine's own same-named def must not ride the outer scope's exemption."""
    src = '''
import asyncio

async def outer():
    def _resolve():
        return cm.get_model_api_config('summary')

    async def inner():
        def _resolve():
            return cm.get_model_api_config('vision')
        return _resolve()

    await inner()
    return await asyncio.to_thread(_resolve)
'''
    path = tmp_path / "nested.py"
    path.write_text(src, encoding="utf-8")

    hits = _sync_config_reads_inside_async_defs(path)

    # 只该报内层那次（第 10 行 vision）；外层 _resolve 是纯卸载，仍豁免
    assert len(hits) == 1, f"外层豁免泄漏进内层作用域了：{hits}"
    assert hits[0][0] == 10, f"应报内层那次读，实得 {hits}"


@pytest.mark.unit
def test_offload_exemption_is_revoked_on_mixed_use(tmp_path):
    """One def both offloaded AND invoked directly: the direct call still blocks the loop.

    Exempting on the strength of the to_thread hand-off alone would hide the sync read
    that the direct invocation performs on the event loop.
    """
    src = '''
import asyncio

async def both_ways(fast_path):
    def _resolve():
        return cm.get_model_api_config('summary')
    if fast_path:
        return _resolve()
    return await asyncio.to_thread(_resolve)
'''
    path = tmp_path / "mixed.py"
    path.write_text(src, encoding="utf-8")

    assert _sync_config_reads_inside_async_defs(path), (
        "同一个 def 既卸载又直调时豁免必须撤销，否则直调那次的同步读被静默放过"
    )


@pytest.mark.unit
def test_offload_exemption_still_holds_for_a_pure_offload(tmp_path):
    """Control: hand a def to to_thread and never call it directly -> stays exempt."""
    src = '''
import asyncio

async def offloads_it():
    def _resolve():
        return cm.get_model_api_config('summary')
    return await asyncio.to_thread(_resolve)

async def uses_a_lambda():
    return await asyncio.to_thread(lambda: cm.get_model_api_config('summary'))
'''
    path = tmp_path / "pure_offload.py"
    path.write_text(src, encoding="utf-8")

    assert _sync_config_reads_inside_async_defs(path) == [], "合法的 to_thread 卸载被误报"


class _MigratingManager(CoreConfigMixin):
    """Exercises the openclawUrl 8089 -> 8088 migration against an in-memory file."""

    def __init__(self, stored: dict):
        self.stored = stored
        self.saved: list[dict] = []

    def load_json_config(self, filename, default_value=None):
        return dict(self.stored)

    def save_json_config(self, filename, data):
        self.stored = dict(data)
        self.saved.append(dict(data))


class _ReadOnlyMigrationManager(CoreConfigMixin):
    """Serves a REAL core_config.json file; fails the test if the read path writes."""

    def __init__(self, config_path: Path):
        self._config_path = config_path

    def get_config_path(self, filename):
        return self._config_path

    def save_json_config(self, filename, data):
        raise AssertionError("get_core_config 是读操作，不允许写盘")


@pytest.mark.unit
def test_get_core_config_never_writes_while_normalizing_a_legacy_port(tmp_path):
    """The read path normalizes 8089 -> 8088 in memory only.

    It runs under to_thread for ~55 async callers now; a write here would race the
    /core_api save handler and could replace the user's just-saved keys.
    """
    path = tmp_path / "core_config.json"
    path.write_text(
        json.dumps({"openclawUrl": "http://127.0.0.1:8089"}), encoding="utf-8"
    )
    manager = _ReadOnlyMigrationManager(path)

    # save_json_config raises if touched -> 用例过即证明读路径没有写盘
    config = manager.get_core_config()

    assert config["OPENCLAW_URL"] == "http://127.0.0.1:8088", "内存归一化没有生效"
    # 前置条件：文件里必须仍是 8089，否则说明根本没走到迁移分支（假绿）
    assert "8089" in path.read_text(encoding="utf-8"), "读路径把迁移落盘了"


@pytest.mark.unit
def test_startup_migration_persists_the_legacy_port_rewrite():
    """Persistence moved to startup, where no concurrent writer exists."""
    manager = _MigratingManager({
        "openclawUrl": "http://127.0.0.1:8089",
        "coreApiKey": "user-key",
    })

    assert manager.migrate_openclaw_url_port() is True
    assert manager.stored["openclawUrl"] == "http://127.0.0.1:8088"
    assert manager.stored["coreApiKey"] == "user-key", "迁移把其他字段弄丢了"


@pytest.mark.unit
def test_startup_migration_is_a_noop_when_the_port_is_already_current():
    manager = _MigratingManager({"openclawUrl": "http://127.0.0.1:8088"})

    assert manager.migrate_openclaw_url_port() is False
    assert manager.saved == [], "端口已是 8088 仍然写了一次盘"


@pytest.mark.unit
def test_legacy_port_rewrite_preserves_host_and_userinfo():
    """The pure helper must keep credentials / IPv6 brackets intact."""
    rewrite = CoreConfigMixin._migrated_openclaw_url

    assert rewrite("http://user:pw@example.test:8089") == "http://user:pw@example.test:8088"
    assert rewrite("http://[::1]:8089") == "http://[::1]:8088"
    # 非 8089、空值、垃圾输入一律不改写
    assert rewrite("http://127.0.0.1:8088") == ""
    assert rewrite("") == ""
    assert rewrite(None) == ""


@pytest.mark.unit
def test_session_construction_resolves_paired_configs_from_one_snapshot():
    """conversation / vision must come from a SINGLE fresh read, never two.

    Two independently offloaded reads let a /core_api save land between them, so the
    client would be built from a torn pair (pre-save conversation + post-save vision).
    Pinned statically: each construction site takes one aget_core_config() and threads
    it into both resolver calls.
    """
    source = (REPO_ROOT / "main_logic" / "core" / "lifecycle.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name not in ("_start_session_start_llm", "_background_prepare_pending_session"):
            continue
        paired = []
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.attr if isinstance(func, ast.Attribute) else None
            if name != "aget_model_api_config":
                continue
            target = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else None
            if target not in ("conversation", "vision"):
                continue
            snapshot = next(
                (kw.value.id for kw in call.keywords
                 if kw.arg == "core_config" and isinstance(kw.value, ast.Name)),
                None,
            )
            paired.append((target, snapshot))

        assert len(paired) == 2, f"{node.name}: 预期 conversation/vision 各一次，实得 {paired}"
        snapshots = {snap for _, snap in paired}
        assert None not in snapshots, f"{node.name}: 有解析没有共用快照，会撕裂 -> {paired}"
        assert len(snapshots) == 1, f"{node.name}: 两者用了不同快照 -> {paired}"
        checked += 1

    assert checked == 2, f"只检查到 {checked} 个构造点，用例的目标函数名可能已过时"


class _FlakyWriteManager(CoreConfigMixin):
    """Fails os.replace-style writes the first N times, like AV holding the target."""

    def __init__(self, stored: dict, fail_times: int):
        self.stored = stored
        self._left = fail_times
        self.attempts = 0

    def load_json_config(self, filename, default_value=None):
        return dict(self.stored)

    def save_json_config(self, filename, data):
        self.attempts += 1
        if self._left > 0:
            self._left -= 1
            raise PermissionError(5, "Access is denied")
        self.stored = dict(data)


@pytest.mark.unit
def test_startup_migration_retries_a_transient_write_failure(monkeypatch):
    """Windows os.replace can lose to an antivirus scan; one attempt must not give up.

    Losing the retry would leave openclawUrl at 8089 for the whole run, and the settings
    form would then post that stale value straight back -- the migration never converges.
    """
    monkeypatch.setattr(core_config_module.time, "sleep", lambda _s: None)
    manager = _FlakyWriteManager({"openclawUrl": "http://127.0.0.1:8089"}, fail_times=2)

    assert manager.migrate_openclaw_url_port() is True
    assert manager.attempts == 3, f"应重试到成功，实际尝试 {manager.attempts} 次"
    assert manager.stored["openclawUrl"] == "http://127.0.0.1:8088"


@pytest.mark.unit
def test_startup_migration_gives_up_after_the_attempt_budget(monkeypatch):
    """Bounded, not infinite: a permanently unwritable config must not hang startup."""
    monkeypatch.setattr(core_config_module.time, "sleep", lambda _s: None)
    manager = _FlakyWriteManager({"openclawUrl": "http://127.0.0.1:8089"}, fail_times=99)

    assert manager.migrate_openclaw_url_port() is False
    assert manager.attempts == core_config_module._OPENCLAW_MIGRATION_ATTEMPTS
    # 预算本身必须小：只断言 attempts==常量 是自指的，把常量调到 50 也照样过，
    # 而那会让一个可选迁移在启动路径上退避好几秒。这里钉死量级。
    assert core_config_module._OPENCLAW_MIGRATION_ATTEMPTS <= 5, "重试预算过大，会拖慢启动"
    worst_case_delay = sum(
        core_config_module._OPENCLAW_MIGRATION_RETRY_DELAY_S * (i + 1)
        for i in range(core_config_module._OPENCLAW_MIGRATION_ATTEMPTS - 1)
    )
    assert worst_case_delay <= 1.0, f"最坏退避累计 {worst_case_delay:.1f}s，启动不该为可选迁移等这么久"
    # 读路径仍会在内存里归一化，所以运行时不受影响
    assert manager.stored["openclawUrl"] == "http://127.0.0.1:8089"


@pytest.mark.unit
def test_save_choke_point_normalizes_a_resubmitted_legacy_port():
    """The settings form echoes the raw file value back; saving it must not re-entrench 8089.

    Startup migration can fail (see above). Without normalizing here, every later save
    would write the legacy port straight back and the migration would never converge.
    """
    source = (
        REPO_ROOT / "main_routers" / "config_router" / "core_config.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # 静态钉住：openclawUrl 的落盘赋值必须经过 _migrated_openclaw_url
    assigns = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(t, ast.Subscript)
            and isinstance(t.slice, ast.Constant)
            and t.slice.value == "openclawUrl"
            for t in node.targets
        )
    ]
    assert assigns, "没找到 openclawUrl 的落盘赋值，用例可能已过时"
    for node in assigns:
        called = {
            c.func.attr for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
        }
        assert "_migrated_openclaw_url" in called, (
            "保存路径直接落了用户提交的 openclawUrl，旧的 8089 会被重新固化"
        )
