"""The runtime as a child process, and the same interface in-process.

The child process uses the real model layer against a local http endpoint
(``fake_openai_server``): no provider, no real key, no real configuration.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any

import psutil
import pytest

from nekoclaw.runtime.core import InProcessRuntime, RuntimeCore
from nekoclaw.runtime.host import HostCallbacks, NoHost
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


async def test_a_shutdown_lets_a_close_that_is_under_way_finish(tmp_path: Path, local_only: None) -> None:
    def respond(body: dict) -> list[dict]:
        del body
        return [sse_text("starting"), sse_sleep(120), sse_text("never"), sse_finish()]

    events = Recorder()
    runtime = SubprocessRuntime(events, idle_exit_seconds=300)
    with FakeOpenAIServer(respond) as server:
        try:
            await runtime.start(_spec(tmp_path, "one", server.base_url))
            await runtime.prompt("one", "long task")
            await events.wait_for(lambda e: e["type"] == "text", timeout=90)
            mark = len(events.events)
            # Closing a session in the middle of a run takes a moment, and the
            # shutdown arrives while the child is at it.
            await runtime.close("one")
            await asyncio.sleep(0.05)
        finally:
            await runtime.shutdown()
    ended = [(event["reason"], event.get("error")) for event in events.events[mark:] if event["type"] == "ended"]
    # The session was closed properly; it was not left half closed and then
    # reported as lost with the process.
    assert ended[-1] == ("closed", None)
    assert all(reason != "error" for reason, _error in ended)
    assert events.of("runtime_exit")[-1]["reason"] == "shutdown"


async def test_commands_sent_right_after_start_wait_for_the_session(tmp_path: Path, local_only: None) -> None:
    def respond(body: dict) -> list[dict]:
        del body
        return [sse_text("hello there"), sse_finish()]

    events = Recorder()
    runtime = SubprocessRuntime(events, idle_exit_seconds=300)
    with FakeOpenAIServer(respond) as server:
        try:
            # No waiting for `started` in between, exactly as with the in-process runtime.
            await runtime.start(_spec(tmp_path, "eager", server.base_url))
            await runtime.prompt("eager", "say hello")
            outcome = await events.wait_for(lambda e: e["type"] in ("ended", "error"), timeout=90)
            assert (outcome["type"], outcome.get("text")) == ("ended", "hello there")
            assert events.of("error") == []
        finally:
            await runtime.shutdown()


async def test_a_start_that_meets_an_idle_exit_goes_to_a_new_child(tmp_path: Path, local_only: None) -> None:
    def respond(body: dict) -> list[dict]:
        del body
        return [sse_text("done"), sse_finish()]

    events = Recorder()
    runtime = SubprocessRuntime(events, idle_exit_seconds=1.0)
    with FakeOpenAIServer(respond) as server:
        late = _spec(tmp_path, "late", server.base_url)
        retire = runtime._retire
        racing: list[asyncio.Future[None]] = []

        def retire_and_start(command: Any) -> None:
            retire(command)
            # The host starts a session at the very moment the idle child was
            # told that it may exit.
            if command.reason == "idle" and not racing:
                racing.append(asyncio.ensure_future(runtime.start(late)))

        runtime._retire = retire_and_start  # type: ignore[method-assign]
        try:
            await runtime.start(_spec(tmp_path, "early", server.base_url))
            answer = await events.wait_for(
                lambda e: e["type"] in ("started", "error") and e.get("session_id") == "late", timeout=120
            )
            assert answer["type"] == "started"
            # The first child left in an orderly way and a second one took the session.
            pids = [event["pid"] for event in events.of("runtime_ready")]
            assert len(pids) == 2 and pids[0] != pids[1]
            assert [event["reason"] for event in events.of("runtime_exit")] == ["idle"]
            assert [e["session_id"] for e in events.of("ended") if e["reason"] == "closed"] == ["early"]
            assert events.of("runtime_idle") == []  # the request to exit stays inside the shell
            await runtime.prompt("late", "go")
            ended = await events.wait_ended("late", timeout=90)
            assert ended["text"] == "done"
            assert events.of("error") == []
        finally:
            await runtime.shutdown()


class _FakePipe:
    """Stands in for the pipe and its writer thread; records what would be sent."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        # Set once the pretended child has exited.
        self.exited = threading.Event()

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass

    def is_alive(self) -> bool:
        return not self.exited.is_set()

    def join(self, timeout: float | None = None) -> None:
        self.exited.wait(timeout)

    def terminate(self) -> None:
        self.exited.set()


