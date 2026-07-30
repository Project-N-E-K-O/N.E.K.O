# -*- coding: utf-8 -*-
"""Regression suite for the single locked writer of ``idle_maintenance_state.json``.

Before this change, ten write sites (``_aclear_review_clean`` from four request
handlers plus the review / compress-backup backoff bookkeeping) each did
"read the module-level dict → edit a field → overwrite the whole file" with no
lock at all. Two layers of damage:

  (a) two coroutines each hand a write to ``asyncio.to_thread``, so two worker
      threads call ``os.replace`` on the same path — PermissionError (WinError 5)
      on Windows;
  (b) each writer's ``json.dumps`` happens at a different moment while the
      ``os.replace`` landing order can be reversed, so the content that lands
      last may be the older one, wiping out the backoff counter or the
      ``review_clean`` flag the other writer just recorded.

The fix: ``gates._amutate_maint_state`` is the only write entry point, running
read → mutate → persist as one ``threading.Lock`` critical section inside a
single ``asyncio.to_thread``.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import pathlib
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.memory_server import gates, review

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "app" / "memory_server"
GATES_PATH = PACKAGE_ROOT / "gates.py"


@pytest.fixture
def clean_state():
    """Drop every maintenance entry before and after the test."""
    gates._maint_state.clear()
    yield gates._maint_state
    gates._maint_state.clear()


# ── 1. 串行化：并发写者不会同时落盘、RMW 不丢写 ──────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_writes_never_overlap_on_disk(clean_state):
    """Two writers must never be inside the persist step at the same time."""
    inflight = 0
    max_inflight = 0
    guard = threading.Lock()

    def _slow_persist():
        nonlocal inflight, max_inflight
        with guard:
            inflight += 1
            max_inflight = max(max_inflight, inflight)
        # 真实落盘是 dumps + fsync + replace，用 sleep 把那段窗口放大到可观测。
        time.sleep(0.01)
        with guard:
            inflight -= 1

    def _mutator(state: dict) -> tuple[bool, None]:
        state["n"] = (state.get("n") or 0) + 1
        return True, None

    with patch.object(gates, "_persist_maint_state_locked", _slow_persist):
        await asyncio.gather(*[
            gates._amutate_maint_state("角色", _mutator) for _ in range(8)
        ])

    assert max_inflight == 1, (
        f"同一目标文件出现 {max_inflight} 个并发写者——os.replace 会在 Windows 上"
        "报 PermissionError(WinError 5)"
    )
    assert gates._maint_state["角色"]["n"] == 8


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_read_modify_write_loses_no_update(clean_state):
    """The whole read-modify-write must be atomic, not just the disk write."""
    def _mutator(state: dict) -> tuple[bool, None]:
        # 读 → 慢 → 写：无锁时两个写者都会读到同一个旧值，其中一个的 +1 被吞掉。
        seen = state.get("review_fail_attempts") or 0
        time.sleep(0.005)
        state["review_fail_attempts"] = seen + 1
        return True, None

    with patch.object(gates, "_persist_maint_state_locked", MagicMock()):
        await asyncio.gather(*[
            gates._amutate_maint_state("角色", _mutator) for _ in range(10)
        ])

    assert gates._maint_state["角色"]["review_fail_attempts"] == 10, (
        "RMW 没有整段串行化：有写者读到了别人已经改过之前的旧值，+1 被吞"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_concurrent_real_writes_produce_no_write_errors(clean_state, tmp_path, monkeypatch):
    """Real fsync+replace under concurrency must not raise (WinError 5 regression)."""
    target = tmp_path / "idle_maintenance_state.json"
    monkeypatch.setattr(gates, "_maint_state_path", lambda: str(target))

    errors: list[BaseException] = []
    real_persist = gates._persist_maint_state_locked

    def _watch_persist():
        # _mutate_maint_state_locked 会把写盘异常收在临界区里只告警，所以这里自己
        # 把异常记下来再放行，否则并发 os.replace 失败会被静静吞掉、断言空过。
        try:
            real_persist()
        except BaseException as exc:  # noqa: BLE001 - 记录后原样放行
            errors.append(exc)
            raise

    def _mutator(index: int, state: dict) -> tuple[bool, None]:
        state[f"k{index}"] = index
        return True, None

    with patch.object(gates, "_persist_maint_state_locked", _watch_persist):
        await asyncio.gather(*[
            gates._amutate_maint_state("角色", lambda st, i=i: _mutator(i, st))
            for i in range(20)
        ])

    assert errors == [], f"并发落盘报错（Windows 上典型为 PermissionError）: {errors}"
    landed = json.loads(target.read_text(encoding="utf-8"))
    assert landed == gates._maint_state, "最后落地的内容与内存态不一致（落地顺序反转）"


# ── 2. 不无脑落盘：外层快筛 + mutator 的 dirty 约定，两层各自有牙 ────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_review_clean_does_not_even_switch_threads_when_flag_absent(clean_state):
    """No flag → no worker thread at all, not merely no disk write."""
    clean_state["角色"] = {"review_clean": False}

    real_to_thread = asyncio.to_thread
    with patch.object(gates.asyncio, "to_thread", wraps=real_to_thread) as thread_spy, \
         patch.object(gates, "_mutate_maint_state_locked") as locked_spy, \
         patch.object(gates, "_persist_maint_state_locked", MagicMock()) as persist:
        await gates._aclear_review_clean("角色")

    # 断言"连线程都没切"，而不是只断言"没落盘"：只留 mutator 里的 dirty 返回、
    # 删掉 _aclear_review_clean 的锁外快筛，落盘次数照样是 0，那样的断言会空过。
    assert thread_spy.call_count == 0, "标记本来就是 False，却仍然切了一次 worker 线程"
    assert locked_spy.call_count == 0
    assert persist.call_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_review_clean_does_persist_when_flag_is_set(clean_state):
    """Dual of the skip test: a set flag must actually go through the entry point."""
    clean_state["角色"] = {"review_clean": True}

    real_to_thread = asyncio.to_thread
    with patch.object(gates.asyncio, "to_thread", wraps=real_to_thread) as thread_spy, \
         patch.object(gates, "_persist_maint_state_locked", MagicMock()) as persist:
        await gates._aclear_review_clean("角色")

    assert thread_spy.call_count == 1
    assert persist.call_count == 1
    assert clean_state["角色"]["review_clean"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_compress_backup_failure_does_not_even_switch_threads_when_backoff_empty(
    clean_state,
):
    """Dual of the review_clean skip test — the backoff-clear path needs the same fast filter.

    Its caller ``_on_compress_done`` runs on every successful main-path
    compression and may already be holding the settle lock (/renew, /settle),
    where its own docstring promises not to block. The counter is empty almost
    always, so paying a default-executor hop each time is exactly what the
    lock-free pre-check exists to avoid.
    """
    clean_state["角色"] = {"compress_backup_fail_attempts": 0, "compress_backup_fail_fp": None}

    real_to_thread = asyncio.to_thread
    with patch.object(gates.asyncio, "to_thread", wraps=real_to_thread) as thread_spy, \
         patch.object(gates, "_mutate_maint_state_locked") as locked_spy, \
         patch.object(gates, "_persist_maint_state_locked", MagicMock()) as persist:
        await review._clear_compress_backup_failure("角色")

    # 同样断言"连线程都没切"：只留 mutator 里的 dirty 返回时落盘次数照样是 0，
    # 那种断言抓不到锁外快筛被删掉。
    assert thread_spy.call_count == 0, "退避计数本来就是空的，却仍然切了一次 worker 线程"
    assert locked_spy.call_count == 0
    assert persist.call_count == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_clear_compress_backup_failure_does_persist_when_backoff_present(clean_state):
    """Dual of the skip test: a non-empty backoff must actually go through the entry point."""
    clean_state["角色"] = {"compress_backup_fail_attempts": 2, "compress_backup_fail_fp": "fp"}

    real_to_thread = asyncio.to_thread
    with patch.object(gates.asyncio, "to_thread", wraps=real_to_thread) as thread_spy, \
         patch.object(gates, "_persist_maint_state_locked", MagicMock()) as persist:
        await review._clear_compress_backup_failure("角色")

    assert thread_spy.call_count == 1
    assert persist.call_count == 1
    assert clean_state["角色"]["compress_backup_fail_attempts"] == 0
    assert clean_state["角色"]["compress_backup_fail_fp"] is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mutator_dirty_recheck_skips_the_redundant_second_clear(clean_state):
    """The in-mutator dirty re-check must be pinned independently of the outer fast filter.

    Driven straight through ``_amutate_maint_state`` on purpose: going via
    ``_aclear_review_clean`` would let the fast filter absorb the second call, so
    the assertion would pass even with the re-check deleted.
    """
    clean_state["角色"] = {"review_clean": True}

    with patch.object(gates, "_persist_maint_state_locked", MagicMock()) as persist:
        await gates._amutate_maint_state("角色", gates._mutate_clear_review_clean)
        assert persist.call_count == 1, "标记为 True 时应当落盘一次"
        # 标记已经是 False：mutator 必须返回 dirty=False，锁内不再落盘。
        await gates._amutate_maint_state("角色", gates._mutate_clear_review_clean)

    assert persist.call_count == 1, (
        f"标记本来就是 False 却又落盘了（累计 {persist.call_count} 次），"
        "mutator 的 dirty 复查没生效"
    )
    assert clean_state["角色"]["review_clean"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compress_backup_mutator_dirty_recheck_skips_the_redundant_second_clear(clean_state):
    """Same two-layer split for the compress-backup mutator's own dirty re-check.

    Also driven straight through ``_amutate_maint_state``: routing via
    ``_clear_compress_backup_failure`` lets the lock-free fast filter absorb the
    second call, which would leave the in-mutator guard unpinned.
    """
    clean_state["角色"] = {"compress_backup_fail_attempts": 2, "compress_backup_fail_fp": "fp"}

    with patch.object(gates, "_persist_maint_state_locked", MagicMock()) as persist:
        await gates._amutate_maint_state("角色", review._mutate_clear_compress_backup_failure)
        assert persist.call_count == 1, "退避计数非空时应当落盘一次"
        # 计数已经清空：mutator 必须返回 dirty=False，锁内不再落盘。
        await gates._amutate_maint_state("角色", review._mutate_clear_compress_backup_failure)

    assert persist.call_count == 1, (
        f"退避计数本来就是空的却又落盘了（累计 {persist.call_count} 次），"
        "mutator 的 dirty 复查没生效"
    )
    assert clean_state["角色"]["compress_backup_fail_attempts"] == 0
    assert clean_state["角色"]["compress_backup_fail_fp"] is None


# ── 3. 读侧只读视图 + 单一写入口的结构守卫 ───────────────────────────


@pytest.mark.unit
def test_maint_view_is_read_only(clean_state):
    """_maint_view must hand back a mapping that cannot be written through."""
    clean_state["角色"] = {"review_clean": True}
    view = gates._maint_view("角色")

    assert view["review_clean"] is True
    with pytest.raises(TypeError):
        view["review_clean"] = False
    assert not hasattr(view, "setdefault")
    assert not hasattr(view, "pop")
    # 缺失角色也必须给只读视图，不能返回一个新建的可写 dict 让调用方误以为改得动。
    with pytest.raises(TypeError):
        gates._maint_view("不存在的角色")["x"] = 1


@pytest.mark.unit
def test_maint_view_is_a_snapshot_not_a_live_proxy(clean_state):
    """The view must be detached from the live sub-dict, so it is safe to iterate.

    A proxy over the live entry stays correct for single ``.get()`` reads (those
    are atomic under the GIL) but breaks the moment a caller iterates it, because
    a mutator running in a worker thread can add a key at any point — CPython
    raises "dictionary changed size during iteration". Copying up front costs one
    C-level ``dict()`` over a handful of scalars.
    """
    entry = clean_state.setdefault("角色", {})
    entry["review_clean"] = True
    view = gates._maint_view("角色")

    # 模拟读者还攥着 view 时，worker 线程里的 mutator 往活 sub-dict 里加了新键。
    entry["review_fail_attempts"] = 1
    assert "review_fail_attempts" not in view, "视图跟着活 sub-dict 变了，不是快照"
    assert dict(view) == {"review_clean": True}

    iterator = iter(view)
    next(iterator)
    entry["compress_backup_fail_attempts"] = 1
    assert list(iterator) == [], "迭代期间被并发写打断（活代理会抛 RuntimeError）"


@pytest.mark.unit
def test_gates_exposes_no_second_save_helper():
    """The unlocked ``_asave_maint_state`` must be gone, not merely unused."""
    assert not hasattr(gates, "_asave_maint_state"), (
        "无锁的整体覆盖写 helper 还在——只要它存在，新写点就会重新绕过单一入口"
    )
    from app import memory_server
    assert not hasattr(memory_server, "_asave_maint_state")


def _iter_app_modules():
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        if path == GATES_PATH:
            continue
        yield path


@pytest.mark.unit
def test_no_module_outside_gates_names_the_maint_state_container():
    """No module under app/ other than gates.py may name ``_maint_state``.

    This is deliberately narrower than "no write site bypasses the single entry
    point": an AST scan cannot see ``st = <mutable handle>; st['x'] = 1`` split
    across two statements. What actually seals field writes is ``_maint_view``
    returning a ``MappingProxyType`` over a copy of a scalar-only sub-dict (see
    ``test_maint_view_is_read_only`` and ``test_maint_view_is_a_snapshot_not_a_live_proxy``)
    — a shallow snapshot IS a real seal here.
    This test closes the remaining hole: the only way back to a mutable handle
    is to name the container again, and naming it anywhere outside gates.py
    fails here. Module list is discovered by walking app/, so a new module
    cannot slip past by not being listed.
    """
    offenders: list[str] = []
    for path in _iter_app_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            named = (
                (isinstance(node, ast.Attribute) and node.attr == "_maint_state")
                or (isinstance(node, ast.Name) and node.id == "_maint_state")
                or (isinstance(node, ast.alias)
                    and node.name in ("_maint_state", "_asave_maint_state"))
            )
            if named:
                line = getattr(node, "lineno", "?")
                offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}")

    assert offenders == [], (
        "这些位置重新拿到了维护状态容器本身；请改走 gates._maint_view（读）/ "
        f"gates._amutate_maint_state（写）: {offenders}"
    )


def _dotted_name(node) -> str:
    """Flatten a Name/Attribute expression to its dotted source text ('' if it is neither)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _collect_mutate_defs() -> tuple[dict[str, str], dict[str, str]]:
    """Map every ``_mutate_*`` def under app/memory_server/ to 'path:line' — (sync, async)."""
    sync_defs: dict[str, str] = {}
    async_defs: dict[str, str] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("_mutate_"):
                continue
            where = f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno} {node.name}"
            bucket = async_defs if isinstance(node, ast.AsyncFunctionDef) else sync_defs
            bucket[node.name] = where
    return sync_defs, async_defs


