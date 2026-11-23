"""The command tool and background jobs, with real shell processes in a temp directory.

Covers: UTF-8 output, exit markers, live progress, timeout turning into a
background job, killing a whole process tree, the completion notice, and
output kept to its tail with the full text on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from nekopower.runtime.host import HostCallbacks
from nekopower.runtime.tools import procs, shell
from nekopower.runtime.tools.jobs import JobRegistry, completion_notice, render_delta, status_line
from tests.unit.nekopower.fakes import ScriptedLLM, allow_all, call, finish, make_spec, open_session, say

pytestmark = pytest.mark.integration_serial

WINDOWS = sys.platform == "win32"
SHELL = shell.shell_kind(sys.platform)

if not WINDOWS and shutil.which("bash") is None:  # pragma: no cover - CI images have bash
    pytestmark = [pytest.mark.integration_serial, pytest.mark.skip(reason="bash is not installed")]


def _python(script: Path, *args: str) -> str:
    """A shell command that runs ``script`` with this interpreter."""
    rendered = " ".join(f"'{arg}'" for arg in (str(script), *args))
    if WINDOWS:
        return f"& '{sys.executable}' {rendered}"
    return f"'{sys.executable}' {rendered}"


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _host() -> HostCallbacks:
    # Running a command needs approval by default; these tests approve everything.
    return HostCallbacks(decide_permission=allow_all())


async def _run_tool_call(tmp_path: Path, arguments: dict, **spec_overrides):  # noqa: ANN003, ANN202
    """Run one command-tool call in a session and return (session, events, result event)."""
    llm = ScriptedLLM([[call(SHELL, arguments), finish("tool_calls")], [say("done"), finish()]])
    session, events = await open_session(make_spec(tmp_path, **spec_overrides), llm, _host())
    session.prompt("run it")
    result = await events.wait_for(lambda event: event["type"] == "tool_end", timeout=60)
    return session, events, result, llm


async def _wait_gone(pid: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not psutil.pid_exists(pid):
            return True
        try:
            if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            return True
        await asyncio.sleep(0.1)
    return False


async def test_command_output_is_utf8_and_a_failure_carries_its_exit_code(tmp_path: Path) -> None:
    script = _write(
        tmp_path,
        "emit.py",
        "import sys\n"
        "sys.stdout.reconfigure(encoding='utf-8')\n"
        "sys.stderr.reconfigure(encoding='utf-8')\n"
        "print('中文输出 ok')\n"
        "print('warning line', file=sys.stderr)\n"
        "sys.exit(3)\n",
    )
    session, events, result, _llm = await _run_tool_call(
        tmp_path, {"description": "Emit text and fail", "command": _python(script)}
    )
    try:
        assert result["is_error"] is False
        # `pwsh -Command` reports any failing native command as exit code 1;
        # bash passes the program's own code through.
        code = 1 if WINDOWS else 3
        assert result["text"] == f"中文输出 ok\n[stderr]\nwarning line\n[exit code: {code}]"
        await events.wait_ended()
    finally:
        await session.close()


async def test_successful_command_has_no_marker_and_empty_output_is_stated(tmp_path: Path) -> None:
    script = _write(tmp_path, "quiet.py", "pass\n")
    session, events, result, _llm = await _run_tool_call(
        tmp_path, {"description": "Run a quiet script", "command": _python(script)}
    )
    try:
        assert result["text"] == "(no output)"
        await events.wait_ended()
    finally:
        await session.close()


async def test_progress_arrives_while_the_command_is_still_running(tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    script = _write(
        tmp_path,
        "staged.py",
        "import os, sys, time\n"
        "print('stage one', flush=True)\n"
        "deadline = time.time() + 40\n"
        "while not os.path.exists(sys.argv[1]) and time.time() < deadline:\n"
        "    time.sleep(0.05)\n"
        "print('stage two', flush=True)\n",
    )
    llm = ScriptedLLM(
        [
            [call(SHELL, {"description": "Run in two stages", "command": _python(script, str(gate))}), finish("tool_calls")],
            [say("done"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    try:
        session.prompt("run it")
        update = await events.wait_for(
            lambda event: event["type"] == "tool_update" and "stage one" in event["text"], timeout=60
        )
        # The command is blocked on the gate file, so it cannot have finished:
        # the update really was delivered mid-run.
        assert not events.of("tool_end")
        assert update["tool_name"] == SHELL
        gate.write_text("go", encoding="utf-8")
        result = await events.wait_for(lambda event: event["type"] == "tool_end", timeout=60)
        assert result["text"] == "stage one\nstage two\n"
        await events.wait_ended()
    finally:
        await session.close()


async def test_timeout_moves_the_command_to_the_background_and_kill_stops_the_tree(tmp_path: Path) -> None:
    child = _write(tmp_path, "child.py", "import time\ntime.sleep(120)\n")
    parent = _write(
        tmp_path,
        "parent.py",
        "import os, subprocess, sys, time\n"
        "grandchild = subprocess.Popen([sys.executable, sys.argv[1]])\n"
        "print(f'pids {os.getpid()} {grandchild.pid}', flush=True)\n"
        "time.sleep(120)\n",
    )
    llm = ScriptedLLM(
        [
            [
                call(
                    SHELL,
                    {
                        "description": "Start a long task",
                        "command": _python(parent, str(child)),
                        "timeoutMs": 4000,
                    },
                ),
                finish("tool_calls"),
            ],
            [call("job_list", {}, "c2"), finish("tool_calls")],
            [call("job_kill", {"job_id": "job-1", "reason": "not needed"}, "c3"), finish("tool_calls")],
            [call("job_output", {"job_id": "job-1", "wait": True, "timeout_ms": 30000}, "c4"), finish("tool_calls")],
            [say("stopped"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    try:
        session.prompt("run it")
        ended = await events.wait_ended(timeout=90)
        assert ended["reason"] == "completed"
        promoted, listed, killed, collected = (event["text"] for event in events.of("tool_end"))

        # The call returned at its timeout with the job id; nothing was killed.
        assert promoted.endswith("[still running after 4000ms; moved to background job job-1]")
        first_line = promoted.splitlines()[0]
        assert first_line.startswith("pids ")
        python_pid, grandchild_pid = (int(part) for part in first_line.split()[1:3])
        assert listed.startswith(f"job-1 [{SHELL}] running — ")
        assert killed == "requested cancellation of job job-1"
        assert "[status: killed" in collected

        # The shell, the script it ran and the process that script started are gone.
        assert await _wait_gone(python_pid)
        assert await _wait_gone(grandchild_pid)
        # The model stopped the job itself, so no completion notice follows.
        assert not any(
            str(message.get("content")).startswith("background job job-1 (")
            for request in llm.requests
            for message in request
        )
    finally:
        await session.close()


async def test_abort_kills_a_running_foreground_command_tree(tmp_path: Path) -> None:
    child = _write(tmp_path, "child.py", "import time\ntime.sleep(120)\n")
    parent = _write(
        tmp_path,
        "parent.py",
        "import os, subprocess, sys, time\n"
        "grandchild = subprocess.Popen([sys.executable, sys.argv[1]])\n"
        "print(f'pids {os.getpid()} {grandchild.pid}', flush=True)\n"
        "time.sleep(120)\n",
    )
    llm = ScriptedLLM(
        [[call(SHELL, {"description": "Start a long task", "command": _python(parent, str(child))}), finish("tool_calls")]]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    try:
        session.prompt("run it")
        update = await events.wait_for(
            lambda event: event["type"] == "tool_update" and "pids" in event["text"], timeout=60
        )
        python_pid, grandchild_pid = (int(part) for part in update["text"].split()[1:3])
        session.abort()
        ended = await events.wait_ended(timeout=60)
        assert ended["reason"] == "aborted"
        assert await _wait_gone(python_pid)
        assert await _wait_gone(grandchild_pid)
        assert session.jobs.visible() == []
    finally:
        await session.close()


async def test_abort_on_an_idle_session_stops_its_background_jobs_for_good(tmp_path: Path) -> None:
    script = _write(
        tmp_path,
        "server.py",
        "import os, time\nprint(f'pid {os.getpid()}', flush=True)\ntime.sleep(120)\n",
    )
    llm = ScriptedLLM(
        [
            [call(SHELL, {"description": "Serve", "command": _python(script), "run_in_background": True}), finish("tool_calls")],
            [say("It is running in the background."), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    try:
        session.prompt("start the server")
        await events.wait_ended(timeout=60)
        # The run's end says that a job is still running: a session with one is not closed to rest.
        assert events.of("ended")[0]["jobs"] == 1
        job = session.jobs.get("job-1")
        # And that its end will start the next run. One that is being stopped by whoever
        # knows about it does not: nothing follows its end, so it keeps no session open.
        assert events.of("ended")[0]["waking_jobs"] == 1 and session.jobs.waking() == [job]
        job.notice_suppressed = True
        assert session.jobs.waking() == [] and session.jobs.running() == [job]
        job.notice_suppressed = False
        for _ in range(100):
            if "pid" in job.process.stdout.read_from(0)[0]:
                break
            await asyncio.sleep(0.1)
        pid = int(job.process.stdout.read_from(0)[0].split()[1])
        assert session.busy and not session.running

        # Nothing is running in the foreground, yet there is something to stop.
        assert session.abort() is True
        assert await _wait_gone(pid)
        await asyncio.wait_for(job.settled.wait(), 30)
        await asyncio.sleep(0.5)
        # The stopped job does not wake the session with a completion notice.
        assert len(llm.requests) == 2 and not session.running
        assert len(events.of("ended")) == 1
        assert not session.busy
        assert session.abort() is False
    finally:
        await session.close()


async def test_abort_during_process_creation_stops_the_new_background_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _write(tmp_path, "linger.py", "import time\ntime.sleep(120)\n")
    llm = ScriptedLLM(
        [
            [call(SHELL, {"description": "Serve", "command": _python(script), "run_in_background": True}), finish("tool_calls")],
            [say("unreachable"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    created: list[procs.ShellProcess] = []
    real_spawn = procs.spawn

    async def spawn_then_abort(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        process = await real_spawn(*args, **kwargs)
        created.append(process)
        # The abort lands while the process is being created: it finds no job
        # to stop, because this one is not registered yet.
        assert session.abort() is True
        return process

    monkeypatch.setattr(procs, "spawn", spawn_then_abort)
    try:
        session.prompt("start the server")
        ended = await events.wait_ended(timeout=60)
        assert ended["reason"] == "aborted"
        assert len(created) == 1
        process = created[0]
        await asyncio.wait_for(process.wait(), 30)
        assert process.kill_requested
        assert await _wait_gone(process.pid)
        assert session.jobs.visible() == [] and not session.busy
        await asyncio.sleep(0.5)
        # Nothing wakes the stopped session afterwards.
        assert len(llm.requests) == 1 and len(events.of("ended")) == 1
    finally:
        for process in created:
            process.kill()
        await session.close()


async def test_a_cancelled_spawn_does_not_leave_a_process_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _write(tmp_path, "linger.py", "import time\ntime.sleep(120)\n")
    created: list[procs.ShellProcess] = []
    real_blocking = procs._spawn_blocking

    def slow_spawn(*args):  # noqa: ANN002, ANN202
        time.sleep(0.5)  # the awaiting call is cancelled during this
        process = real_blocking(*args)
        created.append(process)
        return process

    monkeypatch.setattr(procs, "_spawn_blocking", slow_spawn)
    task = asyncio.ensure_future(
        procs.spawn(
            shell.shell_argv(SHELL, _python(script)),
            cwd=str(tmp_path),
            env=dict(os.environ),
            stdout=procs.OutputBuffer(1000, lambda: None),
            stderr=procs.OutputBuffer(1000, lambda: None),
        )
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        for _ in range(100):
            if created:
                break
            await asyncio.sleep(0.1)
        assert len(created) == 1
        process = created[0]
        # Nobody owns the process any more, so it is killed once it exists.
        await asyncio.wait_for(process.wait(), 30)
        assert process.kill_requested
        assert await _wait_gone(process.pid)
    finally:
        for process in created:
            process.kill()


@pytest.mark.parametrize("managed", [False, True])
async def test_background_job_completion_is_reported_to_the_session(tmp_path: Path, managed: bool) -> None:
    script = _write(tmp_path, "brief.py", "import time\ntime.sleep(1.5)\nprint('all built')\n")
    llm = ScriptedLLM(
        [
            [
                call(
                    SHELL,
                    {"description": "Build in background", "command": _python(script), "run_in_background": True},
                ),
                finish("tool_calls"),
            ],
            [say("Started; nothing else to do right now."), finish()],
            [say("The build finished."), finish()],
        ]
    )
    async def allow(_, payload):
        return {"allow": True, "receipt": "command"}
    host = HostCallbacks(execution_action=allow) if managed else _host()
    session, events = await open_session(make_spec(tmp_path), llm, host)
    try:
        session.prompt("build")
        assert (await events.wait_for(lambda e: e["type"] == "tool_end", timeout=60))["text"] == (
            "started background job job-1"
        )
        final = await events.wait_for(
            lambda event: event["type"] == "ended" and event["text"] == "The build finished.", timeout=60
        )
        assert final["reason"] == "completed"
        notice = llm.requests[2][-1]
        assert notice["role"] == "user"
        assert notice["content"].startswith(f"background job job-1 ({SHELL}: ")
        assert notice["content"].endswith("finished [status: completed, exit code: 0]. Output:\nall built")
        if managed:
            from nekopower.runtime.session import TRANSCRIPT_FILE
            await session.flush()
            assert sum(m.get("content") == notice["content"] for m in llm.requests[2]) == 1
            rows = [json.loads(s) for s in (session._session_dir / TRANSCRIPT_FILE).read_text(encoding="utf-8").splitlines()]
            assert [row["content"] for row in rows if row.get("custom_type") == "notice:job"] == [notice["content"]]
            assert all(row.get("custom_type") != "notice:pending_job" for row in rows)
    finally:
        await session.close()


async def test_collecting_a_finished_job_withdraws_its_pending_notice(tmp_path: Path) -> None:
    script = _write(tmp_path, "quick.py", "print('artifact ready')\n")
    holder: dict = {}

    async def until_job_settled() -> None:
        job = holder["session"].jobs.get("job-1")
        await asyncio.wait_for(job.settled.wait(), 60)
        # The notice is queued for the next step at this point.
        assert "job-1" in holder["session"].jobs.pending_notices

    llm = ScriptedLLM(
        [
            [call(SHELL, {"description": "Build", "command": _python(script), "run_in_background": True}), finish("tool_calls")],
            [until_job_settled, call("job_output", {"job_id": "job-1"}, "c2"), finish("tool_calls")],
            [say("collected"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    holder["session"] = session
    try:
        session.prompt("build and collect")
        await events.wait_ended(timeout=90)
        collected = events.of("tool_end")[1]["text"]
        # The read carries the output the notice would have carried ...
        assert collected == "artifact ready\n[status: completed, exit code: 0]"
        # ... and the notice itself never reaches the model: nothing is said twice.
        assert not any(
            str(message.get("content")).startswith("background job job-1 (")
            for request in llm.requests
            for message in request
        )
        assert len(llm.requests) == 3
    finally:
        await session.close()


async def test_a_blocking_job_read_yields_to_the_user(tmp_path: Path) -> None:
    script = _write(tmp_path, "slow.py", "import time\ntime.sleep(120)\n")
    llm = ScriptedLLM(
        [
            [call(SHELL, {"description": "Long task", "command": _python(script), "run_in_background": True}), finish("tool_calls")],
            [call("job_output", {"job_id": "job-1", "wait": True, "timeout_ms": 600000}, "c2"), finish("tool_calls")],
            [call("job_kill", {"job_id": "job-1"}, "c3"), finish("tool_calls")],
            [say("stopped waiting"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    try:
        session.prompt("start and wait")
        await events.wait_for(
            lambda event: event["type"] == "tool_start" and event["tool_name"] == "job_output", timeout=60
        )
        await asyncio.sleep(0.3)
        assert len(events.of("tool_end")) == 1  # the read is blocked on the job
        session.steer("Stop waiting, I need something else.")
        await events.wait_ended(timeout=60)
        waited = events.of("tool_end")[1]["text"]
        # The wait ended without killing the job and says why.
        assert waited == "(no new output)\n[status: running, user steered]"
        assert llm.requests[2][-1] == {"role": "user", "content": "Stop waiting, I need something else."}
    finally:
        await session.close()


async def test_long_output_keeps_its_tail_and_the_full_text_is_saved(tmp_path: Path) -> None:
    script = _write(
        tmp_path,
        "loud.py",
        "for number in range(20000):\n    print(f'line {number:05d} ' + 'x' * 20)\n",
    )
    session, events, result, _llm = await _run_tool_call(
        tmp_path, {"description": "Print a lot", "command": _python(script)}
    )
    try:
        text = result["text"]
        assert "line 19999" in text
        assert "line 00000" not in text
        marker = [line for line in text.splitlines() if line.startswith("[output truncated; full output: ")]
        assert len(marker) == 1
        saved = Path(marker[0][len("[output truncated; full output: "):-1])
        assert saved.is_file()
        assert Path(make_spec(tmp_path).session_dir) in saved.parents
        full = saved.read_text(encoding="utf-8")
        assert "line 00000" in full and "line 19999" in full
        await events.wait_ended()
    finally:
        await session.close()


async def test_output_saved_before_a_restart_survives_the_next_long_command(tmp_path: Path) -> None:
    script = _write(
        tmp_path,
        "loud.py",
        "import sys\n"
        "for number in range(20000):\n"
        "    print(f'{sys.argv[1]} {number:05d} ' + 'x' * 20)\n",
    )
    prefix = "[output truncated; full output: "

    async def run_once(tag: str) -> Path:
        session, events, result, _llm = await _run_tool_call(
            tmp_path, {"description": "Print a lot", "command": _python(script, tag)}
        )
        try:
            marker = [line for line in result["text"].splitlines() if line.startswith(prefix)]
            assert len(marker) == 1
            await events.wait_ended()
            return Path(marker[0][len(prefix):-1])
        finally:
            await session.close()

    first = await run_once("first")
    before = first.read_bytes()
    assert b"first 00000" in before
    # The same session directory again: this session resumes the transcript
    # that still points at the file saved above.
    second = await run_once("second")
    assert second != first
    assert first.read_bytes() == before
    assert b"second 00000" in second.read_bytes()


async def test_invalid_command_arguments_are_rejected_with_the_stable_messages(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            [
                call(SHELL, {"description": "x", "command": "   "}, "c1", index=0),
                call(SHELL, {"description": " ", "command": "echo hi"}, "c2", index=1),
                call(SHELL, {"description": "x", "command": "echo hi", "timeoutMs": -5}, "c3", index=2),
                call(SHELL, {"description": "x", "command": "echo hi", "workdir": "no-such-dir"}, "c4", index=3),
                finish("tool_calls"),
            ],
            [say("done"), finish()],
        ]
    )
    session, events = await open_session(make_spec(tmp_path), llm, _host())
    try:
        session.prompt("run")
        await events.wait_ended(timeout=60)
        texts = [event["text"] for event in events.of("tool_end")]
        assert texts[0] == "Error: invalid command: expected a non-empty string"
        assert texts[1] == "Error: invalid description: expected a non-empty string"
        assert texts[2] == "Error: invalid timeoutMs: expected a positive number, got -5"
        assert texts[3].startswith("Error: invalid workdir: ") and texts[3].endswith("is not a directory")
    finally:
        await session.close()


def test_the_work_is_done_on_the_spot_when_no_thread_can_be_started() -> None:
    done: list[str] = []

    class NoThreads(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    procs._start_thread_or(NoThreads(target=lambda: done.append("thread")), lambda: done.append("here"))
    assert done == ["here"]
    # With a thread to be had, the work goes to the thread and nothing is done twice.
    worker = threading.Thread(target=lambda: done.append("thread"))
    procs._start_thread_or(worker, lambda: done.append("unexpected"))
    worker.join(5)
    assert done == ["here", "thread"]


@pytest.mark.skipif(not WINDOWS, reason="the taskkill fallback exists on Windows only")
async def test_the_tree_is_killed_even_when_no_thread_can_be_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child = _write(tmp_path, "child.py", "import time\ntime.sleep(120)\n")
    parent = _write(
        tmp_path,
        "parent.py",
        "import os, subprocess, sys, time\n"
        "grandchild = subprocess.Popen([sys.executable, sys.argv[1]])\n"
        "print(f'pids {os.getpid()} {grandchild.pid}', flush=True)\n"
        "time.sleep(120)\n",
    )
    process = await procs.spawn(
        shell.shell_argv(SHELL, _python(parent, str(child))),
        cwd=str(tmp_path),
        env=dict(os.environ),
        stdout=procs.OutputBuffer(1000, lambda: None),
        stderr=procs.OutputBuffer(1000, lambda: None),
    )
    job = process._job
    try:
        for _ in range(300):
            if "pids" in process.stdout.read_from(0)[0]:
                break
            await asyncio.sleep(0.1)
        python_pid, grandchild_pid = (
            int(part) for part in process.stdout.read_from(0)[0].split()[1:3]
        )
        # As if the job object could not be used and no thread were left for
        # the fallback either.
        process._job = None
        start = threading.Thread.start

        def no_thread_for_taskkill(self: threading.Thread) -> None:
            if self.name == "nekopower-taskkill":
                raise RuntimeError("can't start new thread")
            start(self)

        monkeypatch.setattr(threading.Thread, "start", no_thread_for_taskkill)
        assert process.kill() is True
        await asyncio.wait_for(process.wait(), 30)
        assert await _wait_gone(python_pid)
        assert await _wait_gone(grandchild_pid)
    finally:
        process._job = job
        if job is not None:
            job.terminate()
            job.close()


@pytest.mark.skipif(not WINDOWS, reason="the taskkill fallback exists on Windows only")
async def test_a_kill_that_could_not_be_sent_can_be_tried_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _write(tmp_path, "linger.py", "import time\ntime.sleep(120)\n")
    process = await procs.spawn(
        shell.shell_argv(SHELL, _python(script)),
        cwd=str(tmp_path),
        env=dict(os.environ),
        stdout=procs.OutputBuffer(1000, lambda: None),
        stderr=procs.OutputBuffer(1000, lambda: None),
    )
    job = process._job
    attempts: list[int] = []

    def taskkill_cannot_run(pid: int) -> bool:
        attempts.append(pid)
        return False

    try:
        # As if the job object could not be used and taskkill could not be run.
        process._job = None
        monkeypatch.setattr(procs, "_taskkill_tree", taskkill_cannot_run)
        assert process.kill() is True
        for _ in range(100):
            if attempts and not process.kill_requested:
                break
            await asyncio.sleep(0.05)
        assert attempts == [process.pid]
        # Nothing was sent, so the process is not marked as being killed and a
        # later call tries again instead of answering "already done".
        assert process.kill_requested is False
        assert process.kill() is True
        for _ in range(100):
            if len(attempts) == 2:
                break
            await asyncio.sleep(0.05)
        assert attempts == [process.pid, process.pid]
    finally:
        process._job = job
        if job is not None:
            job.terminate()
            job.close()
    await asyncio.wait_for(process.wait(), 30)


def test_output_buffer_keeps_a_tail_and_spills_everything(tmp_path: Path) -> None:
    target = tmp_path / "spill.log"
    buffer = procs.OutputBuffer(10, lambda: target)
    buffer.feed(b"0123456789")
    assert buffer.read_from(0) == ("0123456789", 10, False)
    assert buffer.spill_path is None
    buffer.feed(b"abcde")
    text, next_offset, lossy = buffer.read_from(0)
    assert (text, next_offset, lossy) == ("56789abcde", 15, True)
    assert buffer.read_from(12) == ("cde", 15, False)
    buffer.close()
    assert target.read_bytes() == b"0123456789abcde"


def test_output_buffer_does_not_split_a_character_between_reads(tmp_path: Path) -> None:
    buffer = procs.OutputBuffer(1000, lambda: tmp_path / "unused.log")
    encoded = "中文".encode("utf-8")
    buffer.feed(encoded[:4])
    text, offset, _lossy = buffer.read_from(0)
    assert (text, offset) == ("中", 3)
    buffer.feed(encoded[4:])
    assert buffer.read_from(offset)[0] == "文"


def test_powershell_candidates_prefer_version_seven() -> None:
    env = {"ProgramFiles": "C:\\PF", "SystemRoot": "C:\\Win", "PATH": 'C:\\tools;"C:\\quoted dir"'}
    candidates = shell.candidate_powershell_paths(env)
    assert candidates[0].endswith("PowerShell\\7\\pwsh.exe") or candidates[0].endswith("PowerShell/7/pwsh.exe")
    assert "quoted dir" in candidates[2] and '"' not in candidates[2]
    assert candidates[-1].replace("/", "\\").endswith("WindowsPowerShell\\v1.0\\powershell.exe")
    argv = shell.shell_argv("pwsh", "Get-Date")
    assert argv[1:5] == ["-NoLogo", "-NoProfile", "-NonInteractive", "-Command"]
    assert argv[5].startswith("[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); ")
    assert argv[5].endswith("Get-Date")
    assert shell.ENV_OVERRIDES == {"NO_COLOR": "1", "PAGER": "cat", "GIT_PAGER": "cat"}
    assert shell.shell_kind("win32") == "pwsh" and shell.shell_kind("linux") == "bash"


def test_job_read_rendering_matches_the_documented_shapes() -> None:
    assert render_delta("out", "err\n", False, []) == "out\n[stderr]\nerr\n"
    assert render_delta("", "", True, ["/tmp/a.log"]) == (
        "[some output was dropped from memory; full output: /tmp/a.log]"
    )


async def test_completion_notice_inlines_small_output_and_defers_large_output(tmp_path: Path) -> None:
    finished: list = []
    registry = JobRegistry(finished.append)
    script = _write(tmp_path, "sized.py", "import sys\nprint('y' * int(sys.argv[1]))\n")

    async def run(size: int):  # noqa: ANN202
        stdout = procs.OutputBuffer(64_000, lambda: None)
        stderr = procs.OutputBuffer(64_000, lambda: None)
        process = await procs.spawn(
            shell.shell_argv(SHELL, _python(script, str(size))),
            cwd=str(tmp_path),
            env=dict(__import__("os").environ),
            stdout=stdout,
            stderr=stderr,
        )
        job = registry.start(SHELL, f"print {size}", process, foreground=False)
        await asyncio.wait_for(job.settled.wait(), 60)
        return job

    small = await run(10)
    text, held = completion_notice(registry, small)
    assert text == f"background job {small.id} ({SHELL}: print 10) finished [status: completed, exit code: 0]. Output:\n" + "y" * 10
    assert held == "y" * 10 + "\n"
    assert registry.read(small) == ""  # already delivered with the notice

    large = await run(5000)
    text, held = completion_notice(registry, large)
    assert text.endswith("finished [status: completed, exit code: 0]. Read its output with job_output.")
    assert held is None
    assert registry.read(large) == "y" * 5000 + "\n"  # still unread, for job_output
    assert status_line(large) == "[status: completed, exit code: 0]"
    assert finished == [small, large]


def test_f0255_output_buffer_marks_spill_truncation_at_cap(tmp_path: Path) -> None:
    exact_target = tmp_path / "exact.log"
    exact_buf = procs.OutputBuffer(10, lambda: exact_target, max_spill_bytes=20)
    exact_buf.feed(b"a" * 20)
    assert exact_buf.spill_truncated is False
    exact_buf.close()
    assert exact_target.read_bytes() == b"a" * 20

    # Feeding 1 more byte beyond cap triggers marker and stops further writes
    chunk_target = tmp_path / "chunk.log"
    chunk_buf = procs.OutputBuffer(10, lambda: chunk_target, max_spill_bytes=20)
    chunk_buf.feed(b"b" * 20)
    assert chunk_buf.spill_truncated is False
    chunk_buf.feed(b"c")
    assert chunk_buf.spill_truncated is True
    chunk_buf.feed(b"d" * 50)
    chunk_buf.close()
    content = chunk_target.read_bytes()
    assert content == b"b" * 20 + b"\n[output truncated at 20 bytes]\n"

    # Single oversized feed truncates at cap + marker
    over_target = tmp_path / "over.log"
    over_buf = procs.OutputBuffer(10, lambda: over_target, max_spill_bytes=20)
    over_buf.feed(b"x" * 35)
    assert over_buf.spill_truncated is True
    over_buf.close()
    assert over_target.read_bytes() == b"x" * 20 + b"\n[output truncated at 20 bytes]\n"


def test_f0255_shell_stream_text_and_jobs_render_delta_report_partial_when_spill_capped(
    tmp_path: Path,
) -> None:
    target = tmp_path / "capped.log"
    buf = procs.OutputBuffer(5, lambda: target, max_spill_bytes=12)
    buf.feed(b"0123456789abcdef")
    buf.close()

    rendered_shell = shell._stream_text(buf)
    assert "partial output" in rendered_shell
    assert "full output" not in rendered_shell

    rendered_delta = render_delta("", "", True, [str(target)], spill_truncated=True)
    assert rendered_delta == f"[some output was dropped from memory; partial output: {target}]"
    assert render_delta("", "", True, [str(target)], spill_truncated=False) == (
        f"[some output was dropped from memory; full output: {target}]"
    )


def test_f0255_job_registry_peek_mixed_stream_truncation(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from nekopower.runtime.tools.jobs import Job

    out_path = tmp_path / "out.log"
    err_path = tmp_path / "err.log"
    out_buf = procs.OutputBuffer(4, lambda: out_path)
    err_buf = procs.OutputBuffer(6, lambda: err_path, max_spill_bytes=4)
    out_buf.feed(b"abcdef")
    err_buf.feed(b"err-overflow")
    out_buf.close()
    err_buf.close()

    assert out_buf.spill_path == out_path and out_buf.spill_truncated is False
    assert err_buf.spill_path == err_path and err_buf.spill_truncated is True
    assert out_buf.read_from(0)[2] is True
    assert err_buf.read_from(12)[2] is False

    job = Job(
        id="job-mixed",
        kind="bash",
        label="mixed",
        process=SimpleNamespace(stdout=out_buf, stderr=err_buf),
        started_at=0.0,
        stdout_offset=0,
        stderr_offset=12,  # stderr already read past _start -> err_lossy=False
    )
    reg = JobRegistry(lambda _job: None)
    text, cursors = reg.peek(job)
    assert cursors == (6, 12)
    assert "partial output:" in text
    assert "full output" not in text
    assert str(out_path) in text
    assert str(err_path) in text


async def test_refused_job_output_is_unread_until_delivered(tmp_path):
    tools = {}
    count = 0
    async def audit(_, payload):
        nonlocal count
        if payload["phase"] == "begin":
            key = str(len(tools))
            tools[key] = payload["tool"]
            return {"allow": True, "receipt": key}
        if payload["phase"] == "job_notice":
            return {"allow": False}
        if payload["phase"] == "finish" and tools[payload["receipt"]] == "job_output":
            count += 1
            return {"allow": count != 1}
        return {"allow": True}

    cmd = "Write-Output preserved-data" if WINDOWS else "echo preserved-data"
    llm = ScriptedLLM([[call(SHELL, {"command": cmd, "description": "fixture", "run_in_background": True}),
                        finish("tool_calls")],
                       *[[call("job_output", {"job_id": "job-1", "wait": True}, f"read-{i}"),
                          finish("tool_calls")] for i in range(3)], [say("done"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(execution_action=audit))
    try:
        session.prompt("collect")
        await events.wait_ended("s1", timeout=60)
        replies = {m["tool_call_id"]: m["content"] for batch in llm.requests for m in batch if m.get("tool_call_id")}
        assert "could not be delivered" in replies["read-0"] and "preserved-data" not in replies["read-0"]
        assert "preserved-data" in replies["read-1"] and "no new output" in replies["read-2"]
    finally:
        await session.close()


async def test_closing_collects_pending_notices_after_process_settlement(tmp_path):
    from types import SimpleNamespace
    from nekopower.runtime.session import SessionError

    reporting = asyncio.Event()
    released = asyncio.Event()

    async def report(job):
        reporting.set()
        try:
            await asyncio.Event().wait()
        finally:
            released.set()

    registry = JobRegistry(report)
    finished = asyncio.Event()
    finished.set()
    process = SimpleNamespace(wait=finished.wait, kill_requested=False, signal_name=None, returncode=0)
    settled = registry.start("bash", "finished", process, foreground=False)
    await asyncio.wait_for(reporting.wait(), 2)
    assert registry.running() == [] and registry.waking() == [settled]
    session, _ = await open_session(make_spec(tmp_path), ScriptedLLM([]), _host())
    original_jobs = session.jobs
    try:
        # Only the in-flight completion notice keeps the session busy.
        session.jobs = registry
        with pytest.raises(SessionError, match="background command"):
            session.compact()
    finally:
        session.jobs = original_jobs
        await session.close()
    stopping = asyncio.Event()
    pending = SimpleNamespace(wait=stopping.wait, kill=stopping.set, kill_requested=True, signal_name=None)
    running = registry.start("bash", "running", pending, foreground=False)
    registry.close()
    await registry.wait_closed(timeout_seconds=0.5)
    assert settled.settled.is_set() and running.settled.is_set()
    assert released.is_set() and not registry.waking()


@pytest.mark.parametrize("receive", [False, True])
@pytest.mark.parametrize(("notice_allowed", "saved"), [(False, True), (True, True), (True, False)])
async def test_finishing_job_notifies_only_output_not_handed_over(tmp_path, monkeypatch, receive, notice_allowed, saved):
    from types import SimpleNamespace

    stop = asyncio.Event()
    stdout = procs.OutputBuffer(1024, lambda: None)
    stdout.feed(b"first-part\n")
    stop_at_notice = False
    async def audit(_, payload):
        if payload["phase"] == "begin":
            return {"allow": True, "receipt": "job-read"}
        if payload["phase"] == "job_notice":
            if stop_at_notice:
                session._abort_requested = True
            return {"allow": notice_allowed}
        if payload["phase"] == "finish":
            stdout.feed(b"second-part\n")
            stop.set()
            await asyncio.wait_for(job.settled.wait(), 2)
            await asyncio.sleep(0)
            return {"allow": receive}
        return {"allow": True}
    llm = ScriptedLLM([[call("job_output", {"job_id": "job-1"}, "result"), finish("tool_calls")],
                       [say("done"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(execution_action=audit))
    notices = []
    from nekopower.runtime.loop.messages import CustomMessage

    def enqueue(kind, text):
        handle = CustomMessage(custom_type=f"notice:{kind}", content=text)
        notices.append(handle)
        return handle

    monkeypatch.setattr(session, "deliver_notice", enqueue)
    process = SimpleNamespace(stdout=stdout, stderr=procs.OutputBuffer(1024, lambda: None),
                              wait=stop.wait, kill=stop.set, kill_requested=False, signal_name=None, returncode=0)
    job = session.jobs.start(SHELL, "fixture", process, foreground=False)
    job.origin = "original"
    try:
        session.prompt("Read the current output")
        await events.wait_ended("s1")
        assert len(notices) == 1 and "second-part" not in notices[0].text
        session.harness.message_list.append(notices[0])
        session._on_message(notices[0])
        assert not any(m is notices[0] for m in session.harness.message_list)
        stop_at_notice = True
        assert not await session._deliver_job_notices()
        assert job.id in session.jobs.pending_notices and not session.transcript.is_registered(notices[0])
        stop_at_notice = False
        session._abort_requested = False
        if not saved:
            append = session._recorder._append
            async def fail_notice(entry):
                if getattr(entry, "custom_type", None) == "notice:job":
                    session._recorder._lost[entry.id] = entry.parent_id
                else:
                    await append(entry)
            monkeypatch.setattr(session._recorder, "_append", fail_notice)
        assert await session._deliver_job_notices()
        assert session.transcript.is_registered(notices[0])
        await session.flush()
        from nekopower.runtime.session import TRANSCRIPT_FILE
        rows = [json.loads(line) for line in (session._session_dir / TRANSCRIPT_FILE).read_text(encoding="utf-8").splitlines()]
        assert [row["content"] for row in rows if row.get("custom_type") == "notice:job"] == ([notices[0].text] if saved else [])
        assert all(row.get("custom_type") != "notice:pending_job" for row in rows)
        assert ("second-part" in notices[0].text) == notice_allowed
        assert ("first-part" in notices[0].text) == (notice_allowed and not receive)
        assert ("second-part" in session.jobs.read(job)) != (notice_allowed and saved)
        result = next(m["content"] for m in llm.requests[1] if m.get("tool_call_id") == "result")
        assert ("first-part" in result) == receive
    finally:
        await session.close()


@pytest.mark.parametrize("receive", [False, True])
async def test_promoted_command_advances_output_only_after_handoff(tmp_path, monkeypatch, receive):
    from types import SimpleNamespace

    done = asyncio.Event()
    async def spawn(argv, **kwargs):
        kwargs["stdout"].feed(b"first-chunk\n")
        return SimpleNamespace(wait=done.wait, kill=done.set, stdout=kwargs["stdout"], stderr=kwargs["stderr"],
                               kill_requested=False, signal_name=None, returncode=0)
    monkeypatch.setattr(procs, "spawn", spawn)

    async def audit(_, payload):
        if payload["phase"] == "begin":
            return {"allow": True, "receipt": payload["tool"]}
        if payload["phase"] == "job_notice":
            return {"allow": False}
        if payload["phase"] == "finish" and payload["receipt"] == SHELL:
            done.set()
            return {"allow": receive}
        return {"allow": True}

    llm = ScriptedLLM([[call(SHELL, {"command": "fixture", "description": "fixture", "timeoutMs": 2}, "start"),
                        finish("tool_calls")],
                       [call("job_output", {"job_id": "job-1", "wait": True}, "reread"), finish("tool_calls")],
                       [say("done"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(execution_action=audit))
    try:
        session.prompt("Collect the command output")
        await events.wait_ended()
        replies = {m["tool_call_id"]: m["content"] for batch in llm.requests for m in batch if m.get("tool_call_id")}
        assert ("first-chunk" in replies["start"]) == receive
        assert ("first-chunk" in replies["reread"]) != receive
    finally:
        done.set()
        await session.close()


async def test_oversized_failed_command_keeps_its_exit_status(tmp_path):
    script = _write(tmp_path, "exit_fixture.py", "print('x' * 54000)\nraise SystemExit(2)\n")
    async def allow(_, payload):
        return {"allow": True, "receipt": "command"}
    command = _python(script) + "; exit 2"
    llm = ScriptedLLM([[call(SHELL, {"command": command, "description": "fixture"}, "failed-build"),
                        finish("tool_calls")], [say("done"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(execution_action=allow))
    try:
        session.prompt("Run the fixture.")
        await events.wait_ended(timeout=60)
        result = next(m["content"] for m in llm.requests[1] if m.get("tool_call_id") == "failed-build")
        assert "Use grep" in result and "[exit code: 2]" in result and "x" * 256 not in result
    finally:
        await session.close()


async def test_notice_expansion_is_measured_and_fitted_before_model_request(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from nekopower.runtime.loop.messages import CustomMessage
    from nekopower.runtime.tools.jobs import Job, PendingNotice

    live = True
    phases = []

    async def audit(_, payload):
        phases.append(payload["phase"])
        return {"allow": live}

    llm = ScriptedLLM([[say("done"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(execution_action=audit))
    stdout = procs.OutputBuffer(1024, lambda: None)
    stdout.feed(b"private-fixture-text")
    job = Job("fixture", SHELL, "fixture", SimpleNamespace(stdout=stdout, stderr=procs.OutputBuffer(64, lambda: None)),
              0, status="completed", origin="fixture-receipt")
    handle = CustomMessage(custom_type="notice:pending_job", content="Job finished.", display=False)
    session.jobs._jobs[job.id] = job
    session.jobs.pending_notices[job.id] = PendingNotice(handle, None, admitted=True)

    async def measure():
        return session.context.policy.threshold

    async def fit(**options):
        nonlocal live
        if options.get("reserve_tokens"):
            assert "private-fixture-text" not in str(session.transcript.messages)
            assert job.stdout_offset == 0
            live = False

    monkeypatch.setattr(session, "_size", measure)
    monkeypatch.setattr(session, "_fit", fit)
    try:
        session.prompt("Fixture")
        await events.wait_ended()
        assert phases == ["job_notice_live", "job_notice"]
        assert "Job finished." in str(llm.requests)
        assert "private-fixture-text" not in str(llm.requests)
        assert "private-fixture-text" not in str(session.transcript.messages)
        assert job.stdout_offset == 0
    finally:
        await session.close()


async def test_job_notice_batch_rechecks_earlier_sources_before_delivery(tmp_path):
    from types import SimpleNamespace
    from nekopower.host.task_agents import TaskAgentManager
    from nekopower.runtime.loop.messages import CustomMessage
    from nekopower.runtime.tools.jobs import Job, PendingNotice

    first_live = True

    async def admit_first():
        return True

    async def admit_second():
        nonlocal first_live
        first_live = False
        return True

    sources = {"first": (lambda: first_live, admit_first), "second": (lambda: True, admit_second)}
    host_session = SimpleNamespace(session_id="s1", closing=False, job_sources=sources)
    manager = SimpleNamespace(_held_by={"s1": "work"}, _sessions={"work": host_session})

    async def audit(session_id, payload):
        return await TaskAgentManager._cb_execution_action(manager, session_id, payload)

    llm = ScriptedLLM([[say("done"), finish()]])
    session, events = await open_session(make_spec(tmp_path), llm, HostCallbacks(execution_action=audit))
    jobs = session.jobs
    try:
        for key in sources:
            stdout = procs.OutputBuffer(1024, lambda: None)
            stdout.feed(f"private-{key}".encode())
            proc = SimpleNamespace(stdout=stdout, stderr=procs.OutputBuffer(64, lambda: None))
            job = Job(key, SHELL, "fixture", proc, 0, status="completed", origin=key)
            jobs._jobs[key] = job
            handle = CustomMessage(custom_type="notice:pending_job", content=f"{key} finished.", display=False)
            jobs.pending_notices[key] = PendingNotice(handle, None, admitted=True)
        session.prompt("Collect the fixtures.")
        await events.wait_ended()
        await session.flush()
        assert not first_live
        for view in (str(llm.requests), str(session.transcript.messages)):
            assert "private-first" not in view and "first finished." in view and "private-second" in view
        assert jobs.get("first").stdout_offset == 0 and jobs.get("second").stdout_offset > 0
    finally:
        await session.close()


def test_spill_write_error_reports_partial_output(tmp_path):
    from unittest.mock import Mock

    target = tmp_path / "short.log"
    stream = Mock()
    stream.write.side_effect = OSError("write failed")
    buf = procs.OutputBuffer(5, lambda: target, protected={target: stream})
    buf.feed(b"0123456789")
    assert buf.spill_truncated
    assert f"partial output: {target}" in shell._stream_text(buf)
    assert f"partial output: {target}" in render_delta("", "", True, [str(target)], buf.spill_truncated)
