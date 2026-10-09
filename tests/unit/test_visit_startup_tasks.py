# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Visit sweep / startup recovery / shutdown wiring of the main server (design §5 PR-09b)."""

from __future__ import annotations

import ast
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_routers.visit_router import background, runtime

REPO = Path(__file__).resolve().parents[2]
MAIN_SERVER = REPO / "app" / "main_server" / "__init__.py"
BACKGROUND = REPO / "main_routers" / "visit_router" / "background.py"


@pytest.fixture(autouse=True)
def _clean():
    background.cancel_visit_background_tasks()
    yield
    background.cancel_visit_background_tasks()


# ── 静态：只以 create_task 挂到后台，启动链路上不 await ─────────────────


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == name) or (
                isinstance(func, ast.Attribute) and func.attr == name
            ):
                out.append(node)
    return out


def _awaited_names(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            for inner in ast.walk(node.value):
                if isinstance(inner, ast.Name):
                    names.add(inner.id)
                elif isinstance(inner, ast.Attribute):
                    names.add(inner.attr)
    return names


def test_main_server_starts_visit_tasks_without_awaiting_them():
    tree = ast.parse(MAIN_SERVER.read_text(encoding="utf-8"))
    starts = _calls(tree, "start_visit_background_tasks")
    assert len(starts) == 1
    awaited = _awaited_names(tree)
    for name in ("start_visit_background_tasks", "visit_sweep_loop", "visit_spool_recovery",
                 "run_startup_recovery"):
        assert name not in awaited, name


def test_background_module_only_creates_tasks_for_the_loops():
    tree = ast.parse(BACKGROUND.read_text(encoding="utf-8"))
    start = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and n.name == "start_visit_background_tasks")
    # 同步函数：没有 await 的余地
    assert not any(isinstance(n, ast.Await) for n in ast.walk(start))
    created = [ast.unparse(c.args[0]) for c in _calls(start, "create_task")]
    assert created == ["runtime.visit_sweep_loop()", "_recover_in_background()"]


def test_visit_shutdown_runs_first_in_on_shutdown():
    tree = ast.parse(MAIN_SERVER.read_text(encoding="utf-8"))
    on_shutdown = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "on_shutdown")
    steps = [c for c in _calls(on_shutdown, "_run_shutdown_step") if c.args]
    first_args = [ast.unparse(c.args[0]) for c in sorted(steps, key=lambda c: (c.lineno, c.col_offset))]
    # 串门收口是第一个关机步骤（在语音身份清理之前），且有自己的期限
    assert first_args[:2] == ["stop_visit_background_tasks", "close_voice_identity_runtime"]
    visit_step = next(c for c in steps if ast.unparse(c.args[0]) == "stop_visit_background_tasks")
    deadline = next(k for k in visit_step.keywords if k.arg == "deadline_monotonic")
    assert "SHUTDOWN_STEP_BUDGET_S" in ast.unparse(deadline.value)
    assert background.SHUTDOWN_STEP_BUDGET_S > background.VISIT_SHUTDOWN_BUDGET_S + background._STOP_ALL_SLACK_S


# ── 行为 ──────────────────────────────────────────────────────────────


async def test_start_returns_at_once_and_injects_the_runtime_callbacks(monkeypatch, tmp_path):
    from main_logic.visit import recovery
    from main_routers.visit_router import debrief, transcript_upload

    captured: dict = {}
    gate = asyncio.Event()

    async def fake_recovery(render_chips, upload_transcript=None, **kw):
        captured.update(kw, render_chips=render_chips, upload_transcript=upload_transcript)
        await gate.wait()
        return SimpleNamespace(crashed=[], chips=[], swept=0)

    async def no_family():
        return ("妈妈",)

    monkeypatch.setattr(recovery, "visit_spool_recovery", fake_recovery)
    monkeypatch.setattr(background, "_family_names", no_family)
    monkeypatch.setattr(runtime, "runtime_deps", lambda: SimpleNamespace(config_dir=lambda: tmp_path))
    started = time.monotonic()
    background.start_visit_background_tasks()
    assert time.monotonic() - started < 0.1          # 不等补录
    sweep, rec = background._sweep_task, background._recovery_task
    background.start_visit_background_tasks()         # 幂等：不再起第二份
    assert background._sweep_task is sweep and background._recovery_task is rec
    for _ in range(20):
        await asyncio.sleep(0)
    assert captured["render_chips"] is debrief.render_chips
    assert captured["upload_transcript"] is transcript_upload.upload_visit_transcript
    assert captured["is_live"] is runtime.is_visit_live
    assert captured["spawn_background"] is runtime.spawn_visit_background
    assert captured["retry_later"] is transcript_upload.schedule_visit_retry
    assert captured["submit_report"] is transcript_upload.submit_queued_report
    assert captured["config_dir"] == tmp_path and captured["family_names"] == ("妈妈",)
    assert callable(captured["summary_llm"]) and callable(captured["void_pending"])
    gate.set()
    assert await rec is None
    assert not sweep.done()


