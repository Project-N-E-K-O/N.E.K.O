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
    released = set()

    async def action(_, payload):
        calls.append(payload)
        if payload["phase"] == "release":
            released.add(payload["receipt"])
        if payload["phase"] == "begin":
            if payload.get("origins") and (stopped or released.intersection(payload["origins"])):
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
        elif len(contents) in (2, 4):
            paths = re.findall(re.escape(str(tmp_path)) + r"[^\n]*?-grep(?:-results)?\.txt", contents[-1])
            if not paths:
                return [sse_text("No locator"), sse_finish()]
            saved.append(paths[-1])
            name, args = ("read", {"file_path": saved[0]}) if len(contents) == 2 else (
                "grep", {"path": saved[-1], "pattern": "final-decision"})
        elif len(contents) == 3:
            name, args = "grep", {"path": saved[0], "pattern": "hit"}
        elif len(contents) == 5:
            stopped = True
            name, args = "grep", {"path": saved[-1], "pattern": "final-decision"}
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
        assert len(contents) == 6, [s[-300:] for s in contents]
        assert "Use grep" in contents[0] and "Use grep" in contents[2]
        assert "hit final-decision" in contents[4] and "hit final-decision" not in contents[5]
        assert saved and not Path(saved[0]).exists()
        assert all(len(s.encode("utf-8")) <= READ_MAX_BYTES for s in contents)
        recovered = [p["origins"] for p in calls if p.get("origins")]
        assert len(recovered) == 4 and recovered[0] == recovered[1] and recovered[2] == recovered[3]
        assert recovered[0] != recovered[2]
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize(("post_block", "retained"), [(False, False), (True, False), (True, True)])
async def test_pinned_native_job_output_checks_the_command_source(tmp_path, monkeypatch, post_block, retained):
    import re

    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    shell, command = (("pwsh", "Write-Output kept-job-text; Start-Sleep -Seconds 2; Write-Output later-text")
                      if os.name == "nt" else ("bash", "echo kept-job-text; sleep 2; echo later-text"))
    if retained:
        import asyncio
        from shlex import quote
        from nekoclaw.runtime.tools import fs

        monkeypatch.setattr(fs, "READ_MAX_BYTES", 512)
        saved_calls = []
        retained_output = DshRuntime._retained_output

        async def observe_save(runtime, token, request):
            result = await retained_output(runtime, token, request)
            if request.get("action") == "save_job":
                saved_calls.append(request["call"])
            return result

        monkeypatch.setattr(DshRuntime, "_retained_output", observe_save)
        marker = tmp_path / "release-job"
        ready = tmp_path / "output-ready"
        if os.name == "nt":
            release_path = str(marker).replace("'", "''")
            ready_path = str(ready).replace("'", "''")
            command = ("Write-Output ('a' * 1024); Write-Output kept-job-text; "
                       f"[IO.File]::WriteAllText('{ready_path}', 'ready'); "
                       f"while (!(Test-Path -LiteralPath '{release_path}')) "
                       "{ Start-Sleep -Milliseconds 50 }; Write-Output later-text")
        else:
            command = ("printf '%01024d\\n' 0; echo kept-job-text; "
                       f"touch {quote(str(ready))}; while [ ! -f {quote(str(marker))} ]; do sleep .05; done; echo later-text")
    begins = []
    lists = []
    if post_block:
        block = """  ctx.on('tools/post-execute', (exec, result, next) =>
    exec.callId === 'output0' ? {kind: 'block', feedback: [{type: 'text', text: 'fixture post block'}]} : next())
"""
        monkeypatch.setattr(profile, "GUARD_SOURCE", profile.GUARD_SOURCE.replace("  process.stderr.write", block + "  process.stderr.write"))

    async def action(_, payload):
        if payload["phase"] == "begin":
            begins.append(payload)
            if payload["tool"] == "job_list":
                allowed = payload["origins"] if not lists else []
                lists.append(allowed)
                return {"allow": True, "receipt": f"list-{len(lists)}", "origins": allowed}
            if payload["tool"] == "job_output":
                attempt = sum(p["tool"] == "job_output" for p in begins)
                if retained and attempt == 1:
                    async with asyncio.timeout(10):
                        while not ready.exists():
                            await asyncio.sleep(.02)
                return {"allow": True, "receipt": f"read-{attempt}"}
            return {"allow": True, "receipt": "original-command"}
        if retained and (payload["phase"], payload.get("receipt")) == ("finish", "read-1"):
            marker.touch()
        return {"allow": post_block or (payload["phase"], payload.get("receipt")) != ("finish", "read-1")}

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
        if job_ids and len(lists) < 2:
            return [sse_tool_call("job_list", {}, f"list{len(lists)}"), sse_finish("tool_calls")]
        attempt = sum(p["tool"] == "job_output" for p in begins)
        if retained and attempt == 1:
            return [sse_tool_call("job_output", {"job_id": job_ids[0], "wait": True}, f"output{i}", index=i - 1)
                    for i in (1, 2)] + [sse_finish("tool_calls")]
        if retained and attempt == 3 and not any(m.get("tool_call_id") == "verify-spill" for m in messages):
            for msg in messages:
                if msg.get("tool_call_id") not in {"output1", "output2"}:
                    continue
                match = re.search(re.escape(str(tmp_path)) + r"[^\n]*?-job_output\.txt", msg["content"])
                if match:
                    return [sse_tool_call("grep", {"path": match.group(), "pattern": "kept-job-text|later-text"},
                                          "verify-spill"), sse_finish("tool_calls")]
        if attempt < 3:
            args = {"job_id": job_ids[0], "wait": attempt > 0}
            if retained and attempt == 0:
                args.update(wait=True, timeout_ms=300)
            return [sse_tool_call("job_output", args, f"output{attempt}"),
                    sse_finish("tool_calls")]
        return [sse_text("Done"), sse_finish()]

    try:
        with FakeOpenAIServer(respond) as server:
            spec = make_spec(tmp_path, model=ModelConfig(model="fixture", base_url=server.base_url,
                                                         api_key="local", auto_extra_body=False))
            await runtime.start(spec)
            assert (await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=60))["type"] == "started"
            await runtime.prompt(spec.session_id, "Start the fixture job and collect its output.")
            await events.wait_ended(spec.session_id, timeout=60)
        assert job_ids and [p.get("origins") for p in begins if p["tool"] == "job_output"] == [["original-command"]] * 3
        assert lists == [["original-command"], []]
        listed = [req["messages"][-1]["content"] for req in server.requests
                  if req["messages"][-1].get("tool_call_id") in {"list0", "list1"}]
        assert len(listed) == 2 and job_ids[0] in listed[0] and job_ids[0] not in listed[1], listed
        output = {m["tool_call_id"]: m["content"] for req in server.requests for m in req["messages"]
                  if m["role"] == "tool" and m.get("tool_call_id", "").startswith("output")}
        assert ("fixture post block" if post_block else "could not be delivered") in output["output0"]
        assert "kept-job-text" not in output["output0"]
        if retained:
            assert saved_calls[0] == "output0" and len(saved_calls) == 2, saved_calls
            assert [p.get("origins") for p in begins if p["tool"] == "grep"] == [["original-command"]]
            verified = next(msg["content"] for req in server.requests for msg in req["messages"]
                            if msg.get("tool_call_id") == "verify-spill")
            assert "kept-job-text" in verified and "later-text" in verified, verified
            assert sum("Use grep" in output[key] for key in ("output1", "output2")) == 1
        else:
            assert "kept-job-text" in output["output1"] and "later-text" in output["output1"]
            assert "kept-job-text" not in output["output2"] and "later-text" not in output["output2"]
    finally:
        if retained:
            marker.touch()
        await runtime.shutdown()


