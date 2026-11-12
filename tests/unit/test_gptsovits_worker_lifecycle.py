"""Drive the real worker with controlled sockets, without services or user config."""

import asyncio
from io import BytesIO
import json
import queue
from pathlib import Path
import subprocess
import sys
import threading
import wave

import numpy as np
import pytest
import websockets
from websockets.protocol import State

from main_logic.tts_client._infra import TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client.workers import gptsovits


_EOF = object()


def _wav(value=1000, *, rate=48000, samples=4800):
    result = BytesIO()
    with wave.open(result, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(np.full(samples, value, dtype=np.int16).tobytes())
    return result.getvalue()


class _Socket:
    def __init__(self, *, ready_gate=None, send_gate=None, close_gate=None,
                 connect_gate=None, late_on_cancel=None, fail_append=False, fail_init=False):
        self.state = State.OPEN
        self.events = queue.SimpleQueue()
        self.events.put(json.dumps({"type": "ready"}))
        self.ready_gate = ready_gate
        self.send_gate = send_gate
        self.close_gate = close_gate
        self.connect_gate = connect_gate
        self.late_on_cancel = late_on_cancel
        self.fail_append = fail_append
        self.fail_init = fail_init
        self.recv_calls = 0
        self.sent = []
        self.connect_started = threading.Event()
        self.connect_cancelled = threading.Event()
        self.init_sent = threading.Event()
        self.ready_waiting = threading.Event()
        self.receiver_waiting = threading.Event()
        self.binary_seen = threading.Event()
        self.append_sent = threading.Event()
        self.end_sent = threading.Event()
        self.close_started = threading.Event()
        self.closed = threading.Event()
        self.late_attempted = threading.Event()

    async def _gate(self, gate):
        if gate is not None:
            while not gate.is_set():
                await asyncio.sleep(0.001)

    async def recv(self):
        try:
            self.recv_calls += 1
            if self.recv_calls > 1:
                self.receiver_waiting.set()
            if not self.ready_waiting.is_set():
                self.ready_waiting.set()
                await self._gate(self.ready_gate)
            while self.events.empty():
                await asyncio.sleep(0.001)
            event = self.events.get()
            if event is _EOF:
                raise StopAsyncIteration
            if isinstance(event, bytes):
                self.binary_seen.set()
            return event
        except asyncio.CancelledError:
            if self.late_on_cancel is not None:
                event, self.late_on_cancel = self.late_on_cancel, None
                self.late_attempted.set()
                return event
            raise

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.recv()

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        cmd = message["cmd"]
        if cmd == "init":
            self.init_sent.set()
            if self.fail_init:
                raise RuntimeError("init failed")
        elif cmd == "append":
            self.append_sent.set()
            await self._gate(self.send_gate)
            if self.fail_append:
                raise RuntimeError("append failed")
        elif cmd == "end":
            self.end_sent.set()

    async def close(self):
        self.close_started.set()
        self.state = State.CLOSING
        await self._gate(self.close_gate)
        self.state = State.CLOSED
        self.closed.set()
        self.events.put(_EOF)

    def feed(self, event):
        self.events.put(json.dumps(event) if isinstance(event, dict) else event)

    def finish(self):
        self.feed({"type": "done"})
        self.events.put(_EOF)

    def release(self):
        for gate in (self.ready_gate, self.send_gate, self.close_gate, self.connect_gate):
            if gate is not None:
                gate.set()
        self.events.put(_EOF)


class _Responses(queue.Queue):
    def __init__(self):
        super().__init__()
        self.changed = threading.Condition()

    def put(self, item, *args, **kwargs):
        super().put(item, *args, **kwargs)
        with self.changed:
            self.changed.notify_all()

    def snapshot(self):
        with self.mutex:
            return list(self.queue)

    def wait(self, predicate, timeout=2):
        with self.changed:
            return self.changed.wait_for(lambda: predicate(self.snapshot()), timeout)


class _Worker:
    def __init__(self, monkeypatch, sockets, voice="example"):
        self.sockets = sockets
        self.requests = queue.Queue()
        self.responses = _Responses()
        remaining = iter(sockets)

        async def connect(*_args, **_kwargs):
            socket = next(remaining)
            socket.connect_started.set()
            try:
                await socket._gate(socket.connect_gate)
            except asyncio.CancelledError:
                socket.connect_cancelled.set()
                await socket.close()
                raise
            return socket

        monkeypatch.setattr(gptsovits.websockets, "connect", connect)
        monkeypatch.setattr(gptsovits, "get_config_manager", lambda: None)
        monkeypatch.setattr(gptsovits, "resolve_worker_gsv_api_url", lambda _: "http://127.0.0.1:9881")
        monkeypatch.setattr(gptsovits, "_record_tts_telemetry", lambda *_args: None)
        self.thread = threading.Thread(target=gptsovits.gptsovits_tts_worker,
            args=(self.requests, self.responses, "", voice), daemon=True)
        self.thread.start()

    def ready(self):
        assert self.responses.wait(lambda items: ("__ready__", True) in items)

    def put(self, sid, text=None):
        self.requests.put((sid, text))

    def done(self, sid):
        assert self.responses.wait(lambda items: ("__audio_done__", sid) in items)

    def close(self):
        for socket in self.sockets:
            socket.release()
        self.put(TTS_SHUTDOWN_SENTINEL)
        self.thread.join(5)
        assert not self.thread.is_alive(), "worker or socket cleanup leaked"


@pytest.fixture
def worker(monkeypatch):
    workers = []

    def create(*sockets, voice="example"):
        result = _Worker(monkeypatch, list(sockets), voice)
        workers.append(result)
        return result

    yield create
    for result in workers:
        result.close()


def _audio(items):
    return [item for item in items if isinstance(item, tuple) and item[0] == "__audio__"]


def test_ended_speeches_drain_in_order_without_an_interrupt(worker):
    probe, first, second, third = _Socket(), _Socket(), _Socket(), _Socket()
    run = worker(probe, first, second, third)
    run.ready()
    run.put("first", "First sentence.")
    run.put(None)
    assert first.end_sent.wait(2)
    run.put("second", "Second sentence.")
    run.put(None)
    run.put("third", "Third sentence.")
    run.put(None)
    assert not second.connect_started.wait(0.2)
    assert not first.close_started.is_set()

    for sid, socket in (("first", first), ("second", second), ("third", third)):
        assert socket.end_sent.wait(2)
        socket.feed(_wav(rate=32000, samples=3200))
        socket.finish()
        run.done(sid)

    items = run.responses.snapshot()
    previous_done = -1
    for sid in ("first", "second", "third"):
        done = ("__audio_done__", sid)
        audio = [(i, item[2]) for i, item in enumerate(items) if item[:2] == ("__audio__", sid)]
        assert items.count(done) == 1
        assert sum(len(pcm) for _, pcm in audio) == 9600
        assert all(previous_done < index < items.index(done) for index, _ in audio)
        previous_done = items.index(done)


def test_end_wait_does_not_block_interrupt_or_the_next_speech(worker):
    probe, old, new = _Socket(), _Socket(), _Socket()
    run = worker(probe, old, new)
    run.ready()
    run.put("old", "Old sentence.")
    assert old.append_sent.wait(2)
    run.put(None)
    assert old.end_sent.wait(2)
    run.put("queued", "Queued sentence.")
    run.put(None)
    # Never release old's synthesis/done gate. The control queue must still run.
    run.put("__interrupt__")
    run.put("new", "New sentence.")
    assert old.closed.wait(2), "interrupt is stuck behind normal end"
    assert new.append_sent.wait(2)
    assert [m["data"] for m in new.sent if m["cmd"] == "append"] == ["New sentence."]
    new.feed(_wav(2000))
    run.put(None)
    assert new.end_sent.wait(2)
    new.finish()
    run.done("new")
    items = run.responses.snapshot()
    assert ("__audio_done__", "old") not in items
    assert ("__audio_done__", "queued") not in items
    assert [item[1] for item in _audio(items)] == ["new"]


@pytest.mark.parametrize("rate", [24000, 32000, 48000])
def test_normal_end_preserves_audio_identity_tail_and_completion_order(worker, rate):
    probe, speech = _Socket(), _Socket()
    run = worker(probe, speech)
    run.ready()
    run.put("speech", "A normal sentence.")
    assert speech.append_sent.wait(2)
    speech.feed(_wav(rate=rate, samples=rate // 10))
    run.put(None)
    assert speech.end_sent.wait(2)
    run.put(None)  # Duplicate completion must not send another end.
    speech.finish()
    run.done("speech")
    items = run.responses.snapshot()
    audio = _audio(items)
    assert audio and all(item[1] == "speech" for item in audio)
    assert not any(isinstance(item, bytes) for item in items)
    assert sum(len(item[2]) // 2 for item in audio) == 4800
    assert items.count(("__audio_done__", "speech")) == 1
    assert items.index(("__audio_done__", "speech")) > max(items.index(item) for item in audio)
    assert sum(message["cmd"] == "end" for message in speech.sent) == 1


@pytest.mark.parametrize("late", [_wav(1234), json.dumps({"type": "error", "message": "old failure"}),
                                 json.dumps({"type": "done"})], ids=["audio", "error", "done"])
def test_cancelled_receiver_cannot_publish_late_audio_error_or_done(worker, late):
    probe, old, new = _Socket(), _Socket(late_on_cancel=late), _Socket()
    run = worker(probe, old, new)
    run.ready()
    run.put("old", "Old sentence.")
    assert old.append_sent.wait(2)
    assert old.receiver_waiting.wait(2)
    run.put("__interrupt__")
    assert old.closed.wait(2)
    assert old.late_attempted.is_set()
    run.put("new", "New sentence.")
    assert new.append_sent.wait(2)
    new.feed(_wav(2345))
    run.put(None)
    assert new.end_sent.wait(2)
    new.finish()
    run.done("new")
    items = run.responses.snapshot()
    assert ("__audio_done__", "old") not in items
    assert not any(isinstance(item, bytes) for item in items)
    assert all(item[1] == "new" for item in _audio(items))
    assert not any(item[0] == "__error__" for item in items if isinstance(item, tuple))


@pytest.mark.parametrize("stage", ["connect", "ready", "send"])
def test_interrupt_cancels_network_waits_without_releasing_the_old_gate(worker, stage):
    gate = threading.Event()
    args = {"connect_gate": gate} if stage == "connect" else {"ready_gate": gate} if stage == "ready" else {"send_gate": gate}
    probe, old, new = _Socket(), _Socket(**args), _Socket()
    run = worker(probe, old, new)
    run.ready()
    run.put("old", "Old sentence.")
    entered = old.connect_started if stage == "connect" else old.ready_waiting if stage == "ready" else old.append_sent
    assert entered.wait(2)
    run.put("__interrupt__")
    run.put("new", "New sentence.")
    assert old.closed.wait(2), f"interrupt blocked by {stage}"
    assert new.append_sent.wait(2)
    assert not gate.is_set()
    run.put(None)
    assert new.end_sent.wait(2)
    new.finish()
    run.done("new")
    if stage in ("connect", "ready"):
        assert not old.append_sent.is_set()


def test_retired_cleanup_cannot_close_the_replacement_session(worker):
    close_gate = threading.Event()
    probe, old, middle, new = _Socket(), _Socket(close_gate=close_gate), _Socket(), _Socket()
    run = worker(probe, old, middle, new)
    run.ready()
    run.put("old", "Old sentence.")
    assert old.append_sent.wait(2)
    run.put("middle", "Middle sentence.")
    assert old.close_started.wait(2)
    assert middle.append_sent.wait(2), "old cleanup blocked control dispatch"
    run.put("new", "New sentence.")
    assert new.append_sent.wait(2)
    close_gate.set()
    assert old.closed.wait(2)
    assert not new.closed.is_set()
    run.put(None)
    assert new.end_sent.wait(2)
    new.finish()
    run.done("new")
    assert ("__audio_done__", "old") not in run.responses.snapshot()
    assert ("__audio_done__", "middle") not in run.responses.snapshot()


def test_shutdown_can_exit_while_end_is_waiting(worker):
    probe, speech = _Socket(), _Socket()
    run = worker(probe, speech)
    run.ready()
    run.put("speech", "A sentence.")
    assert speech.append_sent.wait(2)
    run.put(None)
    assert speech.end_sent.wait(2)
    run.put(TTS_SHUTDOWN_SENTINEL)
    run.thread.join(2)
    assert not run.thread.is_alive(), "shutdown waited for old synthesis"
    assert speech.closed.is_set()
    assert ("__audio_done__", "speech") not in run.responses.snapshot()


def test_failed_send_closes_its_socket_and_scopes_the_error(worker):
    probe, failed, new = _Socket(), _Socket(fail_append=True), _Socket()
    run = worker(probe, failed, new)
    run.ready()
    run.put("failed", "A failed sentence.")
    assert failed.closed.wait(2)
    assert run.responses.wait(lambda items: any(item[0] == "__error__" for item in items))
    items = run.responses.snapshot()
    error_index = next(i for i, item in enumerate(items) if item[0] == "__error__")
    assert items[error_index - 1] == ("__tts_sentence_failed__", "failed", "")
    run.put("new", "A new sentence.")
    assert new.append_sent.wait(2)
    run.put(None)
    assert new.end_sent.wait(2)
    new.finish()
    run.done("new")


def test_voice_prefix_and_reserved_advanced_fields_keep_the_existing_contract(worker):
    probe, speech = _Socket(), _Socket()
    run = worker(probe, speech, voice='gsv:example|{"cmd":"bad","voice_id":"bad","speed_factor":1.1}')
    run.ready()
    run.put("speech", "A sentence.")
    assert speech.append_sent.wait(2)
    for socket in (probe, speech):
        assert socket.sent[0] == {"cmd": "init", "voice_id": "example", "speed_factor": 1.1}
    run.put(None)
    assert speech.end_sent.wait(2)
    speech.finish()
    run.done("speech")


def test_startup_failure_closes_the_probe_and_exits_without_queue_input(worker):
    probe = _Socket(fail_init=True)
    run = worker(probe)
    assert run.responses.wait(lambda items: ("__ready__", False) in items)
    run.thread.join(2)
    assert not run.thread.is_alive()
    assert probe.closed.is_set()


@pytest.mark.parametrize("stage", ["connect", "ready"])
def test_shutdown_can_cancel_initial_probe(worker, stage):
    gate = threading.Event()
    probe = _Socket(**{"connect_gate" if stage == "connect" else "ready_gate": gate})
    run = worker(probe)
    entered = probe.connect_started if stage == "connect" else probe.ready_waiting
    assert entered.wait(2)
    run.put(TTS_SHUTDOWN_SENTINEL)
    run.thread.join(2)
    assert not run.thread.is_alive()
    assert probe.closed.is_set()
    assert not gate.is_set()
    assert ("__ready__", True) not in run.responses.snapshot()


def test_interrupt_cancels_connection_retry_without_replaying_the_old_text(worker):
    probe, failed, new = _Socket(), _Socket(fail_init=True), _Socket()
    run = worker(probe, failed, new)
    run.ready()
    run.put("old", "Old sentence.")
    assert failed.closed.wait(2)
    run.put("__interrupt__")
    run.put("new", "New sentence.")
    assert new.append_sent.wait(2)
    assert [item["data"] for item in new.sent if item["cmd"] == "append"] == ["New sentence."]
    run.put(None)
    assert new.end_sent.wait(2)
    new.finish()
    run.done("new")
    assert ("__audio_done__", "old") not in run.responses.snapshot()


def test_end_timeout_cleans_its_receiver_and_does_not_complete_the_failed_speech(worker, monkeypatch):
    real_wait = gptsovits.asyncio.wait_for

    async def timeout_end(task, timeout):
        if timeout == 30.0:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise asyncio.TimeoutError("end timed out")
        return await real_wait(task, timeout)

    monkeypatch.setattr(gptsovits.asyncio, "wait_for", timeout_end)
    probe, speech = _Socket(), _Socket()
    run = worker(probe, speech)
    run.ready()
    run.put("speech", "A sentence.")
    assert speech.append_sent.wait(2)
    run.put(None)
    assert speech.closed.wait(2)
    assert run.responses.wait(lambda items: any(item[0] == "__error__" for item in items))
    assert ("__audio_done__", "speech") not in run.responses.snapshot()


def test_interrupt_discards_resampler_tail_instead_of_flushing_old_audio(worker):
    probe, speech = _Socket(), _Socket()
    run = worker(probe, speech)
    run.ready()
    run.put("speech", "A sentence.")
    assert speech.append_sent.wait(2)
    speech.feed(_wav(rate=32000, samples=37))
    assert speech.binary_seen.wait(2)
    run.put("__interrupt__")
    assert speech.closed.wait(2)
    assert _audio(run.responses.snapshot()) == []
    assert ("__audio_done__", "speech") not in run.responses.snapshot()


@pytest.mark.asyncio
@pytest.mark.integration_serial
async def test_real_websocket_disconnect_after_end_reaches_server(monkeypatch):
    old_end = asyncio.Event()
    old_closed = asyncio.Event()

    async def handler(socket):
        try:
            assert json.loads(await socket.recv())["cmd"] == "init"
            await socket.send(json.dumps({"type": "ready"}))
            text = ""
            async for raw in socket:
                message = json.loads(raw)
                if message["cmd"] == "append":
                    text += message["data"]
                elif message["cmd"] == "end":
                    if text == "Old sentence.":
                        old_end.set()
                        # Simulate blocked inference. Only the actual close
                        # handshake can release this server handler.
                        await socket.wait_closed()
                        old_closed.set()
                    else:
                        await socket.send(_wav(2000))
                        await socket.send(json.dumps({"type": "done"}))
                        await socket.close()
                    break
        except websockets.exceptions.ConnectionClosed:
            pass

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        listeners = tuple(server.sockets)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(gptsovits, "get_config_manager", lambda: None)
        monkeypatch.setattr(gptsovits, "resolve_worker_gsv_api_url", lambda _: f"http://127.0.0.1:{port}")
        monkeypatch.setattr(gptsovits, "_record_tts_telemetry", lambda *_args: None)
        requests, responses = queue.Queue(), _Responses()
        thread = threading.Thread(target=gptsovits.gptsovits_tts_worker,
            args=(requests, responses, "", "example"), daemon=True)
        thread.start()
        try:
            assert await asyncio.to_thread(responses.wait, lambda items: ("__ready__", True) in items)
            requests.put(("old", "Old sentence."))
            requests.put((None, None))
            await asyncio.wait_for(old_end.wait(), 3)
            requests.put(("__interrupt__", None))
            requests.put(("new", "New sentence."))
            requests.put((None, None))
            await asyncio.wait_for(old_closed.wait(), 3)
            assert await asyncio.to_thread(responses.wait, lambda items: ("__audio_done__", "new") in items)
            items = responses.snapshot()
            assert ("__audio_done__", "old") not in items
            assert [item[1] for item in _audio(items)] == ["new"]
        finally:
            requests.put((TTS_SHUTDOWN_SENTINEL, None))
            server.close()
            await server.wait_closed()
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()
    assert all(listener.fileno() == -1 for listener in listeners)


@pytest.mark.integration_serial
def test_worker_cleanup_with_standard_asyncio_run():
    # conftest wraps asyncio.run with nest_asyncio in the parent process. This
    # child deliberately doesn't load conftest, while retaining data isolation.
    script = r'''
import asyncio
from pathlib import Path
import tempfile
import pytest
from utils import logger_config
logs = tempfile.TemporaryDirectory(prefix="neko_gsv_worker_logs_")
logger_config.RobustLoggerConfig._get_documents_directory = lambda self: Path(logs.name)
from utils.config_manager import ConfigManager
from tests import real_root_isolation
real_root_isolation.install(ConfigManager)
from tests.unit import test_gptsovits_worker_lifecycle as checks
assert asyncio.run.__module__ == "asyncio.runners"
for check in (checks.test_end_wait_does_not_block_interrupt_or_the_next_speech,
              checks.test_startup_failure_closes_the_probe_and_exits_without_queue_input):
    workers = []
    with pytest.MonkeyPatch.context() as patches:
        def worker(*sockets, voice="example"):
            result = checks._Worker(patches, list(sockets), voice)
            workers.append(result)
            return result
        try:
            check(worker)
        finally:
            for result in workers:
                result.close()
with pytest.MonkeyPatch.context() as patches:
    asyncio.run(checks.test_real_websocket_disconnect_after_end_reaches_server(patches))
logs.cleanup()
'''
    result = subprocess.run([sys.executable, "-B", "-c", script],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
