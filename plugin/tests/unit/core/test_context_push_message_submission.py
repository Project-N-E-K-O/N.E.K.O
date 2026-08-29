from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import ormsgpack
import pytest

from plugin import settings
from plugin.core import context as context_module
from plugin.core.context import PluginContext


class _Logger:
    def __init__(self) -> None:
        self.records: list[tuple[str, tuple[object, ...]]] = []

    def debug(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append((message, args))

    def warning(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append((message, args))

    def error(self, message: str, *args: object, **kwargs: object) -> None:
        self.records.append((message, args))


class _Socket:
    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self.connect_error = connect_error
        self.send_error = send_error
        self.sent: list[bytes] = []

    def setsockopt(self, *_args: object) -> None:
        return None

    def connect(self, _endpoint: str) -> None:
        if self.connect_error is not None:
            raise self.connect_error

    def send(self, payload: bytes, *, flags: int) -> None:
        assert flags == 0
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(payload)


class _Queue:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.items: list[dict[str, Any]] = []

    def put_nowait(self, payload: dict[str, Any]) -> None:
        if self.error is not None:
            raise self.error
        self.items.append(payload)


class _Again(Exception):
    pass


def _context(tmp_path: Path, *, message_queue: object = None) -> tuple[PluginContext, _Logger]:
    logger = _Logger()
    return (
        PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo" / "plugin.toml",
            logger=logger,  # type: ignore[arg-type]
            status_queue=None,
            message_queue=message_queue,
        ),
        logger,
    )


def _install_slow_message_plane(
    monkeypatch: pytest.MonkeyPatch,
    socket: _Socket,
) -> None:
    class _ZmqContext:
        @staticmethod
        def instance() -> object:
            return SimpleNamespace(socket=lambda _kind: socket)

    monkeypatch.setattr(
        context_module,
        "zmq",
        SimpleNamespace(
            Again=_Again,
            Context=_ZmqContext,
            PUSH=1,
            LINGER=2,
            SNDTIMEO=3,
        ),
    )
    monkeypatch.setattr(
        context_module,
        "ormsgpack",
        SimpleNamespace(packb=lambda _payload: b"packed-message"),
    )
    monkeypatch.setattr(
        settings,
        "MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
        "inproc://submission-test",
    )


@pytest.mark.plugin_unit
def test_slow_message_plane_success_reports_local_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _Socket()
    _install_slow_message_plane(monkeypatch, socket)
    ctx, _logger = _context(tmp_path)

    result = ctx.push_message(
        visibility=[],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "synthetic payload"}],
    )

    assert result == {"submitted": True}
    assert socket.sent == [b"packed-message"]


@pytest.mark.plugin_unit
def test_slow_message_plane_failure_uses_fallback_and_is_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "private-payload-must-not-enter-logs"
    _install_slow_message_plane(
        monkeypatch,
        _Socket(send_error=RuntimeError(private_marker)),
    )
    fallback_queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=fallback_queue)

    result = ctx.push_message(
        visibility=[],
        ai_behavior="respond",
        parts=[{"type": "text", "text": private_marker}],
    )

    assert result == {"submitted": True}
    assert len(fallback_queue.items) == 1
    assert private_marker not in repr(logger.records)
    assert "RuntimeError" in repr(logger.records)


@pytest.mark.plugin_unit
def test_slow_message_plane_backpressure_fallback_reports_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "private-backpressure-detail"
    _install_slow_message_plane(
        monkeypatch,
        _Socket(send_error=_Again(private_marker)),
    )
    fallback_queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=fallback_queue)

    result = ctx.push_message(
        visibility=[],
        ai_behavior="respond",
        parts=[{"type": "text", "text": private_marker}],
    )

    assert result == {"submitted": True}
    assert len(fallback_queue.items) == 1
    assert private_marker not in repr(logger.records)
    assert "_Again" in repr(logger.records)


@pytest.mark.plugin_unit
def test_slow_message_plane_backpressure_is_reported_when_fallback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_send_marker = "private-backpressure-detail"
    private_queue_marker = "private-queue-detail"
    _install_slow_message_plane(
        monkeypatch,
        _Socket(send_error=_Again(private_send_marker)),
    )
    fallback_queue = _Queue(error=RuntimeError(private_queue_marker))
    ctx, logger = _context(tmp_path, message_queue=fallback_queue)

    result = ctx.push_message(
        visibility=[],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "synthetic payload"}],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "backpressure",
    }
    assert fallback_queue.items == []
    assert private_send_marker not in repr(logger.records)
    assert private_queue_marker not in repr(logger.records)
    assert "_Again" in repr(logger.records)
    assert "RuntimeError" in repr(logger.records)


