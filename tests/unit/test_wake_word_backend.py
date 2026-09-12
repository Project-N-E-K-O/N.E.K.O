"""Backend sample mapping and actual spawned-worker lifetime regression tests."""

import asyncio
import time
import threading
import sys
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

import pytest

from main_logic.voice_input.activation.contracts import ActivationGeneration, AudioFrame
from main_logic.voice_input.wake_word import sherpa_backend as backend


GENERATION = ActivationGeneration("test", 1, 1, 1, 1, "input")


def frame(start=16000, count=3200):
    return AudioFrame(0, start, start + count, 10.0, 16000,
                      b"\0\0" * count, GENERATION)


class FakeStream:
    def __init__(self):
        self.samples = []

    def accept_waveform(self, sample_rate, samples):
        assert sample_rate == 16000
        self.samples.extend(samples)


class FakeSpotter:
    def __init__(self):
        self.streams = []
        self.ready = False
        self.keyword = "name"
        self.times = [0.02, 0.08]
        self.keyword_spotter = self

    def create_stream(self):
        stream = FakeStream()
        self.streams.append(stream)
        return stream

    def is_ready(self, stream):
        return self.ready

    def decode_stream(self, stream):
        self.ready = False

    def get_result(self, stream):
        return SimpleNamespace(keyword=self.keyword, timestamps=self.times)

    def timestamps(self, stream):
        return self.times


def streaming():
    worker = backend._StreamingSpotter.__new__(backend._StreamingSpotter)
    worker.runtime_version = backend.SUPPORTED_RUNTIME_VERSION
    worker.native_version = backend.SUPPORTED_RUNTIME_VERSION
    worker.spotter = FakeSpotter()
    worker.keywords = "test @name"
    worker.labels = {"name"}
    worker.stream = None
    worker.identity = None
    worker.base = worker.end = 0
    worker.detected = False
    return worker


def test_silence_continuity_and_absolute_timestamps():
    worker = streaming()
    assert worker.feed(frame(), 3) is None
    worker.spotter.ready = True
    result = worker.feed(frame(19200), 3)
    assert result.generation == GENERATION and result.epoch == 3
    assert result.sample_start == 16320
    assert result.sample_end == 17920
    assert len(worker.spotter.streams) == 1
    assert len(worker.stream.samples) == 6400


@pytest.mark.parametrize("change", ["epoch", "generation", "gap", "hit"])
def test_stream_restarts_without_mapping_across_gaps(change):
    worker = streaming()
    worker.feed(frame(), 1)
    next_frame, epoch = frame(19200), 1
    if change == "epoch":
        epoch = 2
    elif change == "generation":
        next_frame = replace(next_frame, generation=replace(GENERATION, route=2))
    elif change == "gap":
        next_frame = frame(40000)
    else:
        worker.detected = True
    worker.spotter.ready = True
    result = worker.feed(next_frame, epoch)
    assert len(worker.spotter.streams) == 2
    assert result.sample_start == next_frame.sample_start + 320


@pytest.mark.parametrize("times", [[], [float("nan")], [-1], [0.08, 0.02], [5]])
def test_invalid_decoder_timestamps_never_become_evidence(times):
    worker = streaming()
    worker.spotter.times, worker.spotter.ready = times, True
    with pytest.raises(backend.WakeWordBackendError):
        worker.feed(frame(), 1)


def ready_info(config):
    return dict(runtime_version="1.13.8+neko.kws2", native_version="1.13.8+neko.kws2",
                max_active_paths=config.max_active_paths,
                keyword_threshold=config.keyword_threshold, keyword_score=config.keyword_score,
                num_threads=config.num_threads, num_trailing_blanks=1,
                sample_rate=16000, provider="cpu")


def responsive_worker(connection, config):
    connection.send((True, ready_info(config)))
    try:
        while True:
            received, epoch = connection.recv()
            assert received.context is None
            connection.send((True, None))
    except EOFError:
        pass


def stuck_worker(connection, config):
    connection.send((True, ready_info(config)))
    connection.recv()
    time.sleep(120)


def failed_worker(connection, config):
    connection.send((False, "failed"))