def _collect_mutator_call_sites() -> tuple[list[str], list[str]]:
    """Read the mutator argument off every ``_amutate_maint_state`` call — (names, unresolved)."""
    names: list[str] = []
    unresolved: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _dotted_name(node.func).split(".")[-1] != "_amutate_maint_state":
                continue
            where = f"{path.relative_to(REPO_ROOT).as_posix()}:{node.lineno}"
            arg = node.args[1] if len(node.args) >= 2 else None
            # functools.partial(mutator, ...) 是本包绑定额外入参的唯一写法，剥一层
            # 拿到真正的 mutator。
            if isinstance(arg, ast.Call) and _dotted_name(arg.func).split(".")[-1] == "partial":
                arg = arg.args[0] if arg.args else None
            resolved = _dotted_name(arg).split(".")[-1] if arg is not None else ""
            if resolved:
                names.append(resolved)
            else:
                unresolved.append(where)
    return names, unresolved


@pytest.mark.unit
def test_every_mutator_handed_to_the_write_entry_point_is_synchronous():
    """Mutators must stay ``def``, never ``async def``.

    A synchronous mutator is what makes "await inside the critical section" and
    "grab a second lock inside the critical section" impossible to even write —
    the whole reentrancy argument rests on it.

    Scope comes from the ``_amutate_maint_state`` call sites themselves plus the
    ``_mutate_*`` defs of this package, never from a hand-kept list nor from a
    ``found >= N`` floor: an unrelated subsystem elsewhere under ``app/`` cannot
    fail this file, and the two halves keep each other honest — a call site whose
    argument is not a discovered ``_mutate_*`` def shows up as ``unknown``, so the
    discovery cannot silently stop finding things.
    """
    sync_defs, async_defs = _collect_mutate_defs()
    call_site_names, unresolved = _collect_mutator_call_sites()

    assert unresolved == [], (
        f"这些写入口调用点的 mutator 实参不是具名函数（lambda / 闭包 / 变量）: {unresolved}"
    )
    assert call_site_names, (
        "在 app/memory_server/ 里一个 _amutate_maint_state 调用点都没扫到——"
        "解析规则已经失效，本用例形同虚设"
    )
    unknown = sorted({n for n in call_site_names if n not in sync_defs and n not in async_defs})
    assert unknown == [], (
        f"这些 mutator 实参在包内找不到 _mutate_* 定义，自动发现覆盖不到它们: {unknown}"
    )
    assert async_defs == {}, f"mutator 必须是同步函数: {sorted(async_defs.values())}"


