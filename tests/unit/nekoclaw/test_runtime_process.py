"""The runtime as a child process, and the same interface in-process.

The child process uses the real model layer against a local http endpoint
(``fake_openai_server``): no provider, no real key, no real configuration.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import psutil
import pytest

from nekoclaw.runtime.core import InProcessRuntime
from nekoclaw.runtime.host import HostCallbacks
from nekoclaw.runtime.model import LLMClientProvider
from nekoclaw.runtime.process import SubprocessRuntime
from nekoclaw.runtime.protocol import FamiliarSpec, ModelConfig, parse_command, parse_event
from nekoclaw.runtime.session import FamiliarSession
from tests.unit.nekoclaw.fake_openai_server import (
    FakeOpenAIServer,
    sse_finish,
    sse_sleep,
    sse_text,
    sse_tool_call,
    sse_usage,
)
from tests.unit.nekoclaw.fakes import Recorder, ScriptedLLM, call, finish, make_spec, say

pytestmark = pytest.mark.integration_serial

API_KEY = "sk-child-only-secret"


def _spec(tmp_path: Path, session_id: str, base_url: str) -> FamiliarSpec:
    return make_spec(
        tmp_path,
        session_id,
        model=ModelConfig(model="fake-model", base_url=base_url, api_key=API_KEY, max_retries=0),
    )


def _last_user_text(body: dict[str, Any]) -> str:
    for message in reversed(body["messages"]):
        if message["role"] == "user" and not str(message["content"]).startswith("<runtime_context>"):
            return str(message["content"])
    return ""


@pytest.fixture
def local_only(monkeypatch: pytest.MonkeyPatch) -> None:
    # The child inherits the environment: keep any system proxy away from 127.0.0.1.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


async def test_one_child_serves_many_sessions_and_exits_when_idle(tmp_path: Path, local_only: None) -> None:
    reports: list[tuple[str, dict]] = []

    async def report(session_id: str, payload: dict) -> dict:
        reports.append((session_id, payload))
        return {"message": "noted"}

    def respond(body: dict) -> list[dict]:
        text = _last_user_text(body)
        if "again" in text:
            return [sse_text("resumed fine"), sse_finish()]
        if any(message["role"] == "tool" for message in body["messages"]):
            return [sse_text("alpha reported"), sse_finish(), sse_usage(60, 3)]
        if "alpha" in text:
            return [
                sse_tool_call("report", {"status": "progress", "summary": "alpha is half way"}),
                sse_finish("tool_calls"),
                sse_usage(50, 9),
            ]
        return [sse_sleep(0.3), sse_text("beta done"), sse_finish(), sse_usage(40, 2)]

    events = Recorder()
    runtime = SubprocessRuntime(events, HostCallbacks(report=report), idle_exit_seconds=1.5)
    assert runtime.pid is None  # constructing it starts nothing

    with FakeOpenAIServer(respond) as server:
        try:
            await runtime.start(_spec(tmp_path, "alpha", server.base_url))
            await runtime.start(_spec(tmp_path, "beta", server.base_url))
            ready = await events.wait_for(lambda e: e["type"] == "runtime_ready", timeout=90)
            await events.wait_for(lambda e: e["type"] == "started" and e["session_id"] == "alpha", timeout=90)
            await events.wait_for(lambda e: e["type"] == "started" and e["session_id"] == "beta", timeout=90)
            child_pid = runtime.pid
            assert child_pid == ready["pid"] and child_pid != os.getpid()
            assert "report" in events.of("started", "alpha")[0]["tools"]

            await runtime.prompt("alpha", "do the alpha task")
            await runtime.prompt("beta", "do the beta task")
            alpha_end = await events.wait_ended("alpha", timeout=90)
            beta_end = await events.wait_ended("beta", timeout=90)
            assert (alpha_end["reason"], alpha_end["text"]) == ("completed", "alpha reported")
            assert (beta_end["reason"], beta_end["text"]) == ("completed", "beta done")
            # Both sessions ran in the same single process.
            assert len(events.of("runtime_ready")) == 1 and runtime.pid == child_pid

            # The host callback ran here, in the parent, and its answer went back.
            assert reports == [("alpha", {"status": "progress", "summary": "alpha is half way", "details": None, "delivery": None})]
            assert events.of("report", "alpha")[0]["summary"] == "alpha is half way"
            assert events.of("tool_end", "alpha")[0]["text"] == "report delivered (progress). noted"
            assert events.of("usage", "beta")[0]["context_tokens"] == 42

            # The key reached the endpoint from the start command alone.
            assert {headers["authorization"] for headers in server.headers} == {f"Bearer {API_KEY}"}
            child = psutil.Process(child_pid)
            assert API_KEY not in " ".join(child.cmdline())
            assert API_KEY not in "".join(child.environ().values())
            assert API_KEY not in "".join(os.environ.values())

            # Every event is well formed JSON as the protocol defines it.
            for event in events.events:
                parse_event(event)

            # Nothing to do: the child closes its sessions and exits by itself.
            exit_event = await events.wait_for(lambda e: e["type"] == "runtime_exit", timeout=60)
            assert exit_event["reason"] == "idle"
            closed = [e["session_id"] for e in events.of("ended") if e["reason"] == "closed"]
            assert sorted(closed) == ["alpha", "beta"]
            for _ in range(100):
                if not psutil.pid_exists(child_pid) or runtime.pid is None:
                    break
                await asyncio.sleep(0.1)
            assert runtime.pid is None

            # A command for the gone process is rejected, not lost silently.
            mark = len(events.events)
            await runtime.prompt("alpha", "anyone there?")
            rejected = await events.wait_for(lambda e: e["type"] == "error", start=mark)
            assert rejected["command"] == "prompt" and "not running" in rejected["message"]

            # The next start brings up a fresh process; the session resumes.
            mark = len(events.events)
            await runtime.start(_spec(tmp_path, "alpha", server.base_url))
            restarted = await events.wait_for(lambda e: e["type"] == "started", timeout=90, start=mark)
            assert restarted["resumed"] is True
            assert runtime.pid not in (None, child_pid)
            await runtime.prompt("alpha", "once again please")
            again = await events.wait_ended("alpha", timeout=90, start=mark)
            assert again["text"] == "resumed fine"
            history = [str(message["content"]) for message in server.requests[-1]["messages"]]
            assert "do the alpha task" in history and "alpha reported" in history
        finally:
            await runtime.shutdown()
    assert runtime.pid is None
    assert events.of("runtime_exit")[-1]["reason"] == "shutdown"


async def test_abort_reaches_the_child_and_a_dead_child_fails_its_sessions(tmp_path: Path, local_only: None) -> None:
    def respond(body: dict) -> list[dict]:
        del body
        return [sse_text("starting"), sse_sleep(120), sse_text("never"), sse_finish()]

    events = Recorder()
    runtime = SubprocessRuntime(events, idle_exit_seconds=300)
    with FakeOpenAIServer(respond) as server:
        try:
            await runtime.start(_spec(tmp_path, "one", server.base_url))
            await events.wait_for(lambda e: e["type"] == "started", timeout=90)
            await runtime.prompt("one", "long task")
            await events.wait_for(lambda e: e["type"] == "text", timeout=90)
            await runtime.abort("one")
            aborted = await events.wait_ended("one", timeout=60)
            assert aborted["reason"] == "aborted"

            # Unknown sessions and duplicate starts come back as error events.
            mark = len(events.events)
            await runtime.steer("ghost", "hello")
            await runtime.start(_spec(tmp_path, "one", server.base_url))
            errors = [
                await events.wait_for(lambda e: e["type"] == "error" and e["command"] == "steer", start=mark),
                await events.wait_for(lambda e: e["type"] == "error" and e["command"] == "start", start=mark),
            ]
            assert 'unknown session "ghost"' in errors[0]["message"]
            assert "already exists" in errors[1]["message"]

            # The process dies mid-run: its session is reported as failed.
            mark = len(events.events)
            await runtime.prompt("one", "another long task")
            await events.wait_for(lambda e: e["type"] == "text", timeout=90, start=mark)
            psutil.Process(runtime.pid).kill()
            failed = await events.wait_ended("one", timeout=60, start=mark)
            assert failed["reason"] == "error"
            assert failed["error"] == "the runtime process exited"
            exit_event = await events.wait_for(lambda e: e["type"] == "runtime_exit", start=mark)
            assert exit_event["reason"] == "error"
        finally:
            await runtime.shutdown()


# ---------------------------------------------------------------------------
# The same interface without a child process
# ---------------------------------------------------------------------------


def _scripted_opener(scripts: dict[str, ScriptedLLM]):  # noqa: ANN202
    async def open_session(spec, host, emit):  # noqa: ANN001, ANN202
        llm = scripts[spec.session_id]

        async def factory() -> ScriptedLLM:
            return llm

        return await FamiliarSession.open(
            spec, host, emit, model=LLMClientProvider(spec.model, client_factory=factory)
        )

    return open_session


@pytest.mark.parametrize("runtime_type", [InProcessRuntime, SubprocessRuntime])
def test_both_runtimes_offer_the_same_methods(runtime_type: type) -> None:
    for name in ("start", "prompt", "steer", "follow_up", "abort", "close", "shutdown"):
        assert asyncio.iscoroutinefunction(getattr(runtime_type, name)), name


async def test_in_process_runtime_runs_sessions_side_by_side(tmp_path: Path) -> None:
    gate = asyncio.Event()

    async def wait_for_other() -> None:
        await asyncio.wait_for(gate.wait(), 20)

    scripts = {
        # `slow` cannot finish until `fast` has finished: they must overlap.
        "slow": ScriptedLLM([[say("slow "), wait_for_other, say("done"), finish()]]),
        "fast": ScriptedLLM([[call("todo_write", {"todos": [{"content": "x", "status": "completed"}]}), finish("tool_calls")], [say("fast done"), finish()]]),
    }
    events = Recorder()
    runtime = InProcessRuntime(events, open_session=_scripted_opener(scripts))
    try:
        await runtime.start(make_spec(tmp_path, "slow"))
        await runtime.start(make_spec(tmp_path, "fast"))
        assert sorted(runtime.core.session_ids) == ["fast", "slow"]
        assert runtime.core.idle
        await runtime.prompt("slow", "take your time")
        await runtime.prompt("fast", "be quick")
        assert not runtime.core.idle
        fast = await events.wait_ended("fast")
        assert fast["text"] == "fast done"
        assert not events.of("ended", "slow")
        gate.set()
        slow = await events.wait_ended("slow")
        assert slow["text"] == "slow done"
        assert runtime.core.idle

        # Rejections are events here too.
        mark = len(events.events)
        await runtime.prompt("nobody", "hi")
        await runtime.start(make_spec(tmp_path, "fast"))
        kinds = [(e["command"], e["session_id"]) for e in events.events[mark:] if e["type"] == "error"]
        assert kinds == [("prompt", "nobody"), ("start", "fast")]

        await runtime.close("fast")
        assert runtime.core.session_ids == ["slow"]
        assert events.of("ended", "fast")[-1]["reason"] == "closed"
        for event in events.events:
            parse_event(event)
    finally:
        await runtime.shutdown()
    assert runtime.core.session_ids == []


async def test_a_prompt_during_a_run_is_rejected_as_an_event(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    events = Recorder()
    runtime = InProcessRuntime(
        events, open_session=_scripted_opener({"s1": ScriptedLLM([[say("busy"), hold, finish()]])})
    )
    try:
        await runtime.start(make_spec(tmp_path))
        await runtime.prompt("s1", "first")
        await events.wait_for(lambda e: e["type"] == "text")
        await runtime.prompt("s1", "second")
        error = await events.wait_for(lambda e: e["type"] == "error")
        assert error["command"] == "prompt" and "use steer or follow_up" in error["message"]
        release.set()
        await events.wait_ended("s1")
    finally:
        release.set()
        await runtime.shutdown()


def test_commands_are_validated_at_the_boundary(tmp_path: Path) -> None:
    from pydantic import ValidationError

    spec = make_spec(tmp_path)
    command = parse_command({"type": "start", "spec": spec.model_dump(mode="json")})
    assert command.spec.model.api_key == "test-key"
    # The key is kept out of representations that end up in logs.
    assert "test-key" not in repr(command.spec.model)
    assert parse_command({"type": "steer", "session_id": "s1", "text": "hi"}).type == "steer"
    with pytest.raises(ValidationError):
        parse_command({"type": "prompt", "session_id": "s1", "text": ""})
    with pytest.raises(ValidationError):
        parse_command({"type": "start", "spec": {**spec.model_dump(mode="json"), "gateway": "x"}})
    with pytest.raises(ValidationError):
        parse_command({"type": "explode"})
    with pytest.raises(ValidationError):
        FamiliarSpec(session_id="../evil", workdir="w", session_dir="s", model=ModelConfig(model="m"))