@pytest.mark.asyncio
async def test_actual_spawn_receives_only_pcm_metadata_and_close_is_terminal(monkeypatch):
    monkeypatch.setattr(backend, "_worker", responsive_worker)
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig("unused", ("x @name",)))
    await detector.prepare()
    process = detector._process
    assert await detector.feed(replace(frame(), context=lambda: None), 1) is None
    await detector.close()
    assert detector._process is None
    with pytest.raises(ValueError):
        process.is_alive()  # handle is closed after child is reaped
    with pytest.raises(backend.WakeWordBackendError):
        await detector.feed(frame(), 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_stuck_native_work_is_killed_on_timeout_or_cancellation(monkeypatch, cancel):
    monkeypatch.setattr(backend, "_worker", stuck_worker)
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig(
        "unused", ("x @name",), inference_timeout=0.2))
    await detector.prepare()
    task = asyncio.create_task(detector.feed(frame(), 1))
    await asyncio.sleep(0.05)
    if cancel:
        task.cancel()
    with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError, backend.WakeWordBackendError)):
        await asyncio.wait_for(task, 3)
    assert detector._process is None
    assert detector._closed.is_set()


@pytest.mark.asyncio
async def test_preparation_failure_reaps_child(monkeypatch):
    monkeypatch.setattr(backend, "_worker", failed_worker)
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig("unused", ("x @name",)))
    with pytest.raises(backend.WakeWordBackendError):
        await detector.prepare()
    assert detector._process is None


@pytest.mark.asyncio
async def test_cancelled_prepare_late_spawn_cleans_its_own_process(monkeypatch):
    monkeypatch.setattr(backend, "_worker", responsive_worker)
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig("unused", ("x @name",)))
    original = detector._launch
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def delayed_launch():
        entered.set()
        assert release.wait(3)
        try:
            original()
        finally:
            finished.set()

    monkeypatch.setattr(detector, "_launch", delayed_launch)
    task = asyncio.create_task(detector.prepare())
    assert await asyncio.to_thread(entered.wait, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert detector._closed.is_set()
    release.set()
    assert await asyncio.to_thread(finished.wait, 3)
    assert detector._process is None and detector._connection is None


@pytest.mark.asyncio
async def test_concurrent_close_has_one_handle_cleanup_owner(monkeypatch):
    monkeypatch.setattr(backend, "_worker", responsive_worker)
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig("unused", ("x @name",)))
    await detector.prepare()
    await asyncio.wait_for(asyncio.gather(detector.close(), detector.close(), detector.close()), 3)
    assert detector._process is None and detector._connection is None


@pytest.mark.asyncio
async def test_concurrent_feed_rejected_without_extra_audio_queue(monkeypatch):
    monkeypatch.setattr(backend, "_worker", stuck_worker)
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig("unused", ("x @name",)))
    await detector.prepare()
    task = asyncio.create_task(detector.feed(frame(), 1))
    await asyncio.sleep(0.05)
    with pytest.raises(backend.WakeWordBackendError, match="CONCURRENT"):
        await detector.feed(frame(), 1)
    await detector.close()
    with pytest.raises((OSError, EOFError, backend.WakeWordBackendError)):
        await task


@pytest.mark.asyncio
async def test_invalid_frame_budget_never_reaches_worker(monkeypatch):
    monkeypatch.setattr(backend, "_worker", responsive_worker)
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig("unused", ("x @name",)))
    await detector.prepare()
    try:
        for invalid in (frame(count=16001), replace(frame(), sample_rate=8000)):
            with pytest.raises(backend.WakeWordBackendError, match="FRAME_INVALID"):
                await detector.feed(invalid, 1)
    finally:
        await detector.close()


@pytest.mark.parametrize("kwargs", [dict(keywords=()), dict(keywords=("x",)),
    dict(keywords=("x @a/y @b",)), dict(keyword_threshold=float("nan")),
    dict(inference_timeout=0), dict(max_frame_samples=999999)])
def test_invalid_configuration_rejected(kwargs):
    values = dict(model_dir="unused", keywords=("x @name",))
    values.update(kwargs)
    with pytest.raises(ValueError):
        backend.SherpaWakeWordConfig(**values)