def _wired(events: Recorder) -> tuple[SubprocessRuntime, _FakePipe, Any]:
    """A runtime that believes it has a running child, without starting one."""
    runtime = SubprocessRuntime(events)
    pipe = _FakePipe()
    runtime._writer = pipe  # type: ignore[assignment]
    runtime._connection = pipe
    runtime._process = pipe

    def feed(message: dict[str, Any] | None) -> None:
        data = None if message is None else json.dumps(message).encode("utf-8")
        runtime._on_pipe(pipe, data)

    return runtime, pipe, feed


async def test_an_idle_child_may_exit_only_when_nothing_is_on_its_way() -> None:
    events = Recorder()
    runtime, pipe, feed = _wired(events)

    await runtime.prompt("s1", "hi")
    # The child asks to exit before it has read that prompt: declined.
    feed({"type": "runtime_idle", "received": 0})
    assert [message["type"] for message in pipe.sent] == ["prompt"]
    # It asks again having read everything: granted.
    feed({"type": "runtime_idle", "received": 1})
    assert pipe.sent[-1] == {"type": "shutdown", "reason": "idle"}

    # From here on nothing is written to this child; the command is rejected.
    await runtime.prompt("s1", "again")
    assert len(pipe.sent) == 2
    rejected = events.of("error")
    assert len(rejected) == 1 and rejected[0]["command"] == "prompt"
    assert "shutting down" in rejected[0]["message"]
    # A repeated request changes nothing, and none of this reaches the host.
    feed({"type": "runtime_idle", "received": 1})
    assert len(pipe.sent) == 2 and events.of("runtime_idle") == []


async def test_a_start_the_child_never_answered_is_reported_when_it_dies(tmp_path: Path) -> None:
    events = Recorder()
    runtime, pipe, feed = _wired(events)

    await runtime.start(make_spec(tmp_path, "answered"))
    await runtime.start(make_spec(tmp_path, "refused"))
    await runtime.start(make_spec(tmp_path, "pending"))
    assert [message["type"] for message in pipe.sent] == ["start", "start", "start"]
    feed({"type": "started", "session_id": "answered", "seq": 1})
    feed({"type": "error", "session_id": "refused", "seq": 1, "command": "start", "message": "no"})
    mark = len(events.events)
    feed(None)  # the pipe closed: the child is gone

    after = events.events[mark:]
    assert [(e["type"], e.get("session_id"), e.get("command")) for e in after] == [
        ("ended", "answered", None),
        ("error", "pending", "start"),
        ("runtime_exit", None, None),
    ]
    assert after[1]["message"] == "the runtime process exited before the session started"
    for event in events.events:
        parse_event(event)


async def test_commands_for_a_session_that_is_still_starting_wait_for_it(tmp_path: Path) -> None:
    llm = ScriptedLLM([[say("one"), finish()], [say("two"), finish()]])
    opener = _scripted_opener({"s1": llm})

    async def slow_open(spec, host, emit):  # noqa: ANN001, ANN202
        await asyncio.sleep(0.2)
        if spec.session_id == "broken":
            raise RuntimeError("no such model")
        return await opener(spec, host, emit)

    events = Recorder()
    runtime = InProcessRuntime(events, open_session=slow_open)
    try:
        # All three are under way before the session exists.
        await asyncio.gather(
            runtime.start(make_spec(tmp_path)),
            runtime.prompt("s1", "first"),
            runtime.follow_up("s1", "second"),
        )
        ended = await events.wait_ended("s1")
        assert ended["text"] == "two"
        assert [request[-1]["content"] for request in llm.requests] == ["first", "second"]
        assert events.of("error") == []

        # When the start fails, the command that waited is rejected after it.
        await asyncio.gather(
            runtime.start(make_spec(tmp_path, "broken")), runtime.prompt("broken", "anyone?")
        )
        errors = [(e["command"], e["message"]) for e in events.of("error", "broken")]
        assert errors == [
            ("start", "RuntimeError: no such model"),
            ("prompt", 'unknown session "broken"'),
        ]
    finally:
        await runtime.shutdown()


