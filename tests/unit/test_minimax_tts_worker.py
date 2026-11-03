import json
import queue
import threading
import time

import numpy as np
import pytest
import httpx

from main_logic import tts_client


class ControlledQueue:
    def __init__(self):
        self._queue = queue.Queue()
        self._stop = object()

    def put(self, item):
        self._queue.put(item)

    def get(self, timeout=None):
        item = self._queue.get(timeout=timeout)
        if item is self._stop:
            raise EOFError("queue closed")
        return item

    def empty(self):
        return self._queue.empty()

    def close(self):
        self._queue.put(self._stop)


def _start_worker(request_queue, response_queue, base_url="https://api.minimaxi.com"):
    thread = threading.Thread(
        target=tts_client.minimax_tts_worker,
        args=(request_queue, response_queue, "test-minimax-key", "custom_test_voice", base_url),
        daemon=True,
    )
    thread.start()
    return thread


def _wait_for_queue_item(q, predicate, timeout=5.0):
    deadline = time.time() + timeout
    seen = []
    while time.time() < deadline:
        remaining = max(0.01, deadline - time.time())
        try:
            item = q.get(timeout=remaining)
        except queue.Empty:
            continue
        seen.append(item)
        if predicate(item):
            return item, seen
    raise AssertionError(f"Timed out waiting for queue item, seen={seen!r}")


# ---------------------------------------------------------------------------
# Fake httpx transport for testing
# ---------------------------------------------------------------------------


class FakeSSETransport(httpx.AsyncBaseTransport):
    """Fake httpx transport that returns SSE or JSON responses for MiniMax TTS."""

    def __init__(self, sse_events=None, status_code=200, probe_status=400,
                 use_sse=True, trailing_newline=True):
        self._sse_events = sse_events or []
        self._status_code = status_code
        self._probe_status = probe_status
        self._use_sse = use_sse
        self._trailing_newline = trailing_newline
        self.requests = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.requests.append(body)

        # Probe request (empty text, stream=False)
        if not body.get("stream", False):
            return httpx.Response(self._probe_status, json={"base_resp": {"status_code": 0}})

        # Streaming synthesis request
        if self._use_sse:
            # SSE format: data: {json}\n\n
            sse_body = ""
            for i, event in enumerate(self._sse_events):
                sse_body += f"data: {json.dumps(event)}"
                if i < len(self._sse_events) - 1 or self._trailing_newline:
                    sse_body += "\n\n"
            return httpx.Response(
                self._status_code,
                content=sse_body.encode("utf-8"),
                headers={"content-type": "text/event-stream"},
            )
        else:
            # JSON stream format: line-delimited JSON objects
            json_body = ""
            for i, event in enumerate(self._sse_events):
                json_body += json.dumps(event)
                if i < len(self._sse_events) - 1 or self._trailing_newline:
                    json_body += "\n"
            return httpx.Response(
                self._status_code,
                content=json_body.encode("utf-8"),
                headers={"content-type": "application/json"},
            )