def test_model_initialization_checks_paths_tokens_and_temporary_keyword_lifecycle(tmp_path, monkeypatch):
    configured = []

    def constructor(**kwargs):
        path = Path(kwargs["keywords_file"])
        assert path.read_text(encoding="utf-8") == "x @name\n"
        assert kwargs["num_threads"] == 1 and kwargs["provider"] == "cpu"
        assert kwargs["keywords_threshold"] == 0.25
        configured.append(kwargs)
        return FakeSpotter()

    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        KeywordSpotter=constructor, __version__=backend.SUPPORTED_RUNTIME_VERSION,
        version=backend.SUPPORTED_RUNTIME_VERSION,
    ))
    config = backend.SherpaWakeWordConfig(str(tmp_path), ("x @name",))
    with pytest.raises(backend.WakeWordBackendError, match="MODEL_MISSING"):
        backend._StreamingSpotter(config)
    for path in backend.model_files(str(tmp_path)).values():
        Path(path).write_text("other 0\n", encoding="utf-8")
    with pytest.raises(backend.WakeWordBackendError, match="TOKEN_UNKNOWN"):
        backend._StreamingSpotter(config)
    Path(backend.model_files(str(tmp_path))["tokens"]).write_text("x 0\n", encoding="utf-8")
    worker = backend._StreamingSpotter(config)
    assert worker.runtime_version == "1.13.8+neko.kws2"
    assert worker.native_version == "1.13.8+neko.kws2"
    assert worker.labels == {"name"}
    assert len(configured) == 1
    assert not Path(configured[0]["keywords_file"]).exists()


class WorkerConnection:
    def __init__(self):
        self.responses = []
        self.requests = [(frame(), 7)]
        self.closed = False

    def send(self, result):
        self.responses.append(result)

    def recv(self):
        if self.requests:
            return self.requests.pop()
        raise EOFError

    def close(self):
        self.closed = True


@pytest.mark.parametrize("value", [True, False, 0, -1, 17, 8.0, "8", None])
def test_search_budget_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="WAKE_WORD_MAX_ACTIVE_PATHS_INVALID"):
        backend.SherpaWakeWordConfig("unused", ("x @name",), max_active_paths=value)


@pytest.mark.parametrize("value", [None, 1, 4, 8, 16])
def test_search_budget_reaches_native_constructor(tmp_path, monkeypatch, value):
    configured = {}

    def constructor(**kwargs):
        configured.update(kwargs)
        return FakeSpotter()

    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        KeywordSpotter=constructor, __version__=backend.SUPPORTED_RUNTIME_VERSION,
        version=backend.SUPPORTED_RUNTIME_VERSION,
    ))
    for path in backend.model_files(str(tmp_path)).values():
        Path(path).write_text("x 0\n", encoding="utf-8")
    overrides = {} if value is None else {"max_active_paths": value}
    config = backend.SherpaWakeWordConfig(str(tmp_path), ("x @name",), **overrides)
    backend._StreamingSpotter(config)
    assert configured["max_active_paths"] == (8 if value is None else value)
    assert configured["keywords_threshold"] == 0.25
    assert configured["keywords_score"] == 1.0
    assert configured["num_threads"] == 1


def test_worker_ready_diagnostic_reports_search_configuration(monkeypatch, capsys):
    monkeypatch.setenv("NEKO_WAKE_WORD_DIAGNOSTICS", "1")
    worker = streaming()
    # Distinguish the worker observation from a hardcoded expected-version log.
    worker.runtime_version = "version-observed-in-worker"
    worker.native_version = "core-version-observed-in-worker"
    monkeypatch.setattr(backend, "_StreamingSpotter", lambda config: worker)
    connection = WorkerConnection()
    backend._worker(connection, backend.SherpaWakeWordConfig("unused", ("x @name",)))
    output = capsys.readouterr().out
    assert "runtime_version=version-observed-in-worker" in output
    assert connection.responses[0][1]["runtime_version"] == worker.runtime_version
    assert "native_version=core-version-observed-in-worker" in output
    assert connection.responses[0][1]["native_version"] == worker.native_version
    assert "max_active_paths=8" in output
    assert "keyword_threshold=0.25" in output
    assert "keyword_score=1.0" in output
    assert "num_threads=1" in output


