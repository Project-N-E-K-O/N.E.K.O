"""The JSON-RPC connection to an agent process, against a few lines of agent."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.dsh.acp import AcpClosed, AcpConnection, AcpError

pytestmark = pytest.mark.integration_serial

AGENT = r'''
import json, sys, time

# The client writes UTF-8 whatever the locale is (cp1252 on a Windows runner).
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()

sys.stderr.write("agent up\n")
sys.stderr.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    message = json.loads(line)
    method = message.get("method")
    if method == "echo":
        send({"jsonrpc": "2.0", "id": message["id"], "result": message["params"]})
    elif method == "fail":
        send({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": "Internal error", "data": {"details": "turn failed: no model"}}})
    elif method == "ask":
        # Say something, then ask; the answer comes back as this request's result.
        send({"jsonrpc": "2.0", "method": "note", "params": {"n": 1}})
        send({"jsonrpc": "2.0", "method": "note", "params": {"n": 2}})
        send({"jsonrpc": "2.0", "id": "q1", "method": message["params"]["what"], "params": {"about": "it"}})
        answer = json.loads(sys.stdin.readline())
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"client_said": answer}})
    elif method == "garbage":
        sys.stdout.write("this is not json\n\n")
        sys.stdout.flush()
        send({"jsonrpc": "2.0", "id": message["id"], "result": "after garbage"})
    elif method == "silent":
        pass
    elif method == "exit":
        sys.exit(int(message["params"]["code"]))
    elif method == "said":
        open(message["params"]["path"], "a").write(json.dumps(message["params"]) + "\n")
    elif method == "leave":
        # Start something that outlives the agent, in its process group; holding its output open, or not.
        import subprocess
        params = message["params"]
        left = subprocess.Popen(
            [sys.executable, "-c", f"import time; time.sleep({params['seconds']})"],
            stdin=subprocess.DEVNULL,
            stdout=None if params["keep_output"] else subprocess.DEVNULL,
        )
        send({"jsonrpc": "2.0", "id": message["id"], "result": left.pid})
if "--linger" in sys.argv:
    time.sleep(60)
'''


class Client:
    def __init__(self) -> None:
        self.notes: list[tuple[str, dict[str, Any]]] = []
        self.asked: list[tuple[str, dict[str, Any], int]] = []
        self.errors: list[str] = []
        self.closed = 0
        self.answer: Any = {"ok": True}
        self.connection = AcpConnection(
            on_notification=lambda method, params: self.notes.append((method, params)),
            on_request=self._request,
            on_stderr=self.errors.append,
            on_closed=self._closed,
        )

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        # How many notifications had been handed over when the request was answered.
        self.asked.append((method, params, len(self.notes)))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def _closed(self) -> None:
        self.closed += 1


@pytest.fixture
async def agent(tmp_path: Path):
    script = tmp_path / "agent.py"
    script.write_text(AGENT, encoding="utf-8")
    clients: list[Client] = []

    async def start(*arguments: str) -> Client:
        client = Client()
        await client.connection.open([sys.executable, str(script), *arguments], cwd=str(tmp_path), env=dict(os.environ))
        clients.append(client)
        return client

    yield start
    for client in clients:
        await client.connection.close(5.0)


async def test_requests_are_answered_and_errors_keep_their_details(agent) -> None:
    client = await agent()

    assert await client.connection.request("echo", {"text": "héllo 你好"}) == {"text": "héllo 你好"}
    with pytest.raises(AcpError) as failure:
        await client.connection.request("fail", {})

    assert failure.value.code == -32603 and failure.value.detail == "Internal error: turn failed: no model"
    assert client.connection.alive and client.connection.pid is not None
    # Lines that are not JSON, and empty ones, are skipped.
    assert await client.connection.request("garbage", {}) == "after garbage"


async def test_what_the_agent_said_before_it_asked_is_known_when_it_is_answered(agent) -> None:
    client = await agent()
    client.answer = {"outcome": "allowed"}

    result = await client.connection.request("ask", {"what": "session/request_permission"})

    assert client.notes == [("note", {"n": 1}), ("note", {"n": 2})]
    assert client.asked == [("session/request_permission", {"about": "it"}, 2)]
    assert result["client_said"] == {"jsonrpc": "2.0", "id": "q1", "result": {"outcome": "allowed"}}


async def test_a_request_the_client_cannot_answer_is_answered_with_an_error(agent) -> None:
    client = await agent()
    client.answer = AcpError(-32601, "fs/read is not supported")
    refused = await client.connection.request("ask", {"what": "fs/read"})
    assert refused["client_said"]["error"] == {"code": -32601, "message": "fs/read is not supported"}

    client.answer = RuntimeError("boom")
    broken = await client.connection.request("ask", {"what": "anything"})
    assert broken["client_said"]["error"]["code"] == -32603 and "boom" in broken["client_said"]["error"]["message"]
    # None is an empty result.
    client.answer = None
    assert (await client.connection.request("ask", {"what": "anything"}))["client_said"]["result"] == {}


async def test_a_request_that_gets_no_answer_times_out_and_a_late_answer_is_dropped(agent) -> None:
    client = await agent()
    with pytest.raises(asyncio.TimeoutError):
        await client.connection.request("silent", {}, timeout=0.2)
    assert await client.connection.request("echo", {"still": "works"}) == {"still": "works"}


async def test_notifications_reach_the_agent(agent, tmp_path: Path) -> None:
    client = await agent()
    target = tmp_path / "said.jsonl"
    client.connection.notify("said", {"path": str(target), "word": "one"})
    client.connection.notify("said", {"path": str(target), "word": "two"})
    await client.connection.request("echo", {})
    assert [line.count("one") + 2 * line.count("two") for line in target.read_text().splitlines()] == [1, 2]


POSIX_ONLY = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX's")


def _running(pid: int) -> bool:
    """POSIX: whether a process runs (a zombie, killed and not yet collected, does not)."""
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


async def _until_gone(pid: int) -> bool:
    for _attempt in range(250):
        if not _running(pid):
            return True
        await asyncio.sleep(0.02)
    return False


@POSIX_ONLY
@pytest.mark.parametrize("keep_output", [True, False])
async def test_an_agent_that_exits_takes_what_it_left_in_its_group_with_it(agent, keep_output: bool) -> None:
    client = await agent()
    # Holding the agent's output open, or not: its end does not wait for that.
    left = await client.connection.request("leave", {"seconds": 60, "keep_output": keep_output})
    assert _running(left)

    client.connection.notify("exit", {"code": 0})

    assert await _until_gone(left)


@POSIX_ONLY
@pytest.mark.parametrize("noticed_by", ["returncode", "close"])
async def test_what_an_agent_left_is_ended_whoever_notices_its_exit_first(
    agent, monkeypatch: pytest.MonkeyPatch, noticed_by: str
) -> None:
    import psutil

    from nekoclaw.dsh import acp

    # Not the thread that waits for the exit: something else gets there first.
    monkeypatch.setattr(acp, "_end_group_once_gone", lambda process: None)
    killed_while_its_own: list[bool] = []
    kill_group = os.killpg

    def watched(group: int, signal: int) -> None:
        # The group's id is the agent's own until its exit is collected: a zombie, still listed.
        try:
            killed_while_its_own.append(psutil.Process(group).status() == psutil.STATUS_ZOMBIE)
        except psutil.NoSuchProcess:
            killed_while_its_own.append(False)
        kill_group(group, signal)

    monkeypatch.setattr(os, "killpg", watched)
    client = await agent()
    left = await client.connection.request("leave", {"seconds": 60, "keep_output": False})

    client.connection.notify("exit", {"code": 3})
    if noticed_by == "returncode":
        for _attempt in range(250):
            if client.connection.returncode is not None:
                break
            await asyncio.sleep(0.02)
        assert client.connection.returncode == 3
    else:
        await asyncio.sleep(0.3)
        await client.connection.close(5.0)

    assert await _until_gone(left)
    assert killed_while_its_own == [True]


@POSIX_ONLY
async def test_an_agent_that_will_not_exit_is_killed_while_its_exit_is_watched() -> None:
    import subprocess
    import threading

    from nekoclaw.dsh import acp

    process = await asyncio.to_thread(
        subprocess.Popen, [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    # The thread a connection starts to see the agent's end: it does not stand in the way of killing it.
    threading.Thread(target=acp._end_group_once_gone, args=(process,), daemon=True).start()
    assert not await asyncio.to_thread(acp._wait, process, 0.5)

    await asyncio.wait_for(asyncio.to_thread(acp.kill_tree, process), 10)

    assert await asyncio.to_thread(acp._wait, process, 5.0)


@POSIX_ONLY
async def test_killing_an_agent_that_is_waited_for_elsewhere_does_not_wait_for_ever(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import threading

    from nekoclaw.dsh import acp

    monkeypatch.setattr(acp, "_GROUP_LOCK_SECONDS", 0.3)
    process = await asyncio.to_thread(
        subprocess.Popen, [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    # ``Popen.wait()`` without a time limit, from elsewhere: it holds Popen's lock till the process ends.
    waiting = threading.Thread(target=process.wait, daemon=True)
    waiting.start()
    await asyncio.sleep(0.1)

    killing = asyncio.ensure_future(asyncio.to_thread(acp.kill_tree, process))
    await asyncio.wait_for(killing, 10)

    await asyncio.to_thread(waiting.join, 10)
    assert process.returncode is not None


@POSIX_ONLY
@pytest.mark.parametrize("collected", [True, False])
async def test_the_group_of_a_process_is_killed_only_while_its_id_is_still_its_own(
    monkeypatch: pytest.MonkeyPatch, collected: bool
) -> None:
    import subprocess

    from nekoclaw.dsh import acp

    process = await asyncio.to_thread(subprocess.Popen, [sys.executable, "-c", "pass"], start_new_session=True)
    for _attempt in range(250):
        if acp._exited(process):
            break
        await asyncio.sleep(0.02)
    assert acp._exited(process)
    if collected:
        process.wait()
    killed: list[int] = []
    monkeypatch.setattr(os, "killpg", lambda group, signal: killed.append(group))

    acp._end_group(process)

    # Collected, its id may be another process's by now; until then it is still its own.
    assert killed == ([] if collected else [process.pid])
    process.wait()


async def test_an_agent_that_exits_fails_what_was_waiting_and_is_reported_once(agent) -> None:
    client = await agent()
    waiting = asyncio.ensure_future(client.connection.request("silent", {}))
    await asyncio.sleep(0.1)

    client.connection.notify("exit", {"code": 3})
    with pytest.raises(AcpClosed):
        await asyncio.wait_for(waiting, 10)
    for _attempt in range(100):
        if client.connection.returncode is not None:
            break
        await asyncio.sleep(0.02)

    assert client.closed == 1 and not client.connection.alive and client.connection.returncode == 3
    with pytest.raises(AcpClosed):
        await client.connection.request("echo", {})
    client.connection.notify("said", {})  # dropped, not an error
    assert "agent up" in client.connection.stderr_tail and client.errors == ["agent up"]


async def test_closing_ends_an_agent_that_leaves_when_its_input_ends(agent) -> None:
    client = await agent()
    await client.connection.request("echo", {})
    await client.connection.close(10.0)
    assert client.connection.returncode == 0 and client.closed == 1
    await client.connection.close(1.0)  # again: nothing to do
    assert client.closed == 1


async def test_an_agent_that_does_not_leave_is_killed(agent) -> None:
    client = await agent("--linger")
    await client.connection.request("echo", {})
    pid = client.connection.pid
    await client.connection.close(0.3)
    import psutil

    assert client.connection.returncode is not None and client.closed == 1
    assert pid is not None and not psutil.pid_exists(pid)


async def test_a_start_that_is_cancelled_ends_the_process_it_started(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    script = tmp_path / "agent.py"
    script.write_text(AGENT, encoding="utf-8")
    spawned: list[Any] = []
    spawning = threading.Event()
    spawn = AcpConnection._spawn

    def slow(argv: list[str], cwd: str, env: dict[str, str]) -> Any:
        spawning.set()
        time.sleep(0.3)
        spawned.append(spawn(argv, cwd, env))
        return spawned[-1]

    monkeypatch.setattr(AcpConnection, "_spawn", staticmethod(slow))
    client = Client()
    opening = asyncio.ensure_future(
        client.connection.open([sys.executable, str(script), "--linger"], cwd=str(tmp_path), env=dict(os.environ))
    )
    while not spawning.is_set():
        await asyncio.sleep(0.01)

    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening

    # The agent was started after all, and is ended rather than left running for nobody.
    (process,) = spawned
    assert process.poll() is not None
    assert client.connection.pid is None
    await client.connection.close(0.5)


async def test_a_program_that_does_not_exist_raises(tmp_path: Path) -> None:
    client = Client()
    with pytest.raises(OSError):
        await client.connection.open([str(tmp_path / "no-such-program")], cwd=str(tmp_path), env=dict(os.environ))
    with pytest.raises(AcpClosed):
        await client.connection.request("echo", {})
    await client.connection.close(1.0)
