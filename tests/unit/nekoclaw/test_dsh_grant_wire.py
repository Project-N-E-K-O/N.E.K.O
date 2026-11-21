"""Run a local model fixture through the pinned, real dsh ACP and pre-execute hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nekoclaw.dsh import profile
from nekoclaw.dsh.driver import DshRuntime
from nekoclaw.host.permissions import PermissionGate, PermissionStore
from nekoclaw.runtime.host import HostCallbacks
from nekoclaw.runtime.protocol import ModelConfig
from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_tool_call
from tests.unit.nekoclaw.fakes import Recorder, make_spec


async def test_pinned_dsh_native_denial_precedes_host_ask(tmp_path, monkeypatch):
    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    native = """  ctx.on('tools/pre-execute', (exec, next) =>
        exec.name === 'write' ? {kind:'deny', reason:'fixture native denial'} : next())
"""
    monkeypatch.setattr(profile, "GUARD_SOURCE", profile.GUARD_SOURCE.replace("  process.stderr.write", native + "  process.stderr.write"))
    calls = []

    async def approve(session_id, payload):
        calls.append(payload["tool"])
        return {"allow": True}

    events = Recorder()
    runtime = DshRuntime(
        events, HostCallbacks(decide_permission=approve),
        launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
        environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )
    requested = tmp_path / "input.txt"
    requested.write_text("wire-test-read", encoding="utf-8")
    destination = tmp_path / "native-denied.txt"
    responses = iter([
        [sse_tool_call("read", {"file_path": str(requested)}), sse_finish("tool_calls")],
        [sse_tool_call("write", {"file_path": str(destination), "content": "denied"}, "call_2"), sse_finish("tool_calls")],
        [sse_text("Done"), sse_finish()],
    ])
    try:
        with FakeOpenAIServer(lambda _: next(responses)) as server:
            model = ModelConfig(model="local-fixture", base_url=server.base_url, api_key="local",
                                auto_extra_body=False)
            spec = make_spec(tmp_path, model=model)
            await runtime.start(spec)
            opened = await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=60)
            assert opened["type"] == "started", opened
            await runtime.prompt(spec.session_id, "Read the fixture then test the denied tool.")
            await events.wait_ended(spec.session_id, timeout=40)
            assert calls == ["read"]
            assert len(server.requests) == 3
            assert "wire-test-read" in str(server.requests[1]["messages"][-1])
            assert "fixture native denial" in str(server.requests[2]["messages"][-1])
            assert not destination.exists()
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize(("tool", "human", "allow"), [
    ("todo_write", True, True), ("todo_write", True, False),
    ("create_goal", False, True), ("create_goal", True, True),
])
async def test_pinned_task_control_is_reserved_before_execution(tmp_path, tool, human, allow):
    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    calls = []

    original = [{"content": "existing item", "status": "pending"}]
    seeded = []
    before = []

    def persisted_todos():
        cache, = (tmp_path / "dsh" / "storages" / "session_projcache" / "sessions").glob("*.json")
        rows = json.loads(cache.read_text(encoding="utf-8"))["record"]["rows"]
        return rows["todos"]["val"]

    async def begin(sid, payload):
        if tool == "todo_write" and not seeded:
            seeded.append(payload["call_id"])
            return {"allow": True}
        calls.append((sid, payload))
        if tool == "todo_write":
            before.extend((e["tool_call_id"], e["is_error"], e["text"]) for e in events.of("tool_end"))
        return {"allow": allow}

    events = Recorder()
    runtime = DshRuntime(
        events, HostCallbacks(task_control_begin=begin),
        launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
        environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )
    arguments = {"objective": "local fixture"} if tool == "create_goal" else {
        "todos": [{"content": "local fixture", "status": "pending"}],
    }
    # dsh clears its todo projection on turn/start. Seed and update it in the same turn.
    seed_replies = [
        [sse_tool_call("todo_write", {"todos": original}, "seed_1"), sse_finish("tool_calls")],
    ] if tool == "todo_write" else []
    replies = iter([
        *seed_replies,
        [sse_tool_call(tool, arguments),
         sse_finish("tool_calls")],
        [sse_text("Done"), sse_finish()],
    ])
    try:
        with FakeOpenAIServer(lambda _: next(replies)) as server:
            spec = make_spec(tmp_path, model=ModelConfig(
                model="local-fixture", base_url=server.base_url, api_key="local", auto_extra_body=False,
            ))
            await runtime.start(spec)
            opened = await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=60)
            assert opened["type"] == "started", opened
            await runtime.prompt(spec.session_id, "Update the local state.", human=human)
            await events.wait_ended(spec.session_id, timeout=40)
            end = events.of("tool_end")[-1]
            expected = {"tool": tool, "call_id": end["tool_call_id"]}
            if not human:
                expected["refused"] = True
            assert calls == [(spec.session_id, expected)]
            assert end["is_error"] == (not allow or not human), end
    finally:
        await runtime.shutdown()
    if tool == "todo_write":
        (seed_id, is_error, text), = before
        assert [seed_id] == seeded and not is_error
        assert "1 pending" in text
        assert persisted_todos() == (arguments["todos"] if allow else original)


async def test_pinned_dsh_completes_its_automatic_goal_round(tmp_path):
    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    events = Recorder()
    admissions = []
    goal = {}

    async def begin(sid, payload):
        session = runtime._sessions[sid]
        admissions.append((payload["tool"], session.in_turn, session.tool_calls[payload["call_id"]][1]))
        return {"allow": True}

    runtime = DshRuntime(
        events, HostCallbacks(task_control_begin=begin),
        launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
        environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )

    def respond(request):
        number = len(server.requests)
        if number == 1:
            return [sse_tool_call("create_goal", {"objective": "local fixture", "max_goal_rounds": 2}), sse_finish("tool_calls")]
        if number == 2:
            goal.update(json.loads(request["messages"][-1]["content"])["goal"])
        if number == 3:
            args = {"action": "complete", "goal_id": goal["id"], "revision": goal["revision"]}
            return [sse_tool_call("update_goal", args, "finish_goal"), sse_finish("tool_calls")]
        return [sse_text("Done"), sse_finish()]

    try:
        with FakeOpenAIServer(respond) as server:
            spec = make_spec(tmp_path, model=ModelConfig(
                model="local-fixture", base_url=server.base_url, api_key="local", auto_extra_body=False,
            ))
            await runtime.start(spec)
            await events.wait_for(lambda e: e["type"] == "started", timeout=60)
            await runtime.prompt(spec.session_id, "Set a goal and complete it in the automatic round.")
            ended = await events.wait_for(
                lambda e: e["type"] == "tool_end" and e["tool_name"] == "update_goal", timeout=30,
            )
            assert not ended["is_error"], ended
            assert admissions[-1] == ("update_goal", False, {
                "action": "complete", "goal_id": goal["id"], "revision": goal["revision"],
            })
            assert json.loads(ended["text"])["goal"]["phase"] == "complete"
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize(("configured", "expected_asks"), [(None, 0), ("always_ask", 2), ("allow", 0)])
async def test_pinned_dsh_workdir_reads_use_auto_or_the_explicit_rule(tmp_path, monkeypatch, configured, expected_asks):
    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    store = PermissionStore(tmp_path / "permissions.json")
    if configured:
        await store.set_mode("workdir_files", configured)

    questions = []

    async def once(session_id, question):
        questions.append((question.category, question.options))
        return "once"

    gate = PermissionGate(store, once)

    async def decide(session_id, payload):
        assert payload["category"] == "workdir_files"
        return (await gate.check_native(session_id, payload["category"], detail=payload)).to_json()

    events = Recorder()
    runtime = DshRuntime(
        events, HostCallbacks(decide_permission=decide),
        launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
        environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"},
    )
    updates, requests = [], []
    on_update, on_request = runtime._on_notification, runtime._on_request

    def record_update(process, method, params):
        update = params.get("update", {})
        if update.get("sessionUpdate") == "tool_call":
            updates.append(update)
        return on_update(process, method, params)

    async def record_request(process, method, params):
        if method == "session/request_permission":
            requests.append(params["toolCall"])
        return await on_request(process, method, params)

    monkeypatch.setattr(runtime, "_on_notification", record_update)
    monkeypatch.setattr(runtime, "_on_request", record_request)
    try:
        fixture = iter([
            [sse_tool_call("read", {"file_path": "note.txt"}, call_id), sse_finish("tool_calls")]
            for call_id in ("call_1", "call_2")
        ] + [[sse_text("Done"), sse_finish()]])
        with FakeOpenAIServer(lambda _: next(fixture)) as server:
            model = ModelConfig(model="local-fixture", base_url=server.base_url, api_key="local", auto_extra_body=False)
            spec = make_spec(tmp_path, model=model)
            Path(spec.workdir).mkdir(parents=True, exist_ok=True)
            (Path(spec.workdir) / "note.txt").write_text("local fixture", encoding="utf-8")
            await runtime.start(spec)
            opened = await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=60)
            assert opened["type"] == "started", opened
            await runtime.prompt(spec.session_id, "Read the fixture twice.")
            await events.wait_ended(spec.session_id, timeout=40)
            assert all("local fixture" in str(turn["messages"][-1]) for turn in server.requests[1:])
            assert len(server.requests) == 3
        # This release sends a name and raw arguments, with no registered source or verified path.
        assert len(updates) == len(requests) == 2
        for update, request in zip(updates, requests, strict=True):
            assert request == {"toolCallId": update["toolCallId"]}
            assert set(update) == {"sessionUpdate", "toolCallId", "title", "kind", "status", "rawInput"}
            assert update["title"] == "read" and update["kind"] == "other"
        assert questions == [("workdir_files", ("once", "deny"))] * expected_asks
    finally:
        await runtime.shutdown()
