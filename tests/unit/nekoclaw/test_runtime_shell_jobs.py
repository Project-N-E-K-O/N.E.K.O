"""The command tool and background jobs, with real shell processes in a temp directory.

Covers: UTF-8 output, exit markers, live progress, timeout turning into a
background job, killing a whole process tree, the completion notice, and
output kept to its tail with the full text on disk.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from nekoclaw.runtime.host import HostCallbacks
from nekoclaw.runtime.tools import procs, shell
from nekoclaw.runtime.tools.jobs import JobRegistry, completion_notice, render_delta, status_line
from tests.unit.nekoclaw.fakes import ScriptedLLM, allow_all, call, finish, make_spec, open_session, say

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
        job = session.jobs.get("job-1")
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


async def test_background_job_completion_is_reported_to_the_session(tmp_path: Path) -> None:
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
    session, events = await open_session(make_spec(tmp_path), llm, _host())
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
            if self.name == "nekoclaw-taskkill":
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