@pytest.mark.parametrize("failure", [False, True])
def test_worker_dispatches_and_reports_failure_without_audio_or_exception_detail(monkeypatch, failure):
    worker = streaming()

    def constructor(config):
        if failure:
            raise RuntimeError("private backend detail")
        return worker

    monkeypatch.setattr(backend, "_StreamingSpotter", constructor)
    connection = WorkerConnection()
    backend._worker(connection, backend.SherpaWakeWordConfig("unused", ("x @name",)))
    assert connection.closed
    if failure:
        assert connection.responses == [(False, "WAKE_WORD_WORKER_FAILED")]
    else:
        assert connection.responses == [
            (True, ready_info(backend.SherpaWakeWordConfig("unused", ("x @name",)))),
            (True, None),
        ]


@pytest.mark.asyncio
async def test_worker_metadata_is_received_read_only_and_cleared_on_close(monkeypatch):
    monkeypatch.setattr(backend, "_worker", responsive_worker)
    config = backend.SherpaWakeWordConfig("unused", ("x @name",), max_active_paths=4)
    detector = backend.SherpaWakeWordDetector(config)
    assert detector.runtime_info is None
    try:
        await detector.prepare()
        assert dict(detector.runtime_info) == ready_info(config)
        with pytest.raises(TypeError):
            detector.runtime_info["runtime_version"] = "unverified"
        assert await detector.prepare() is None
        assert await detector.feed(frame(), 1) is None
    finally:
        await detector.close()
    assert detector.runtime_info is None


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [None, {}, {"runtime_version": "1.13.8+neko.kws1"},
                                  {**ready_info(backend.SherpaWakeWordConfig("unused", ("x @name",))),
                                   "max_active_paths": 16}])
async def test_unverified_ready_payload_never_marks_detector_ready(monkeypatch, reply):
    detector = backend.SherpaWakeWordDetector(backend.SherpaWakeWordConfig("unused", ("x @name",)))
    monkeypatch.setattr(detector, "_launch", lambda: None)
    monkeypatch.setattr(detector, "_exchange", lambda request, timeout: reply)
    with pytest.raises(backend.WakeWordBackendError, match="RUNTIME_INFO_INVALID"):
        await detector.prepare()
    assert detector.runtime_info is None
    assert detector._closed.is_set() and not detector._ready


@pytest.mark.parametrize("version", [None, "1.13.8", "1.13.8+neko.kws1"])
def test_worker_rejects_old_runtime_without_ready_event(monkeypatch, capsys, version):
    monkeypatch.setenv("NEKO_WAKE_WORD_DIAGNOSTICS", "1")
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        __version__=version, version=backend.SUPPORTED_RUNTIME_VERSION))
    config = backend.SherpaWakeWordConfig("unused", ("x @name",))
    # Reject specifically at the package-version gate, before unrelated missing
    # assets could produce the same generic worker-failure response.
    with pytest.raises(backend.WakeWordBackendError, match="RUNTIME_FIX_REQUIRED"):
        backend._StreamingSpotter(config)
    connection = WorkerConnection()
    backend._worker(connection, config)
    assert connection.responses == [(False, "WAKE_WORD_WORKER_FAILED")]
    assert connection.closed
    assert "event=ready" not in capsys.readouterr().out


@pytest.mark.parametrize("native_version", [None, "1.13.8", "1.13.8+neko.kws1"])
def test_supported_package_with_old_native_core_never_reports_ready(monkeypatch, capsys, native_version):
    monkeypatch.setenv("NEKO_WAKE_WORD_DIAGNOSTICS", "1")
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
        __version__=backend.SUPPORTED_RUNTIME_VERSION, version=native_version))
    config = backend.SherpaWakeWordConfig("unused", ("x @name",))
    with pytest.raises(backend.WakeWordBackendError, match="RUNTIME_FIX_REQUIRED"):
        backend._StreamingSpotter(config)
    connection = WorkerConnection()
    backend._worker(connection, config)
    assert connection.responses == [(False, "WAKE_WORD_WORKER_FAILED")]
    assert connection.closed
    assert "event=ready" not in capsys.readouterr().out


def test_native_result_is_consumed_once_with_its_timestamps(monkeypatch):
    worker = streaming()
    calls = []

    def consume(stream):
        calls.append(stream)
        assert len(calls) == 1, "Native result retrieval consumes the pending result"
        return SimpleNamespace(keyword="name", timestamps=[0.02, 0.08])

    monkeypatch.setattr(worker.spotter, "get_result", consume)
    worker.spotter.ready = True
    result = worker.feed(frame(), 1)
    assert len(calls) == 1
    assert (result.sample_start, result.sample_end) == (16320, 17920)