async def test_pinned_dsh_fetch_uses_prepared_host_receipt(tmp_path, monkeypatch):
    from nekoclaw.runtime.tools import web

    binary = os.getenv("NEKOCLAW_TEST_DSH_BINARY")
    if not binary:
        pytest.skip("Set NEKOCLAW_TEST_DSH_BINARY to the pinned dsh executable")
    phases = []
    async def audit(session_id, payload):
        phases.append((session_id, payload["phase"], payload.get("receipt")))
        return {"allow": True, "receipt": "prepared-web"}
    async def fetch(url):
        return "local web fixture"
    monkeypatch.setattr(web, "web_fetch", fetch)
    events = Recorder()
    runtime = DshRuntime(events, HostCallbacks(execution_action=audit, native_execution_action=audit),
                         launch=lambda: profile.DshLaunch(home=tmp_path / "dsh", executable=Path(binary)),
                         environment={**os.environ, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    replies = iter([[sse_tool_call("mcp__host__web_fetch", {"url": "https://example.invalid/test"}),
                     sse_finish("tool_calls")], [sse_text("Done"), sse_finish()]])
    try:
        with FakeOpenAIServer(lambda _: next(replies)) as server:
            spec = make_spec(tmp_path, model=ModelConfig(model="fixture", base_url=server.base_url,
                                                         api_key="local", auto_extra_body=False))
            await runtime.start(spec)
            assert (await events.wait_for(lambda e: e["type"] in ("started", "error"), timeout=60))["type"] == "started"
            await runtime.prompt(spec.session_id, "Fetch the fixture.")
            await events.wait_ended(spec.session_id, timeout=60)
        assert "local web fixture" in str(server.requests[-1]["messages"][-1])
        assert phases == [(spec.session_id, "begin", None), (spec.session_id, "check", "prepared-web"),
                          (spec.session_id, "finish", "prepared-web")]
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


@pytest.mark.parametrize(("configured", "expected_asks"), [(None, 0), ("always_ask", 2), ("first_ask", 1), ("allow", 0)])
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
        return "session" if "session" in question.options else "once"

    gate = PermissionGate(store, once)

    async def decide(session_id, payload):
        assert payload["category"] == "workdir_files"
        return (await gate.check_native(
            session_id, payload["category"], detail=payload, stock_tool=payload.get("private_route") is True,
        )).to_json()

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
        options = ("session", "once", "deny") if configured == "first_ask" else ("once", "deny")
        assert questions == [("workdir_files", options)] * expected_asks
    finally:
        await runtime.shutdown()