def _make_audio_sse_events(pcm_bytes: bytes, num_chunks: int = 1):
    """Generate SSE event dicts with hex-encoded PCM audio."""
    events = []
    chunk_size = max(1, len(pcm_bytes) // num_chunks)
    for i in range(num_chunks):
        start = i * chunk_size
        end = start + chunk_size if i < num_chunks - 1 else len(pcm_bytes)
        chunk = pcm_bytes[start:end]
        status = 2 if i == num_chunks - 1 else 1
        events.append({
            "data": {"audio": chunk.hex(), "status": status},
            "trace_id": "test-trace",
            "base_resp": {"status_code": 0, "status_msg": "success"},
        })
    return events


# ---------------------------------------------------------------------------
# URL helper tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_get_minimax_tts_http_url():
    assert tts_client._get_minimax_tts_http_url("https://api.minimaxi.com/v1") == "https://api.minimaxi.com/v1/t2a_v2"
    assert tts_client._get_minimax_tts_http_url("https://api.minimax.io") == "https://api.minimax.io/v1/t2a_v2"
    # ws/wss conversion.
    assert tts_client._get_minimax_tts_http_url("wss://api.minimaxi.com") == "https://api.minimaxi.com/v1/t2a_v2"
    # Defaults.
    assert tts_client._get_minimax_tts_http_url(None) == "https://api.minimaxi.com/v1/t2a_v2"


# ---------------------------------------------------------------------------
# Worker integration tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_minimax_worker_streams_audio_via_sse(monkeypatch):
    pcm_bytes = (np.arange(3000, dtype=np.int16)).tobytes()
    sse_events = _make_audio_sse_events(pcm_bytes, num_chunks=2)
    transport = FakeSSETransport(sse_events=sse_events)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    ready_item, _ = _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))
    assert ready_item == ("__ready__", True)

    request_queue.put(("speech-1", "你好"))
    request_queue.put(("speech-1", "世界今天"))
    request_queue.put((None, None))

    audio_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[1] == "speech-1"
    assert isinstance(audio_item[2], bytes)
    assert len(audio_item[2]) > 0

    # Verify the synthesis request was sent with accumulated text
    synth_requests = [r for r in transport.requests if r.get("stream")]
    assert len(synth_requests) == 1
    assert synth_requests[0]["text"] == "你好世界今天"

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_streams_audio_via_json_format(monkeypatch):
    """Test JSON stream format (application/json) instead of SSE"""
    pcm_bytes = (np.arange(3000, dtype=np.int16)).tobytes()
    json_events = _make_audio_sse_events(pcm_bytes, num_chunks=2)
    transport = FakeSSETransport(sse_events=json_events, use_sse=False)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-1", "你好"))
    request_queue.put(("speech-1", "世界今天"))
    request_queue.put((None, None))

    audio_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[0] == "__audio__"
    assert audio_item[1] == "speech-1"
    assert isinstance(audio_item[2], bytes)
    assert len(audio_item[2]) > 0

    # Verify the synthesis request was sent
    synth_requests = [r for r in transport.requests if r.get("stream")]
    assert len(synth_requests) == 1
    assert synth_requests[0]["text"] == "你好世界今天"

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_auth_failure_reports_not_ready(monkeypatch):
    transport = FakeSSETransport(probe_status=401)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    not_ready_item, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
    )
    assert not_ready_item == ("__ready__", False)
    assert any(isinstance(item, tuple) and item[0] == "__error__" for item in seen)

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_switches_speech_id_discards_old_buffer(monkeypatch):
    pcm_bytes = (np.arange(2500, dtype=np.int16)).tobytes()
    sse_events = _make_audio_sse_events(pcm_bytes, num_chunks=1)
    transport = FakeSSETransport(sse_events=sse_events)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    # Send text for old speech, then switch to new speech before flush
    request_queue.put(("speech-old", "abcdef"))
    request_queue.put(("speech-new", "ghijkl"))
    request_queue.put((None, None))

    audio_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[1] == "speech-new"

    # Only one synthesis request for the new speech
    synth_requests = [r for r in transport.requests if r.get("stream")]
    assert len(synth_requests) == 1
    assert synth_requests[0]["text"] == "ghijkl"

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_interrupt_clears_buffer(monkeypatch):
    pcm_bytes = (np.arange(1000, dtype=np.int16)).tobytes()
    sse_events = _make_audio_sse_events(pcm_bytes)
    transport = FakeSSETransport(sse_events=sse_events)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    # Buffer some text, then interrupt, then close
    request_queue.put(("speech-1", "abcdef"))
    request_queue.put(("__interrupt__", None))
    request_queue.put((None, None))  # flush with no buffer → no synthesis
    request_queue.close()

    thread.join(timeout=3.0)
    assert not thread.is_alive()

    # No synthesis should have happened (only probe request)
    synth_requests = [r for r in transport.requests if r.get("stream")]
    assert len(synth_requests) == 0