async def test_recovery_failure_is_logged_not_raised(monkeypatch, caplog):
    async def broken():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(background, "run_startup_recovery", broken)
    background.start_visit_background_tasks()
    assert await background._recovery_task is None
    assert "visit recovery failed" in caplog.text


async def test_shutdown_is_bounded_even_when_stop_all_hangs(monkeypatch):
    monkeypatch.setattr(background, "VISIT_SHUTDOWN_BUDGET_S", 0.2)
    stopped = asyncio.Event()

    async def hanging(reason="shutdown"):
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()

    async def slow_recovery():
        await asyncio.sleep(60)

    monkeypatch.setattr(runtime, "stop_all", hanging)
    monkeypatch.setattr(background, "run_startup_recovery", slow_recovery)
    background.start_visit_background_tasks()
    sweep, rec = background._sweep_task, background._recovery_task
    started = time.monotonic()
    await background.stop_visit_background_tasks()
    assert time.monotonic() - started < 1.0          # 到点不挡后面的关机钩子
    assert stopped.is_set()
    await asyncio.sleep(0)
    assert sweep.cancelled() or sweep.done()
    assert rec.cancelled() or rec.done()


async def test_shutdown_without_any_visit_calls_no_runtime_shutdown(monkeypatch):
    runtime._reset_for_tests()
    calls = []
    monkeypatch.setattr(runtime.VisitRuntime, "shutdown", lambda self: calls.append(self))
    await background.stop_visit_background_tasks()
    assert calls == []


# ── 登出 / 切换社区账号 ────────────────────────────────────────────────


class _FakeRt:
    def __init__(self, sealed_after: float):
        self.sealed_after = sealed_after
        self.finalized: list[str] = []

    def request_finalize(self, reason):
        self.finalized.append(reason)
        return True

    async def wait_upload_sealed(self, timeout):
        await asyncio.sleep(min(self.sealed_after, timeout))
        return self.sealed_after <= timeout


async def test_account_change_ends_every_visit_and_waits_for_the_seal(monkeypatch):
    a, b = _FakeRt(0.05), _FakeRt(0.1)
    monkeypatch.setattr(runtime, "_runtimes", {"A": a, "B": b})
    started = time.monotonic()
    assert await runtime.end_visits_for_account_change(timeout=1.0) == 2
    assert a.finalized == ["route_end"] and b.finalized == ["route_end"]
    assert time.monotonic() - started >= 0.09         # 等到最慢那场封存完


async def test_account_change_wait_is_bounded(monkeypatch, caplog):
    slow = _FakeRt(10.0)
    monkeypatch.setattr(runtime, "_runtimes", {"A": slow})
    started = time.monotonic()
    await runtime.end_visits_for_account_change(timeout=0.1)
    assert time.monotonic() - started < 0.5
    assert "before every upload was sealed" in caplog.text


async def test_real_visit_is_sealed_before_the_account_change_returns(tmp_path, monkeypatch):
    from main_routers.visit_router import transport_ws
    from tests.unit.visit_runtime_harness import bring_up, teardown
    from utils import visit_route_state

    runtime._reset_for_tests()
    runtime.register_visit_route_kind()
    host, guest, wire, clock, _wall = await bring_up(tmp_path, monkeypatch)
    try:
        hrt = host.rt

        async def advance():
            while not (hrt.exit_task and hrt.exit_task.done()):
                clock.advance(0.5)
                await asyncio.sleep(0.01)

        mover = asyncio.ensure_future(advance())
        assert await runtime.end_visits_for_account_change(timeout=20.0) >= 1
        assert hrt.finalize_reason == "route_end"
        assert hrt._sealing is not None and hrt._sealing.done() and hrt.sealed_doc is not None
        mover.cancel()
    finally:
        await teardown(host, guest, wire=wire, clock=clock)
        runtime._reset_for_tests()
        transport_ws._reset_for_tests()
        visit_route_state._reset_for_tests()


