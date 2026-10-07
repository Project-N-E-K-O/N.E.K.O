"""Terminate real overwrite processes at barriers, then restart against the same JSON."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue, Empty
import subprocess
import signal
import threading

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "tests" / "helpers" / "voice_overwrite_crash_worker.py"


@contextmanager
def provider():
    state = {"requests": 0, "accepted": 0}
    partial = threading.Event()
    ended = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"] += 1
            partial.set()
            try:
                expected = int(self.headers["Content-Length"])
                body = self.rfile.read(expected)
                if len(body) != expected:
                    return
                state["accepted"] += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")
            except (BrokenPipeError, ConnectionResetError):
                # Killing the client may reset the read as well as the reply.
                pass
            finally:
                ended.set()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/update", state, partial, ended
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def start(directory, url, stage="none", action="overwrite"):
    return subprocess.Popen(
        ["uv", "run", "--no-sync", "python", str(WORKER), str(directory), url, stage, action],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8",
        start_new_session=os.name != "nt",
    )


def terminate(process):
    # uv owns a child Python process: terminate the full tree, not just its waiter.
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           check=True, capture_output=True)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def wait_barrier(process, stage):
    lines = Queue()

    def read():
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    output = []
    try:
        for _ in range(200):
            try:
                line = lines.get(timeout=20)
            except Empty:
                pytest.fail("No barrier reached: " + "".join(output))
            if line is None:
                pytest.fail("Worker ended before barrier: " + "".join(output))
            output.append(line)
            if line.strip() == "BARRIER:" + stage:
                return reader
        pytest.fail("Excessive worker output before barrier")
    except BaseException:
        terminate(process)
        reader.join(timeout=5)
        assert not reader.is_alive(), "Barrier reader outlived the terminated process"
        raise


def run(directory, url, action):
    process = start(directory, url, action=action)
    try:
        output, _ = process.communicate(timeout=25)
        assert process.returncode == 0, output
        data = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        assert data, output
        return data[-1]
    finally:
        if process.poll() is None:
            terminate(process)


def persisted(directory):
    storage = json.loads((directory / "voices.json").read_text(encoding="utf-8"))
    return next(record for bucket in storage.values() for record in bucket.values())


@pytest.mark.parametrize("stage", ["prepared", "converted", "sending", "accepted", "before_save"])
def test_crash_restart_preserves_submission_evidence_and_identity(tmp_path, stage):
    with provider() as (url, state, partial, ended):
        process = start(tmp_path, url, stage)
        reader = None
        try:
            reader = wait_barrier(process, stage)
            if stage == "sending":
                assert partial.wait(timeout=5)
            terminate(process)
        finally:
            if process.poll() is None:
                terminate(process)
            if reader is not None:
                reader.join(timeout=5)
                assert not reader.is_alive(), "Barrier reader outlived the terminated process"
            process.stdin.close()
            process.stdout.close()
        if stage in {"sending", "accepted", "before_save"}:
            assert ended.wait(timeout=5)
        record = persisted(tmp_path)
        ref, op = record["local_ref"], record["overwrite_operation_id"]
        assert record["overwrite_status"] == "processing"
        expected_count = 1 if stage in {"accepted", "before_save"} else 0
        assert state["accepted"] == expected_count
        recovered = run(tmp_path, url, "recover")
        if stage == "prepared":
            assert recovered["result"]["status"] == "failed"
            assert persisted(tmp_path)["overwrite_terminal_reason"] == "not_submitted_recovered"
            retried = run(tmp_path, url, "overwrite")
            assert retried["result"]["status"] == "completed"
            assert persisted(tmp_path)["overwrite_operation_id"] != op
            assert state["accepted"] == 1
        else:
            assert recovered["code"] == "VOICE_STATE_CHANGED"
            retried = run(tmp_path, url, "overwrite")
            assert retried["code"] == "UPDATE_OUTCOME_UNKNOWN"
            assert state["accepted"] == expected_count
            assert persisted(tmp_path)["overwrite_operation_id"] == op
        assert persisted(tmp_path)["local_ref"] == ref