async def test_shutdown_closes_existing_sessions_without_waiting_for_a_slow_start(tmp_path: Path) -> None:
    order: list[str] = []
    emitted: list[dict[str, Any]] = []
    gate = asyncio.Event()

    class Stub:
        busy = False

        def __init__(self, name: str) -> None:
            self.name = name

        def prompt(self, text: str) -> None:
            order.append(f"prompt {self.name}: {text}")

        async def close(self) -> None:
            order.append(f"close {self.name}")

    async def open_session(spec, host, emit):  # noqa: ANN001, ANN202
        del host, emit
        if spec.session_id == "slow":
            await gate.wait()
        return Stub(spec.session_id)

    core = RuntimeCore(NoHost(), emitted.append, open_session=open_session)
    await core.start(make_spec(tmp_path, "ready"))
    slow = asyncio.ensure_future(core.start(make_spec(tmp_path, "slow")))
    await asyncio.sleep(0)
    stopping = asyncio.ensure_future(core.shutdown())
    await asyncio.sleep(0.05)
    # The session that exists is told to stop right away, although another one
    # is still being created and the shutdown as a whole is not finished.
    assert order == ["close ready"] and not stopping.done()
    # No new work is accepted meanwhile.
    core.prompt("ready", "one more thing")
    assert [(e["command"], e["message"]) for e in emitted if e["type"] == "error"] == [
        ("prompt", "the runtime is shutting down")
    ]

    # A caller with a time limit gives up waiting. What existed was told to
    # stop all the same, and nothing else was cancelled along with the wait.
    stopping.cancel()
    await asyncio.gather(stopping, return_exceptions=True)
    gate.set()
    await slow
    assert core.session_ids == ["slow"]
    await core.shutdown()
    assert order == ["close ready", "close slow"]


async def test_a_bounded_shutdown_returns_and_a_late_session_is_closed_on_arrival(tmp_path: Path) -> None:
    order: list[str] = []
    gate = asyncio.Event()

    class Stub:
        busy = False

        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            order.append(f"close {self.name}")

    async def open_session(spec, host, emit):  # noqa: ANN001, ANN202
        del host, emit
        if spec.session_id == "stuck":
            await gate.wait()
        return Stub(spec.session_id)

    core = RuntimeCore(NoHost(), lambda event: None, open_session=open_session)
    await core.start(make_spec(tmp_path, "ready"))
    stuck = asyncio.ensure_future(core.start(make_spec(tmp_path, "stuck")))
    await asyncio.sleep(0)

    # Creating "stuck" hangs. A shutdown with a time limit returns all the same.
    await asyncio.wait_for(core.shutdown(timeout=0.2), 5)
    assert order == ["close ready"]
    assert core.session_ids == []

    # When that session finally exists it is closed at once instead of being
    # left open behind the shutdown.
    gate.set()
    await stuck
    assert order == ["close ready", "close stuck"]
    assert core.session_ids == [] and core.idle
    # A later session of the same name is unaffected.
    await core.start(make_spec(tmp_path, "stuck"))
    assert core.session_ids == ["stuck"]
    await core.shutdown()


class _SlowToClose:
    """Stands in for a session whose closing takes a moment."""

    busy = False

    def __init__(self, name: str, order: list[str]) -> None:
        self.name = name
        self._order = order

    async def close(self) -> None:
        self._order.append(f"closing {self.name}")
        await asyncio.sleep(0.3)
        self._order.append(f"closed {self.name}")


def _slow_closing_core(order: list[str], emitted: list[dict[str, Any]]) -> RuntimeCore:
    async def open_session(spec, host, emit):  # noqa: ANN001, ANN202
        del host, emit
        order.append(f"opened {spec.session_id}")
        return _SlowToClose(spec.session_id, order)

    return RuntimeCore(NoHost(), emitted.append, open_session=open_session)


async def test_a_cancelled_close_still_closes_the_session(tmp_path: Path) -> None:
    order: list[str] = []
    core = _slow_closing_core(order, [])
    await core.start(make_spec(tmp_path))
    closing = asyncio.ensure_future(core.close("s1"))
    await asyncio.sleep(0.05)
    # Whoever asked for the close stops waiting for it.
    closing.cancel()
    await asyncio.gather(closing, return_exceptions=True)
    assert order == ["opened s1", "closing s1"]
    # The close goes on regardless, and a new session of the same id waits for it.
    await core.start(make_spec(tmp_path))
    assert order == ["opened s1", "closing s1", "closed s1", "opened s1"]
    await core.shutdown()


async def test_closing_a_session_that_is_already_closing_waits_for_the_same_close(tmp_path: Path) -> None:
    order: list[str] = []
    emitted: list[dict[str, Any]] = []
    core = _slow_closing_core(order, emitted)

    def errors() -> list[tuple[str, str]]:
        return [(e["command"], e["message"]) for e in emitted if e["type"] == "error"]

    # Asked for twice: the second call is not an error, it waits for the first.
    await core.start(make_spec(tmp_path))
    first = asyncio.ensure_future(core.close("s1"))
    await asyncio.sleep(0.05)
    await core.close("s1")
    assert order == ["opened s1", "closing s1", "closed s1"]
    await first
    assert errors() == []

    # The same while a shutdown is closing it.
    await core.start(make_spec(tmp_path))
    stopping = asyncio.ensure_future(core.shutdown())
    await asyncio.sleep(0.05)
    await core.close("s1")
    assert order[-1] == "closed s1" and order.count("closing s1") == 2
    await stopping
    assert errors() == []

    # Once it is gone, closing it again is the error it always was.
    await core.close("s1")
    assert errors() == [("close", 'unknown session "s1"')]