# ── 4. 载入时的整体重绑定必须与 mutator 互斥 ─────────────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_load_rebind_is_mutually_exclusive_with_mutators(tmp_path, monkeypatch, clean_state):
    """``_aload_maint_state`` rebinds the whole map, so it must wait for the lock."""
    monkeypatch.setattr(
        gates, "_maint_state_path", lambda: str(tmp_path / "idle_maintenance_state.json"),
    )

    gates._maint_state_lock.acquire()
    try:
        task = asyncio.create_task(gates._aload_maint_state())
        await asyncio.sleep(0.1)
        assert not task.done(), (
            "有 mutator 持锁时 _aload_maint_state 仍然完成了重绑定——它可能把某个"
            "mutator 正在改的 sub-dict 变成孤儿"
        )
    finally:
        gates._maint_state_lock.release()

    await asyncio.wait_for(task, timeout=5)
    assert gates._maint_state == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mutator_takes_the_sub_dict_inside_the_lock(clean_state, monkeypatch):
    """The sub-dict must be fetched after the lock, or a rebind orphans the mutation.

    Recreates the exact window: a writer is already queued on the lock when
    ``_aload_maint_state`` swaps the whole map. Fetching the sub-dict before the
    lock hands the mutator an entry that has fallen off the live map — the change
    is neither persisted nor visible to later reads.
    """
    old_map = gates._maint_state
    old_map["角色"] = {"review_fail_attempts": 7}
    started = threading.Event()

    def _mutator(state: dict) -> tuple[bool, None]:
        started.set()
        state["review_fail_attempts"] = (state.get("review_fail_attempts") or 0) + 1
        return True, None

    new_map = {"角色": {"review_fail_attempts": 1}}
    with patch.object(gates, "_persist_maint_state_locked", MagicMock()):
        gates._maint_state_lock.acquire()
        try:
            task = asyncio.create_task(gates._amutate_maint_state("角色", _mutator))
            await asyncio.sleep(0.05)
            assert not started.is_set(), "锁被占用时 mutator 就跑了"
            # 直接赋值模拟 _aload_maint_state 的整体重绑定；不走
            # _rebind_maint_state_locked 是因为它要拿本用例正持着的那把锁。
            monkeypatch.setattr(gates, "_maint_state", new_map)
        finally:
            gates._maint_state_lock.release()
        await asyncio.wait_for(task, timeout=5)

    assert new_map["角色"]["review_fail_attempts"] == 2, (
        "改动落在了重绑定前那份 dict 上（孤儿条目），活着的 map 没被更新"
    )
    assert old_map["角色"]["review_fail_attempts"] == 7


