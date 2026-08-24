from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