@pytest.mark.plugin_unit
def test_slow_message_plane_and_fallback_failures_are_distinguishable_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_send_marker = "private-send-error"
    private_queue_marker = "private-queue-error"
    _install_slow_message_plane(
        monkeypatch,
        _Socket(send_error=RuntimeError(private_send_marker)),
    )
    fallback_queue = _Queue(error=RuntimeError(private_queue_marker))
    ctx, logger = _context(tmp_path, message_queue=fallback_queue)

    result = ctx.push_message(
        visibility=[],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "synthetic payload"}],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "transport_error",
    }
    assert fallback_queue.items == []
    assert private_send_marker not in repr(logger.records)
    assert private_queue_marker not in repr(logger.records)
    assert "RuntimeError" in repr(logger.records)


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    ("enqueue_error", "expected"),
    [
        (None, {"submitted": True}),
        (
            RuntimeError("queue full"),
            {
                "ok": False,
                "submitted": False,
                "reason": "backpressure",
            },
        ),
    ],
)
def test_fast_batcher_reports_enqueue_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enqueue_error: Exception | None,
    expected: dict[str, object],
) -> None:
    class _Batcher:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def stop(self, *, timeout: float) -> None:
            assert timeout == 2.0

        def enqueue(self, _item: dict[str, object]) -> None:
            if enqueue_error is not None:
                raise enqueue_error

    from plugin.utils import zeromq_ipc

    monkeypatch.setattr(context_module, "zmq", object())
    monkeypatch.setattr(
        settings,
        "MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
        "inproc://submission-test",
    )
    monkeypatch.setattr(zeromq_ipc, "MessagePlaneIngestBatcher", _Batcher)
    ctx, _logger = _context(tmp_path)

    with pytest.warns(DeprecationWarning, match="fast_mode.*v0.9"):
        result = ctx.push_message(parts=[], fast_mode=True)

    assert result == expected


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_async_wrapper_returns_fallback_queue_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = _Queue()
    monkeypatch.setattr(context_module, "zmq", None)
    ctx, _logger = _context(tmp_path, message_queue=queue)

    result = await ctx.push_message_async(parts=[])

    assert result == {"submitted": True}
    assert len(queue.items) == 1


@pytest.mark.plugin_unit
def test_fallback_queue_failure_is_distinguishable_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "private-queue-error"
    queue = _Queue(error=RuntimeError(private_marker))
    monkeypatch.setattr(context_module, "zmq", None)
    ctx, logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        parts=[{"type": "text", "text": private_marker}],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "transport_error",
    }
    assert private_marker not in repr(logger.records)
    assert "RuntimeError" in repr(logger.records)


@pytest.mark.plugin_unit
def test_fallback_queue_backpressure_is_classified_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "private-queue-backpressure"
    queue = _Queue(error=_Again(private_marker))
    monkeypatch.setattr(context_module, "zmq", SimpleNamespace(Again=_Again))
    monkeypatch.setattr(settings, "MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT", "")
    ctx, logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        parts=[{"type": "text", "text": private_marker}],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "backpressure",
    }
    assert private_marker not in repr(logger.records)
    assert "_Again" in repr(logger.records)


@pytest.mark.plugin_unit
def test_missing_transports_report_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context_module, "zmq", None)
    ctx, _logger = _context(tmp_path)

    result = ctx.push_message(parts=[])

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "transport_unavailable",
    }


@pytest.mark.plugin_unit
def test_primary_setup_failure_can_use_fallback_before_submission_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_marker = "private-connect-detail"
    _install_slow_message_plane(
        monkeypatch,
        _Socket(connect_error=RuntimeError(private_marker)),
    )
    fallback_queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=fallback_queue)

    result = ctx.push_message(
        visibility=[],
        ai_behavior="respond",
        parts=[{"type": "text", "text": private_marker}],
    )

    assert result == {"submitted": True}
    assert len(fallback_queue.items) == 1
    assert private_marker not in repr(logger.records)
    assert "RuntimeError" in repr(logger.records)


# ---------------------------------------------------------------------------
# Local payload-size rejection (host parity)
#
# The host's ingest server measures len(ormsgpack.packb(payload)) of each delta
# item against MESSAGE_PLANE_PAYLOAD_MAX_BYTES and drops the whole item on
# overflow, in a different process, after push_message() has already returned.
# These tests pin the SDK-side probe that turns that into a synchronous verdict
# -- including the hot-path gate, which is the part most likely to rot: an
# unconditional pack here would be a permanent tax on every text-only cue.
# ---------------------------------------------------------------------------


class _PackCounter:
    """Real ormsgpack behind a counter, so tests can price each push.

    Sizes have to be real for the limit comparison to mean anything, so this
    delegates instead of returning a canned blob like
    ``_install_slow_message_plane`` does.
    """

    def __init__(self) -> None:
        self.sizes: list[int] = []

    @property
    def calls(self) -> int:
        return len(self.sizes)

    def packb(self, payload: object) -> bytes:
        encoded = ormsgpack.packb(payload)
        self.sizes.append(len(encoded))
        return encoded