# ── 5. 取消路径：to_thread 交出去就取消不掉，锁不能泄漏 ───────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelling_the_awaiting_coroutine_still_releases_the_lock(clean_state):
    """Cancelling the awaiter must not leave the lock held or the write half-done."""
    started = threading.Event()

    def _slow_persist():
        started.set()
        time.sleep(0.2)

    def _mutator(state: dict) -> tuple[bool, None]:
        state["review_clean"] = True
        return True, None

    with patch.object(gates, "_persist_maint_state_locked", _slow_persist):
        task = asyncio.create_task(gates._amutate_maint_state("角色", _mutator))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # to_thread 的 worker 取消不掉，它会跑完并释放锁。等一个后续写者拿到锁，
        # 就同时证明了「锁没泄漏成永久持有」和「不需要 shield」。
        with patch.object(gates, "_persist_maint_state_locked", MagicMock()):
            await asyncio.wait_for(
                gates._amutate_maint_state("角色", _mutator), timeout=5,
            )

    assert gates._maint_state["角色"]["review_clean"] is True
    assert not gates._maint_state_lock.locked()


# ── 6. 「清退避 + 断掉输出耗尽序列」必须落在同一次写里 ────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_generic_failure_lands_both_updates_in_one_write(clean_state):
    """The failure bump and the exhaustion-streak reset must share one snapshot."""
    from app import memory_server

    name = "同一次写"
    clean_state[name] = {
        "review_output_exhaustion_attempts": 2,
        "review_output_exhaustion_min_context_tokens": 1000,
        "review_output_exhaustion_blocked": False,
    }
    fake_mgr = MagicMock()
    fake_mgr.review_history = MagicMock()

    landed: list[dict] = []

    def _record_persist():
        landed.append(copy.deepcopy(gates._maint_state))

    async def _failed(*_a, **_k):
        return ("failed", None)

    fake_mgr.review_history = _failed
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(gates, "_persist_maint_state_locked", _record_persist):
        await memory_server._run_review_in_background(name, [], asyncio.Event())

    assert len(landed) == 1, (
        f"'failed' 分支落了 {len(landed)} 次盘；清输出耗尽序列与 bump 退避被拆开了，"
        "中间会留下「清了但没写」的窗口"
    )
    snapshot = landed[0][name]
    assert snapshot["review_fail_attempts"] == 1
    assert snapshot["review_output_exhaustion_attempts"] == 0
    assert snapshot["review_output_exhaustion_min_context_tokens"] is None
    assert snapshot["review_output_exhaustion_blocked"] is False