async def test_account_change_fences_admission_and_ends_live_visits(monkeypatch):
    ended = []

    async def spy(timeout=None):
        ended.append(True)
        return 1

    monkeypatch.setattr(runtime, "end_visits_for_account_change", spy)
    monkeypatch.setattr(runtime, "_runtimes", {"A": object()})
    async with runtime.account_change():
        assert ended == [True]
        # 换账号期间建房一律拒（占位之前就拒，不动槽位）
        with pytest.raises(runtime.VisitRefused) as exc:
            await runtime.start_visit("A", "host")
        assert exc.value.body == {"code": "VISIT_E_BUSY", "reason": "account_change"}
    assert runtime._account_changes == 0


async def test_start_awaiting_its_account_lookup_is_refused_after_an_account_change(monkeypatch):
    from main_routers.visit_router import persona
    from main_routers.visit_router.persona import PersonaGate
    from utils import visit_route_state

    runtime._reset_for_tests()
    visit_route_state._reset_for_tests()
    gate = asyncio.Event()

    async def persona_ok(name):
        return PersonaGate(ok=True, state="ok", character_uid="uid-a", text="猫娘")

    async def slow_account():
        await gate.wait()
        return "acct"

    class Host:
        lanlan_name = "A"

        def precondition_failure(self):
            return None

        def is_current(self):
            return True

    monkeypatch.setattr(persona, "persona_gate", persona_ok)
    monkeypatch.setattr(runtime, "_local_account", slow_account)
    starting = asyncio.ensure_future(runtime.start_visit("A", "host", host=Host(),
                                                         deps=SimpleNamespace(config_dir=lambda: Path("."))))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    async with runtime.account_change():
        gate.set()
        with pytest.raises(runtime.VisitRefused) as exc:
            pytest.fail(f"admitted during an account change: {await starting!r}")
    assert exc.value.body["reason"] == "account_change"
    assert visit_route_state.get_visit_route_state("A") is None      # 占位已放掉
    runtime._reset_for_tests()


async def test_oauth_fence_decides_relogin_with_the_fence_up(monkeypatch):
    from main_routers import community_oauth
    from main_routers.visit_router import accounts

    ended = []
    fence_up_when_checked = []

    async def spy(timeout=None):
        ended.append(True)
        return 1

    async def current():
        fence_up_when_checked.append(runtime._account_changes > 0)
        return "acct-1"

    monkeypatch.setattr(runtime, "_runtimes", {"A": object()})
    monkeypatch.setattr(runtime, "end_visits_for_account_change", spy)
    monkeypatch.setattr(accounts, "local_account", current)
    async with community_oauth.visit_account_change():               # 登出：收尾
        assert runtime._account_changes == 1
    async with community_oauth.visit_account_change("acct-2"):       # 换账号：收尾
        pass
    async with community_oauth.visit_account_change("acct-1"):       # 同一账号重登：不收尾，但闸照样立着
        assert runtime._account_changes == 1
    assert ended == [True, True]
    # 账号比对在闸立起之后才做：比对期间不会有新串门被准入
    assert fence_up_when_checked == [True, True]


class _SealStub:
    """Just the fields ``wait_upload_sealed`` reads."""

    def __init__(self, *, sealing=None, exited=True, deferred=False):
        self._sealing = sealing
        self._journal_opening = None
        self._files_deferred = deferred
        self._deferred_files_done = False
        self._terminated = False
        done = asyncio.get_running_loop().create_future()
        if exited:
            done.set_result(None)
        self._exit_task = done

    def _header_pending(self):
        return runtime.VisitRuntime._header_pending(self)

    def seal_pending(self):
        return runtime.VisitRuntime.seal_pending(self)


async def test_seal_wait_keeps_waiting_for_a_seal_finished_in_the_background():
    sealing = asyncio.get_running_loop().create_future()
    stub = _SealStub(sealing=sealing, exited=True)
    asyncio.get_running_loop().call_later(0.3, sealing.set_result, {"ok": True})
    started = time.monotonic()
    assert await runtime.VisitRuntime.wait_upload_sealed(stub, 2.0) is True
    assert time.monotonic() - started >= 0.25          # 退出流程先结束不算：等封存真的落盘