@pytest.mark.unit
def test_minimax_worker_server_error_in_sse(monkeypatch):
    error_events = [{
        "data": {"audio": "", "status": 1},
        "trace_id": "test-trace",
        "base_resp": {"status_code": 1000, "status_msg": "internal error"},
    }]
    transport = FakeSSETransport(sse_events=error_events)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-1", "hello"))
    request_queue.put((None, None))

    # Should get an error
    error_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 2 and item[0] == "__error__",
    )
    assert "internal error" in error_item[1]

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_residual_buffer_without_trailing_newline(monkeypatch):
    """Parse the final buffered event even if server closes stream without trailing newline."""
    pcm_bytes = (np.arange(2000, dtype=np.int16)).tobytes()
    # One event only, without trailing newline.
    sse_events = _make_audio_sse_events(pcm_bytes, num_chunks=1)
    transport = FakeSSETransport(sse_events=sse_events, trailing_newline=False)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put(("speech-1", "hello"))
    request_queue.put((None, None))

    # Parse and emit audio even without trailing newline.
    audio_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[1] == "speech-1"
    assert len(audio_item[2]) > 0

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_probe_rejects_5xx(monkeypatch):
    """Probe reports not ready on 500 or any status outside 200/400."""
    transport = FakeSSETransport(probe_status=500)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    not_ready_item, seen = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
    )
    assert not_ready_item == ("__ready__", False)
    assert any(isinstance(item, tuple) and item[0] == "__error__" for item in seen)

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_probe_rejects_404(monkeypatch):
    """Probe reports not ready on 404."""
    transport = FakeSSETransport(probe_status=404)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    not_ready_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: item == ("__ready__", False),
    )
    assert not_ready_item == ("__ready__", False)

    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_minimax_worker_incremental_synthesis_on_punctuation(monkeypatch):
    """Sentence-ending punctuation starts synthesis immediately, without waiting for flush."""
    pcm_bytes = (np.arange(2000, dtype=np.int16)).tobytes()
    sse_events = _make_audio_sse_events(pcm_bytes, num_chunks=1)
    transport = FakeSSETransport(sse_events=sse_events)

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = ControlledQueue()
    response_queue = queue.Queue()
    thread = _start_worker(request_queue, response_queue)

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    # First sentence has period; second does not (synthesized on flush).
    request_queue.put(("speech-1", "你好世界。"))
    request_queue.put(("speech-1", "今天天气"))
    request_queue.put((None, None))

    # Audio should arrive.
    audio_item, _ = _wait_for_queue_item(
        response_queue,
        lambda item: isinstance(item, tuple) and len(item) == 3 and item[0] == "__audio__",
    )
    assert audio_item[1] == "speech-1"

    # Second synthesis starts on worker thread after (None, None), crossing threads. Fixed sleeps
    # are a coin flip on Windows (15.6ms clock precision; busy-loop sleep can provide zero wait).
    # close() puts stop sentinel at FIFO tail, so preceding (None, None) must finish first;
    # join returning proves both syntheses dispatched, a deterministic completion point.
    request_queue.close()
    thread.join(timeout=3.0)
    assert not thread.is_alive()

    # Expect two syntheses: sentence-period split and flush.
    synth_requests = [r for r in transport.requests if r.get("stream")]
    assert len(synth_requests) == 2
    assert synth_requests[0]["text"] == "你好世界。"
    assert synth_requests[1]["text"] == "今天天气"


# ---------------------------------------------------------------------------
# Shutdown sentinel regression tests
#
# History: multiprocessing workers closed via process.terminate(); (None, None) was only an extra
# hint. After December 2025 threading migration removed .terminate(), shutdown sentinel remained unchanged,
# making worker treat (None, None) as utterance end; thread.join(2.0) always timed out,
# leaking threads. Lock in distinct (None, None) and ("__shutdown__", None) semantics.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_shutdown_sentinel_exits_minimax_worker(monkeypatch):
    """minimax (_non_bistream_tts_main_loop) exits within two seconds on __shutdown__."""
    transport = FakeSSETransport(sse_events=[])

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = queue.Queue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.minimax_tts_worker,
        args=(request_queue, response_queue, "test-key", "v", "https://api.minimaxi.com"),
        daemon=True,
    )
    thread.start()

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "worker 未在 2 秒内响应 __shutdown__ 退出"


@pytest.mark.unit
def test_none_none_does_not_exit_minimax_worker(monkeypatch):
    """(None, None) flushes utterance end, never exits the worker."""
    transport = FakeSSETransport(sse_events=[])

    original_async_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched_client)

    request_queue = queue.Queue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.minimax_tts_worker,
        args=(request_queue, response_queue, "test-key", "v", "https://api.minimaxi.com"),
        daemon=True,
    )
    thread.start()

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put((None, None))
    thread.join(timeout=1.0)
    assert thread.is_alive(), "(None, None) 是 flush 信号，不应让 worker 退出"

    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=2.0)
    assert not thread.is_alive()


@pytest.mark.unit
def test_shutdown_sentinel_exits_dummy_worker():
    """Dummy worker continues on (None, None), exits only on __shutdown__."""
    request_queue = queue.Queue()
    response_queue = queue.Queue()
    thread = threading.Thread(
        target=tts_client.dummy_tts_worker,
        args=(request_queue, response_queue, "k", "v"),
        daemon=True,
    )
    thread.start()

    _wait_for_queue_item(response_queue, lambda item: item == ("__ready__", True))

    request_queue.put((None, None))
    thread.join(timeout=0.5)
    assert thread.is_alive(), "dummy (None, None) 不应退出"

    request_queue.put((tts_client.TTS_SHUTDOWN_SENTINEL, None))
    thread.join(timeout=2.0)
    assert not thread.is_alive()