async def test_a_second_shutdown_does_not_reopen_the_door_for_the_first(tmp_path: Path) -> None:
    order: list[str] = []
    emitted: list[dict[str, Any]] = []
    core = _slow_closing_core(order, emitted)
    await core.start(make_spec(tmp_path, "old"))
    first = asyncio.ensure_future(core.shutdown())
    await asyncio.sleep(0.05)
    # A second call with a short limit comes back early ...
    await core.shutdown(timeout=0.01)
    assert not first.done()
    # ... while the first is still at work, so sessions are still refused.
    await core.start(make_spec(tmp_path, "new"))
    assert [(e["session_id"], e["message"]) for e in emitted if e["type"] == "error"] == [
        ("new", "the runtime is shutting down")
    ]
    # A further call without a limit waits for the close the first one began.
    await core.shutdown()
    assert order == ["opened old", "closing old", "closed old"]
    await first
    # With every call over, sessions are accepted again.
    await core.start(make_spec(tmp_path, "new"))
    assert core.session_ids == ["new"]
    await core.shutdown()


async def test_a_start_during_an_explicit_shutdown_is_refused_by_the_subprocess_runtime(
    tmp_path: Path,
) -> None:
    events = Recorder()
    runtime, pipe, feed = _wired(events)
    stopping = asyncio.ensure_future(runtime.shutdown())
    try:
        await asyncio.sleep(0.05)
        assert [message["type"] for message in pipe.sent] == ["shutdown"]
        # The child is still on its way out. A session asked for now is refused,
        # as in the in-process runtime: nothing is written to the leaving child
        # and the request does not sit waiting to start another one.
        await asyncio.wait_for(runtime.start(make_spec(tmp_path, "late")), 2)
        refused = [(e["session_id"], e["command"], e["message"]) for e in events.of("error")]
        assert refused == [("late", "start", "the runtime is shutting down")]
        assert len(pipe.sent) == 1
    finally:
        pipe.exited.set()  # the child has exited
        feed(None)  # and its pipe has closed
        await stopping
    assert runtime.pid is None


@pytest.mark.parametrize("give_up", ["cancel", "timeout"])
async def test_a_session_is_not_reopened_while_an_abandoned_shutdown_still_closes_it(
    tmp_path: Path, give_up: str
) -> None:
    order: list[str] = []
    gate = asyncio.Event()

    class Stub:
        busy = False

        def __init__(self, name: str) -> None:
            self.name = name

        async def close(self) -> None:
            order.append(f"closing {self.name}")
            await asyncio.sleep(0.3)
            order.append(f"closed {self.name}")

    async def open_session(spec, host, emit):  # noqa: ANN001, ANN202
        del host, emit
        if spec.session_id == "stuck":
            await gate.wait()
        order.append(f"opened {spec.session_id}")
        return Stub(spec.session_id)

    core = RuntimeCore(NoHost(), lambda event: None, open_session=open_session)
    await core.start(make_spec(tmp_path, "ready"))
    stuck = asyncio.ensure_future(core.start(make_spec(tmp_path, "stuck")))
    await asyncio.sleep(0)

    # The caller of the shutdown stops waiting while "ready" is still closing.
    if give_up == "cancel":
        stopping = asyncio.ensure_future(core.shutdown())
        await asyncio.sleep(0.05)
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
    else:
        await core.shutdown(timeout=0.05)
    assert order == ["opened ready", "closing ready"]

    # Started again right away, the session opens only once the old one has
    # finished closing: it would otherwise read a transcript still being written.
    await core.start(make_spec(tmp_path, "ready"))
    assert order == ["opened ready", "closing ready", "closed ready", "opened ready"]

    gate.set()
    await stuck
    await core.shutdown()
    assert core.session_ids == [] and core.idle