# ── 7. 锁内复查：dead-letter 判定的三条分支 ──────────────────────────
# 直接同步调 mutator，不经 _amutate_maint_state：这两个 mutator 的价值全在「判定
# 被搬进临界区」，而判定结果只从返回值出来。经调用点驱动的话，锁外快筛（attempts
# 不足时压根不进 mutator）会把 'proceed' 早退分支整个遮住，断言就空过了。

_BACKOFF_RECHECK_MUTATORS = [
    pytest.param(
        review._mutate_reset_review_fail_backoff,
        "review_fail_attempts",
        "review_fail_fp",
        id="review",
    ),
    pytest.param(
        review._mutate_reset_compress_backup_backoff,
        "compress_backup_fail_attempts",
        "compress_backup_fail_fp",
        id="compress-backup",
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(("mutator", "attempts_key", "fp_key"), _BACKOFF_RECHECK_MUTATORS)
def test_backoff_recheck_proceeds_when_another_writer_already_cleared_the_counter(
    mutator, attempts_key, fp_key,
):
    """Under lock the counter may already be below threshold — that must proceed, not dead-letter.

    The caller only reads the counter outside the lock; by the time the mutator
    runs, a successful review (or a successful compression) may have zeroed it.
    The fingerprint deliberately matches, so dropping this early return turns the
    call into a dead-letter and skips a spawn that should have gone ahead.
    """
    state = {attempts_key: 1, fp_key: "same-fp"}

    assert mutator("same-fp", 3, state) == (False, "proceed")
    # 早退不写任何字段：dirty=False 时 _mutate_maint_state_locked 会跳过落盘，
    # 这里要确认它跳过的确实是一次「什么都没改」的调用。
    assert state == {attempts_key: 1, fp_key: "same-fp"}


@pytest.mark.unit
@pytest.mark.parametrize(("mutator", "attempts_key", "fp_key"), _BACKOFF_RECHECK_MUTATORS)
def test_backoff_recheck_dead_letters_when_the_input_is_unchanged(
    mutator, attempts_key, fp_key,
):
    """Budget exhausted on an unchanged input → dead-letter, and the counter is preserved."""
    state = {attempts_key: 3, fp_key: "same-fp"}

    assert mutator("same-fp", 3, state) == (False, "dead_letter")
    assert state == {attempts_key: 3, fp_key: "same-fp"}, (
        "dead-letter 分支改了状态——退避计数必须原样留着，否则下一轮又白试一次"
    )


@pytest.mark.unit
@pytest.mark.parametrize(("mutator", "attempts_key", "fp_key"), _BACKOFF_RECHECK_MUTATORS)
def test_backoff_recheck_expires_the_budget_when_the_input_changed(
    mutator, attempts_key, fp_key,
):
    """A changed input fingerprint expires the old budget: reset, mark dirty, proceed."""
    state = {attempts_key: 3, fp_key: "old-fp"}

    assert mutator("new-fp", 3, state) == (True, "proceed")
    assert state[attempts_key] == 0
    assert state[fp_key] is None


# ── 8. 落盘失败必须被收在临界区里（承重语义，不是疏忽）─────────────────


@pytest.mark.unit
@pytest.mark.asyncio
async def test_persist_failure_is_swallowed_and_memory_state_still_updated(clean_state):
    """A failed disk write must not propagate out of the write entry point.

    /cache, /process, /renew and /settle all call into this path inside their
    request try-block. Letting a transient write error escape would turn the
    whole round into ``{"status": "error"}``, which in turn stalls cross_server's
    ``last_synced_index`` — far worse than a maintenance flag that is only on
    disk one round late. The in-memory update and the caller's return value must
    survive.
    """
    def _boom():
        raise OSError("disk full")

    def _mutator(state: dict) -> tuple[bool, str]:
        state["review_clean"] = True
        return True, "carried-out"

    fake_logger = MagicMock()
    with patch.object(gates, "_persist_maint_state_locked", _boom), \
         patch.object(gates, "logger", fake_logger):
        value = await gates._amutate_maint_state("角色", _mutator)

    assert value == "carried-out", "落盘失败时 mutator 的返回值没有原样带出来"
    assert clean_state["角色"]["review_clean"] is True, "落盘失败把内存态也回滚了"
    assert fake_logger.warning.call_count == 1, (
        "落盘失败被完全静默——吞异常的前提是至少留一条告警"
    )
    assert not gates._maint_state_lock.locked(), "落盘抛异常后锁没释放"


# ── Gate 6a recovery: the in-lock recheck ───────────────────────────────
# 恢复判定必须在锁外做（token 计数是 async，进不了同步 mutator），所以锁内要复查它依赖的
# 那份输入还在不在。否则另一个后台 review 在那次 await 期间刚 armed 的新断路器会被抹掉，
# 那条已经证明压不动的 context 又被放行重烧一轮。


@pytest.mark.unit
def test_gate6a_recovery_clears_when_the_observed_breaker_is_still_current() -> None:
    """The Gate 6a mutator clears the breaker when nobody armed a newer one."""
    state = {
        'review_output_exhaustion_attempts': 2,
        'review_output_exhaustion_min_context_tokens': 1000,
        'review_output_exhaustion_blocked': True,
    }

    dirty, value = review._mutate_clear_output_exhaustion(
        state, seen_attempts=2, seen_min_tokens=1000
    )

    assert (dirty, value) == (True, None)
    assert state['review_output_exhaustion_attempts'] == 0
    assert state['review_output_exhaustion_min_context_tokens'] is None
    assert state['review_output_exhaustion_blocked'] is False


@pytest.mark.unit
@pytest.mark.parametrize(
    ("newer_state", "what_changed"),
    [
        (
            {
                'review_output_exhaustion_attempts': 2,
                'review_output_exhaustion_min_context_tokens': 4000,
                'review_output_exhaustion_blocked': True,
            },
            "min_context_tokens",
        ),
        (
            {
                'review_output_exhaustion_attempts': 3,
                'review_output_exhaustion_min_context_tokens': 1000,
                'review_output_exhaustion_blocked': True,
            },
            "attempts",
        ),
    ],
    ids=["newer-min-tokens", "newer-attempts"],
)
def test_gate6a_recovery_keeps_a_breaker_armed_by_a_concurrent_writer(
    newer_state: dict, what_changed: str
) -> None:
    """A breaker armed while the async token count ran must survive the recovery clear."""
    before = dict(newer_state)

    dirty, value = review._mutate_clear_output_exhaustion(
        newer_state, seen_attempts=2, seen_min_tokens=1000
    )

    assert (dirty, value) == (False, None), f"{what_changed} 变了就不该清零"
    assert newer_state == before, "复查失败时一个字段都不许动"