async def test_seal_wait_on_a_deferred_chain_times_out_instead_of_lying():
    stub = _SealStub(sealing=None, exited=True, deferred=True)
    assert await runtime.VisitRuntime.wait_upload_sealed(stub, 0.3) is False


async def test_seal_wait_returns_at_once_when_nothing_is_left_to_seal():
    stub = _SealStub(sealing=None, exited=True)
    started = time.monotonic()
    assert await runtime.VisitRuntime.wait_upload_sealed(stub, 5.0) is True
    assert time.monotonic() - started < 0.1


def _body_of(source: str, signature: str) -> str:
    return source.split(signature, 1)[1].split(chr(10) + "@", 1)[0]


def test_every_credential_change_runs_inside_the_visit_fence():
    oauth = (REPO / "main_routers" / "community_oauth.py").read_text(encoding="utf-8")
    logout = _body_of(oauth, "async def oauth_logout_endpoint(")
    assert "async with visit_account_change():" in logout and "return await _oauth_logout()" in logout
    callback = _body_of(oauth, "async def _handle_oauth_callback(")
    # 先拿登录锁、确认这次尝试没被新的 /oauth/start 顶掉，才立闸收尾串门，再写凭证
    lock = callback.index("async with _oauth_start_lock:")
    recheck = callback.index("if not await _oauth_attempt_still_current(expected_state):", lock)
    fence = callback.index("async with visit_account_change(local_user_id):", lock)
    assert lock < recheck < fence < callback.index("_persist_oauth_credentials", lock)
    card = (REPO / "main_routers" / "card_drop_router.py").read_text(encoding="utf-8")
    sync = _body_of(card, "async def sync_session_endpoint(")
    assert sync.index("async with community_oauth.visit_account_change():") < sync.index("_clear_auth)")
    assert sync.index("community_oauth.visit_account_change(_normalize_local_user_id(") < sync.index(
        "await _store_session(")
    logout2 = _body_of(card, "async def logout_endpoint(")
    assert logout2.index("async with community_oauth.visit_account_change():") < logout2.index("_clear_auth)")


# ── 评审第 2 轮 ───────────────────────────────────────────────────────


async def test_startup_rollback_also_cancels_upload_retry_workers():
    from main_routers.visit_router import transcript_upload

    worker = asyncio.ensure_future(asyncio.sleep(60))
    transcript_upload._workers["v" * 22] = worker
    try:
        background.cancel_visit_background_tasks()
        await asyncio.sleep(0)
        assert worker.cancelled()
    finally:
        transcript_upload._workers.pop("v" * 22, None)


async def test_shutdown_stops_the_recovery_before_stop_all(monkeypatch):
    seen = []

    async def slow_recovery():
        await asyncio.sleep(60)

    async def stop_all(reason="shutdown"):
        seen.append(rec.done())

    monkeypatch.setattr(background, "run_startup_recovery", slow_recovery)
    monkeypatch.setattr(runtime, "stop_all", stop_all)
    background.start_visit_background_tasks()
    rec = background._recovery_task
    await asyncio.sleep(0)
    await background.stop_visit_background_tasks()
    # 补录先停：它不会在 stop_all 取后台任务快照之后再派生写入
    assert seen == [True]


def test_sync_session_clear_rechecks_the_account_inside_the_fence():
    card = (REPO / "main_routers" / "card_drop_router.py").read_text(encoding="utf-8")
    sync = _body_of(card, "async def sync_session_endpoint(")
    fenced = sync.split("async with community_oauth.visit_account_change():", 1)[1].split("cleared = await", 1)[0]
    # 等收尾期间别的登录可能已换账号：闸内、清除之前再核对一次令牌
    assert "_access_token" in fenced and "local_session_mismatch" in fenced


async def test_account_changes_run_one_at_a_time(monkeypatch):
    # 前一次变更还在等封存（那场可能已注销）：后一次不能看着空表抢先改凭证
    order: list[str] = []
    release = asyncio.Event()

    async def slow_end(timeout=None):
        order.append("first:end-start")
        monkeypatch.setattr(runtime, "_runtimes", {})          # 那场已从登记表注销
        await release.wait()
        order.append("first:end-done")
        return 1

    monkeypatch.setattr(runtime, "_runtimes", {"A": object()})
    monkeypatch.setattr(runtime, "end_visits_for_account_change", slow_end)

    async def first():
        async with runtime.account_change():
            order.append("first:write")

    async def second():
        async with runtime.account_change():
            order.append("second:write")

    a = asyncio.ensure_future(first())
    await asyncio.sleep(0.01)
    b = asyncio.ensure_future(second())
    await asyncio.sleep(0.05)
    assert "second:write" not in order and runtime._account_changes == 2   # 后一次排队时闸照样立着
    release.set()
    await asyncio.gather(a, b)
    assert order == ["first:end-start", "first:end-done", "first:write", "second:write"]



