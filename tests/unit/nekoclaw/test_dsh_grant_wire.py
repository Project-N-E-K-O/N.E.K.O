"""Run a local model fixture through the pinned, real dsh ACP and pre-execute hooks."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nekoclaw.dsh import profile
from nekoclaw.dsh.driver import DshRuntime
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
