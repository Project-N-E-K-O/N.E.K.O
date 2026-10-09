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
    assert "VISIT_SHUTDOWN_BUDGET_S" in ast.unparse(deadline.value)


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
    await rec
    assert not sweep.done()


async def test_recovery_failure_is_logged_not_raised(monkeypatch, caplog):
    async def broken():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(background, "run_startup_recovery", broken)
    background.start_visit_background_tasks()
    await background._recovery_task
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


async def test_oauth_hook_is_a_no_op_without_live_visits(monkeypatch):
    from main_routers import community_oauth

    called = []

    async def spy(*_a, **_k):
        called.append(True)
        return 0

    monkeypatch.setattr(runtime, "_runtimes", {})
    monkeypatch.setattr(runtime, "end_visits_for_account_change", spy)
    await community_oauth._end_visits_before_account_change()
    await community_oauth._end_visits_before_account_change("someone")
    assert called == []


async def test_oauth_hook_ends_visits_on_logout_and_on_another_account(monkeypatch):
    from main_routers import community_oauth
    from main_routers.visit_router import accounts

    called = []

    async def spy(*_a, **_k):
        called.append(True)
        return 1

    async def current():
        return "acct-1"

    monkeypatch.setattr(runtime, "_runtimes", {"A": object()})
    monkeypatch.setattr(runtime, "end_visits_for_account_change", spy)
    monkeypatch.setattr(accounts, "local_account", current)
    await community_oauth._end_visits_before_account_change()             # 登出
    await community_oauth._end_visits_before_account_change("acct-2")     # 换账号
    await community_oauth._end_visits_before_account_change("acct-1")     # 同一账号重登：不动
    assert called == [True, True]


def test_oauth_entry_points_end_visits_before_touching_the_login_state():
    source = (REPO / "main_routers" / "community_oauth.py").read_text(encoding="utf-8")
    logout = source.split("async def oauth_logout_endpoint(", 1)[1].split("\n@", 1)[0]
    assert logout.index("_end_visits_before_account_change()") < logout.index("_revoke_tokens_best_effort")
    assert logout.index("_end_visits_before_account_change()") < logout.index("C._clear_auth")
    callback = source.split("async def _handle_oauth_callback(", 1)[1].split("\n@", 1)[0]
    assert callback.index("_end_visits_before_account_change(local_user_id)") < callback.index(
        "_persist_oauth_credentials")