async def test_stale_oauth_attempt_is_recognized(monkeypatch):
    # 被新的 /oauth/start 顶掉的回调：不为它结束在飞的串门（凭证写入本来也会拒）
    from main_routers import community_oauth

    pending = {"state": "new-attempt"}
    monkeypatch.setattr(community_oauth, "_load_oauth_pending", lambda: (Path("p"), pending))
    assert await community_oauth._oauth_attempt_still_current("new-attempt") is True
    assert await community_oauth._oauth_attempt_still_current("old-attempt") is False
    monkeypatch.setattr(community_oauth, "_load_oauth_pending", lambda: (None, None))
    assert await community_oauth._oauth_attempt_still_current("new-attempt") is False


# ── 评审第 5 轮 ───────────────────────────────────────────────────────


async def test_account_change_waits_for_a_seal_of_a_recently_unregistered_visit(monkeypatch):
    # 退出流程超过封存期限、封存还在后台写：这场已从 _runtimes 注销，但账号变更仍要等它
    sealing = asyncio.get_running_loop().create_future()
    stub = _SealStub(sealing=sealing, exited=True)
    stub.wait_upload_sealed = lambda timeout: runtime.VisitRuntime.wait_upload_sealed(stub, timeout)
    monkeypatch.setattr(runtime, "_runtimes", {})
    monkeypatch.setattr(runtime, "_recent", {"v" * 22: stub})
    asyncio.get_running_loop().call_later(0.2, sealing.set_result, {"ok": True})
    started = time.monotonic()
    async with runtime.account_change(timeout=2.0):
        assert sealing.done()                      # 改凭证之前已封存
    assert time.monotonic() - started >= 0.15


async def test_shutdown_cancels_upload_retry_workers(monkeypatch):
    from main_routers.visit_router import transcript_upload

    async def nothing(reason="shutdown"):
        return None

    monkeypatch.setattr(runtime, "stop_all", nothing)
    worker = asyncio.ensure_future(asyncio.sleep(60))
    transcript_upload._workers["w" * 22] = worker
    try:
        await background.stop_visit_background_tasks()
        assert worker.cancelled()
    finally:
        transcript_upload._workers.pop("w" * 22, None)


# ── 自动 Review 第 1 轮 ───────────────────────────────────────────────


async def test_a_slow_runtime_shutdown_does_not_cut_off_stop_alls_cleanup(monkeypatch):
    # 某场 shutdown() 用满预算：外层不能先超时把 stop_all 后半段（取消后台写入）截掉
    budget = 0.3
    monkeypatch.setattr(runtime, "VISIT_SHUTDOWN_BUDGET_S", budget)
    monkeypatch.setattr(background, "VISIT_SHUTDOWN_BUDGET_S", budget)
    runtime._reset_for_tests()

    class SlowRuntime:
        visit_id = "s" * 22
        _room_cancel_task = None

        async def shutdown(self):
            await asyncio.sleep(budget)

    background_write = asyncio.ensure_future(asyncio.sleep(60))
    monkeypatch.setattr(runtime, "_runtimes", {"A": SlowRuntime()})
    runtime._visit_bg_tasks["uid-a"] = {background_write}
    try:
        await background.stop_visit_background_tasks()
        await asyncio.sleep(0)
        assert background_write.cancelled()
    finally:
        runtime._visit_bg_tasks.clear()
        runtime._reset_for_tests()


async def test_startup_rollback_cancels_background_writes_recovery_spawned():
    runtime._reset_for_tests()
    write = asyncio.ensure_future(asyncio.sleep(60))
    runtime._visit_bg_tasks["uid-a"] = {write}
    try:
        background.cancel_visit_background_tasks()
        await asyncio.sleep(0)
        assert write.cancelled()
    finally:
        runtime._visit_bg_tasks.clear()