class _RecordingBatcher:
    """Fast-path batcher stand-in that keeps whatever reached the queue."""

    items: list[dict[str, object]] = []

    def __init__(self, **_kwargs: object) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self, *, timeout: float) -> None:
        assert timeout == 2.0

    def enqueue(self, item: dict[str, object]) -> None:
        type(self).items.append(item)


def _install_counting_slow_plane(
    monkeypatch: pytest.MonkeyPatch,
    socket: _Socket,
) -> _PackCounter:
    _install_slow_message_plane(monkeypatch, socket)
    counter = _PackCounter()
    monkeypatch.setattr(
        context_module,
        "ormsgpack",
        SimpleNamespace(packb=counter.packb),
    )
    return counter


def _install_counting_fast_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_PackCounter, type[_RecordingBatcher]]:
    from plugin.utils import zeromq_ipc

    counter = _PackCounter()
    batcher = type("_Batcher", (_RecordingBatcher,), {"items": []})
    monkeypatch.setattr(context_module, "zmq", object())
    monkeypatch.setattr(
        context_module,
        "ormsgpack",
        SimpleNamespace(packb=counter.packb),
    )
    monkeypatch.setattr(
        settings,
        "MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
        "inproc://submission-test",
    )
    monkeypatch.setattr(zeromq_ipc, "MessagePlaneIngestBatcher", batcher)
    return counter, batcher


def _inline_image_part(raw_bytes: int) -> dict[str, object]:
    return {
        "type": "image",
        "data": b"\x00" * raw_bytes,
        "mime": "image/png",
    }


@pytest.mark.plugin_unit
def test_oversized_inline_push_is_rejected_before_the_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _Socket()
    _install_counting_slow_plane(monkeypatch, socket)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    fallback_queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=fallback_queue)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "look"}, _inline_image_part(8192)],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "payload_too_large",
    }
    assert socket.sent == []
    # The legacy control-plane queue must not become a silent second chance:
    # it would carry the same oversized payload to a different consumer, which
    # is the invisible non-delivery this rejection exists to end.
    assert fallback_queue.items == []
    # The author has to be able to act on the log line alone.
    reported = repr(logger.records)
    assert "payload_too_large" in reported
    assert "4096" in reported
    assert "2.34" in reported
    assert "ctx.images.upload" in reported


@pytest.mark.plugin_unit
def test_oversized_inline_fast_push_is_not_enqueued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _counter, batcher = _install_counting_fast_plane(monkeypatch)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    ctx, _logger = _context(tmp_path)

    with pytest.warns(DeprecationWarning, match="fast_mode.*v0.9"):
        result = ctx.push_message(
            visibility=["chat"],
            ai_behavior="read",
            parts=[_inline_image_part(8192)],
            fast_mode=True,
        )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "payload_too_large",
    }
    assert batcher.items == []


@pytest.mark.plugin_unit
def test_text_only_fast_push_pays_no_size_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter, batcher = _install_counting_fast_plane(monkeypatch)
    ctx, _logger = _context(tmp_path)

    with pytest.warns(DeprecationWarning, match="fast_mode.*v0.9"):
        result = ctx.push_message(
            visibility=["chat"],
            ai_behavior="read",
            parts=[{"type": "text", "text": "a high-frequency cue"}],
            fast_mode=True,
        )

    assert result == {"submitted": True}
    assert len(batcher.items) == 1
    # The batcher does its own packing on its own thread; push_message itself
    # must pack nothing at all for a text-only cue.
    assert counter.calls == 0


@pytest.mark.plugin_unit
def test_text_only_slow_push_packs_only_the_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _Socket()
    counter = _install_counting_slow_plane(monkeypatch, socket)
    ctx, _logger = _context(tmp_path)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="read",
        parts=[{"type": "text", "text": "a high-frequency cue"}],
    )

    assert result == {"submitted": True}
    assert len(socket.sent) == 1
    # One pack: the ZMQ envelope. A second one would mean the probe went
    # unconditional and every text cue now pays for the rare image push.
    assert counter.calls == 1


@pytest.mark.plugin_unit
def test_small_inline_push_still_submits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket = _Socket()
    counter = _install_counting_slow_plane(monkeypatch, socket)
    ctx, _logger = _context(tmp_path)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "a tiny icon"}, _inline_image_part(64)],
    )

    assert result == {"submitted": True}
    assert len(socket.sent) == 1
    # Probe + envelope, and the probe measured well under the real default cap.
    assert counter.calls == 2
    assert counter.sizes[0] < int(settings.MESSAGE_PLANE_PAYLOAD_MAX_BYTES)
