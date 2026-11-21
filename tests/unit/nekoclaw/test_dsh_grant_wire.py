"""Run a local model fixture through the pinned, real dsh ACP and pre-execute hooks."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nekoclaw.dsh import profile
from nekoclaw.dsh.driver import DshRuntime
from nekoclaw.runtime.host import HostCallbacks
from nekoclaw.runtime.protocol import ModelConfig
from tests.unit.nekoclaw.fake_openai_server import FakeOpenAIServer, sse_finish, sse_text, sse_tool_call
from tests.unit.nekoclaw.fakes import Recorder, make_spec


@pytest.mark.parametrize(("managed", "receive"), [(False, True), (True, True), (True, False)])
async def test_pinned_dsh_native_denial_precedes_host_ask(tmp_path, monkeypatch, managed, receive):
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

    phases = []

    async def action(session_id, payload):
        phases.append(payload)
        if payload["phase"] == "begin":
            assert await approve(session_id, payload)
            return {"allow": True, "receipt": "fixture-receipt"}
        return {"allow": receive if payload["phase"] == "finish" else True}

    events = Recorder()
    runtime = DshRuntime(
        events, HostCallbacks(decide_permission=approve, native_execution_action=action if managed else None),
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
            if managed:
                checked = [p for p in phases if p["phase"] != "release"]
                assert [(p["phase"], p.get("status")) for p in checked] == [
                    ("begin", None), ("check", None), ("finish", "completed"), ("native_failure", None),
                ]
                assert phases[1]["receipt"] == phases[2]["receipt"] == "fixture-receipt"
                assert checked[3] == {"phase": "native_failure", "tool": "write"}
            assert len(server.requests) == 3
            assert ("wire-test-read" in str(server.requests[1]["messages"][-1])) == receive
            if not receive:
                assert "could not be delivered" in str(server.requests[1]["messages"][-1])
            assert "fixture native denial" in str(server.requests[2]["messages"][-1])
            assert not destination.exists()
    finally:
        await runtime.shutdown()


async def test_pinned_dsh_reuses_source_when_searching_retained_text(tmp_path):
    import re

    from nekoclaw.runtime.tools.fs import READ_MAX_BYTES

    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    document = tmp_path / "minutes.txt"
    document.write_text(("hit paragraph\n" * 4200) + "hit final-decision\n", encoding="utf-8")
    calls = []
    stopped = False

    async def action(_, payload):
        calls.append(payload)
        if payload["phase"] == "begin":
            if payload.get("origins") and stopped:
                return {"allow": False}
            return {"allow": True, "receipt": f"receipt-{len(calls)}"}
        return {"allow": True}

    events = Recorder()
    runtime = DshRuntime(events, HostCallbacks(native_execution_action=action),
                         launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
                         environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    contents = []
    saved = []

    def respond(req):
        nonlocal stopped
        last = req["messages"][-1]
        if last["role"] == "tool":
            contents.append(last["content"])
        if len(contents) == 0:
            name, args = "read", {"file_path": str(document)}
        elif len(contents) == 1:
            name, args = "grep", {"path": str(document), "pattern": "hit"}
        elif len(contents) == 2:
            match = re.search("(" + re.escape(str(tmp_path)) + r"[^\n]*?-grep(?:-results)?\.txt)", contents[-1])
            if not match:
                return [sse_text("No locator"), sse_finish()]
            saved.append(match.group(1))
            name, args = "read", {"file_path": saved[0]}
        elif len(contents) in (3, 4):
            stopped = len(contents) == 4
            name, args = "grep", {"path": saved[0], "pattern": "final-decision"}
        else:
            return [sse_text("Done"), sse_finish()]
        return [sse_tool_call(name, args, f"call_{len(contents)}"), sse_finish("tool_calls")]

    try:
        with FakeOpenAIServer(respond) as server:
            model = ModelConfig(model="fixture", base_url=server.base_url, api_key="local", auto_extra_body=False)
            spec = make_spec(tmp_path, model=model)
            await runtime.start(spec)
            assert (await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=60))["type"] == "started"
            await runtime.prompt(spec.session_id, "Search for the final decision.")
            await events.wait_ended(spec.session_id, timeout=60)
        assert len(contents) == 5, [s[-300:] for s in contents]
        assert "Use grep" in contents[0] and "Use grep" in contents[2]
        assert "hit final-decision" in contents[3] and "hit final-decision" not in contents[4]
        assert saved and not Path(saved[0]).exists()
        assert all(len(s.encode("utf-8")) <= READ_MAX_BYTES for s in contents)
        recovered = [p["origins"] for p in calls if p.get("origins")]
        assert len(recovered) == 3 and len({tuple(refs) for refs in recovered}) == 1
    finally:
        await runtime.shutdown()


async def test_pinned_native_job_output_checks_the_command_source(tmp_path):
    import re

    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    shell, command = ("pwsh", "Write-Output kept-job-text") if os.name == "nt" else ("bash", "echo kept-job-text")
    begins = []

    async def action(_, payload):
        if payload["phase"] == "begin":
            begins.append(payload)
            return {"allow": payload.get("tool") != "job_output", "receipt": "original-command"}
        return {"allow": True}

    events = Recorder()
    runtime = DshRuntime(events, HostCallbacks(native_execution_action=action),
                         launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
                         environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    job_ids = []

    def respond(request):
        messages = request["messages"]
        if not any(m["role"] == "tool" for m in messages):
            return [sse_tool_call(shell, {"command": command, "description": "fixture", "run_in_background": True}),
                    sse_finish("tool_calls")]
        if not job_ids:
            response = next(m["content"] for m in messages if m["role"] == "tool")
            match = re.search(r"started background job ([\w-]+)", response)
            if match:
                job_ids.append(match.group(1))
                return [sse_tool_call("job_output", {"job_id": job_ids[0]}, "output"), sse_finish("tool_calls")]
        return [sse_text("Done"), sse_finish()]

    try:
        with FakeOpenAIServer(respond) as server:
            spec = make_spec(tmp_path, model=ModelConfig(model="fixture", base_url=server.base_url,
                                                         api_key="local", auto_extra_body=False))
            await runtime.start(spec)
            assert (await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=60))["type"] == "started"
            await runtime.prompt(spec.session_id, "Start the fixture job and collect its output.")
            await events.wait_ended(spec.session_id, timeout=60)
        assert job_ids and [p.get("origins") for p in begins if p["tool"] == "job_output"] == [["original-command"]]
        replies = [m["content"] for req in server.requests for m in req["messages"] if m["role"] == "tool"]
        assert all("kept-job-text" not in reply for reply in replies)
    finally:
        await runtime.shutdown()