async def test_shutdown_waits_for_sessions_on_their_way_in_and_out(tmp_path: Path) -> None:
    names = ("leaving", "entering", "again")
    opener = _scripted_opener({name: ScriptedLLM([[say("unused"), finish()]]) for name in names})
    gate = asyncio.Event()

    async def open_session(spec, host, emit):  # noqa: ANN001, ANN202
        if spec.session_id == "entering":
            await gate.wait()
        session = await opener(spec, host, emit)
        if spec.session_id == "leaving":
            close = session.close

            async def slow_close() -> None:
                await asyncio.sleep(0.3)
                await close()

            session.close = slow_close  # type: ignore[method-assign]
        return session

    events = Recorder()
    runtime = InProcessRuntime(events, open_session=open_session)
    await runtime.start(make_spec(tmp_path, "leaving"))
    leaving = asyncio.ensure_future(runtime.close("leaving"))  # takes a while
    entering = asyncio.ensure_future(runtime.start(make_spec(tmp_path, "entering")))  # held at the gate
    await asyncio.sleep(0.05)
    stopping = asyncio.ensure_future(runtime.shutdown())
    await asyncio.sleep(0.05)
    # Neither session is in the table right now, yet the shutdown does not
    # return as if there were nothing to do.
    assert not stopping.done()
    # A start that arrives meanwhile is refused: nobody would close that session.
    await runtime.start(make_spec(tmp_path, "late"))
    assert [(e["session_id"], e["message"]) for e in events.of("error")] == [
        ("late", "the runtime is shutting down")
    ]
    gate.set()
    await asyncio.gather(stopping, leaving, entering)
    # On return both are gone: the one that was closing has finished, the one
    # that was being created exists and has been closed.
    closed = sorted(e["session_id"] for e in events.of("ended") if e["reason"] == "closed")
    assert closed == ["entering", "leaving"]
    assert runtime.core.session_ids == [] and runtime.core.idle

    # The runtime takes sessions again afterwards.
    await runtime.start(make_spec(tmp_path, "again"))
    assert runtime.core.session_ids == ["again"]
    await runtime.shutdown()
    assert runtime.core.session_ids == []


async def test_a_start_waits_for_the_same_session_to_finish_closing(tmp_path: Path) -> None:
    scripts = [ScriptedLLM([[say("old"), finish()]]), ScriptedLLM([[say("new"), finish()]])]

    async def open_session(spec, host, emit):  # noqa: ANN001, ANN202
        llm = scripts.pop(0)

        async def factory() -> ScriptedLLM:
            return llm

        session = await FamiliarSession.open(
            spec, host, emit, model=LLMClientProvider(spec.model, client_factory=factory)
        )
        close = session.close

        async def slow_close() -> None:
            await asyncio.sleep(0.3)
            await close()

        session.close = slow_close  # type: ignore[method-assign]
        return session

    events = Recorder()
    runtime = InProcessRuntime(events, open_session=open_session)
    try:
        await runtime.start(make_spec(tmp_path))
        await runtime.prompt("s1", "one")
        await events.wait_ended("s1")
        mark = len(events.events)
        # Close it, start it again and use it, without waiting in between.
        await asyncio.gather(
            runtime.close("s1"), runtime.start(make_spec(tmp_path)), runtime.prompt("s1", "two")
        )
        ended = await events.wait_for(
            lambda e: e["type"] == "ended" and e["reason"] == "completed", start=mark
        )
        assert ended["text"] == "new"
        after = [
            (event["type"], event.get("reason"), event.get("resumed"))
            for event in events.events[mark:]
            if event["type"] in ("started", "ended", "error")
        ]
        # The old session was gone, and its transcript complete, before the new one opened.
        assert after == [
            ("ended", "closed", None),
            ("started", None, True),
            ("ended", "completed", None),
        ]
    finally:
        await runtime.shutdown()


async def test_a_cancelled_waiting_command_does_not_cancel_the_others(tmp_path: Path) -> None:
    llm = ScriptedLLM([[say("one"), finish()]])
    opener = _scripted_opener({"s1": llm})
    release = asyncio.Event()

    async def gated_open(spec, host, emit):  # noqa: ANN001, ANN202
        await release.wait()
        return await opener(spec, host, emit)

    events = Recorder()
    runtime = InProcessRuntime(events, open_session=gated_open)
    try:
        starting = asyncio.ensure_future(runtime.start(make_spec(tmp_path)))
        impatient = asyncio.ensure_future(runtime.steer("s1", "never mind"))
        patient = asyncio.ensure_future(runtime.prompt("s1", "first"))
        await asyncio.sleep(0.05)
        # One caller gives up while the session is still being created.
        impatient.cancel()
        await asyncio.sleep(0)
        release.set()
        # The start and the other waiting command are untouched by that.
        await asyncio.gather(starting, patient)
        with pytest.raises(asyncio.CancelledError):
            await impatient
        ended = await events.wait_ended("s1")
        assert ended["text"] == "one"
        assert [request[-1]["content"] for request in llm.requests] == ["first"]
        assert events.of("error") == []
    finally:
        release.set()
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
