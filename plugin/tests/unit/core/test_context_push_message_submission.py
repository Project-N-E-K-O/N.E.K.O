from __future__ import annotations

import base64
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
    # 1.33x, not 2.34x: an inline payload travels base64 and nothing else.
    assert "1.33" in reported
    assert "image=" in reported
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
def test_text_only_fast_push_pays_exactly_one_size_probe(
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
    # Exactly one pack: the size probe.
    #
    # This asserted ZERO until the probe stopped skipping payloads with no
    # inline carrier. That skip existed to keep this high-frequency path cheap,
    # and it was the wrong trade: the host measures the WHOLE envelope, so an
    # oversized text cue was dropped there as payload_too_big while
    # push_message() had already answered submitted=True. Measured before
    # removing it -- 0.19 us to pack a 248 B cue -- which is what the silence
    # was buying.
    #
    # What still needs pinning is that the probe costs ONE pack, not one per
    # part: this number going up with the part count is the regression to
    # catch. The batcher does its own packing later, on its own thread.
    assert counter.calls == 1


@pytest.mark.plugin_unit
def test_text_only_slow_push_packs_probe_then_envelope(
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
    # Two packs: the size probe, then the ZMQ envelope.
    #
    # This asserted ONE while the probe skipped carrier-free payloads. That
    # skip is gone: the host measures the whole envelope regardless, so a big
    # text cue was being dropped there after the author was told it went out.
    # A THIRD pack would mean something started packing per part rather than
    # once per push, which is the cost regression worth catching here.
    assert counter.calls == 2


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


# ---------------------------------------------------------------------------
# Remediation follows the payload that actually spent the budget
#
# The probe fires on any part carrying binary_base64, audio and video included,
# but ctx.images.upload() exists only for images. A rejection that always named
# it sent the author of an inline audio part after an API that is not there
# (Codex). What the rejection says now depends on what the envelope carries.
# ---------------------------------------------------------------------------


def _inline_part(part_type: str, raw_bytes: int, mime: str) -> dict[str, object]:
    return {"type": part_type, "data": b"\x00" * raw_bytes, "mime": mime}


def _carrier_summary(logger: _Logger) -> str:
    """Pull the ``type=NB ...`` breakdown out of the recorded log args."""
    for _message, args in logger.records:
        for arg in args:
            if isinstance(arg, str) and arg[:1].isalpha() and "=" in arg and arg.endswith("B"):
                return arg
    raise AssertionError(f"no carrier breakdown was logged: {logger.records!r}")


@pytest.mark.plugin_unit
def test_oversized_inline_audio_is_not_sent_after_the_image_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inline audio part gets advice that exists: shrink it, or send a URL."""
    socket = _Socket()
    _install_counting_slow_plane(monkeypatch, socket)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    ctx, logger = _context(tmp_path)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="read",
        parts=[_inline_part("audio", 8192, "audio/wav")],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "payload_too_large",
    }
    assert socket.sent == []
    reported = repr(logger.records)
    # The whole point: no pointer at an upload helper that does not exist for
    # this part type.
    assert "images.upload" not in reported
    # It has to name the offender and offer something an author can act on.
    assert "audio=" in reported
    assert "inline audio payload" in reported
    assert "url=" in reported


@pytest.mark.plugin_unit
def test_oversized_inline_image_is_still_pointed_at_the_upload_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The image-specific advice survives: images DO have an upload helper."""
    socket = _Socket()
    _install_counting_slow_plane(monkeypatch, socket)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    ctx, logger = _context(tmp_path)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[_inline_part("image", 8192, "image/png")],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "payload_too_large",
    }
    reported = repr(logger.records)
    assert "image=" in reported
    assert "ctx.images.upload" in reported
    # ...and it must not also hand out the "there is no helper" line.
    assert "no upload helper" not in reported


@pytest.mark.plugin_unit
def test_remediation_follows_the_biggest_carrier_not_the_first_one(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tiny leading thumbnail must not claim a rejection an audio clip caused.

    Wire order would name the image, because it is part[0]. The budget was
    spent by the clip, so that is what the advice has to be about -- while
    still telling the author the thumbnail can be offloaded too.
    """
    socket = _Socket()
    _install_counting_slow_plane(monkeypatch, socket)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    ctx, logger = _context(tmp_path)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="read",
        parts=[
            _inline_part("image", 64, "image/png"),
            _inline_part("audio", 8192, "audio/wav"),
        ],
    )

    assert result["reason"] == "payload_too_large"
    reported = repr(logger.records)
    assert "inline audio payload" in reported
    # Both carriers are itemised, biggest first, so the arithmetic is checkable.
    # The stub logger keeps the template and its args apart, so the breakdown is
    # read out of the args rather than out of a formatted line.
    summary = _carrier_summary(logger)
    assert summary.startswith("audio=")
    assert "image=" in summary
    # The image is still worth offloading; the advice says so without pretending
    # it was the cause.
    assert "ctx.images.upload" in reported


# ---------------------------------------------------------------------------
# The effective ceiling is now a real 256 KiB
#
# The envelope used to carry every inline image TWICE -- base64 in
# parts[].binary_base64 and raw again in the legacy binary_data compat field,
# ~2.34x the picture -- so a 256 KiB cap really only admitted ~110 KiB. The
# duplicate is gone (base64 alone is ~1.34x) and the cap is 512 KiB, which is
# what makes the documented 256 KiB inline image actually fit.
# ---------------------------------------------------------------------------


@pytest.mark.plugin_unit
def test_a_256_kib_inline_image_fits_the_real_default_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The acceptance case, measured exactly the way the host ingest measures it.

    No patched cap and no canned pack: the probe here runs real ormsgpack over
    the real envelope against the real MESSAGE_PLANE_PAYLOAD_MAX_BYTES, which
    is the same expression the host applies before it drops an item whole.
    """
    socket = _Socket()
    counter = _install_counting_slow_plane(monkeypatch, socket)
    ctx, logger = _context(tmp_path)
    raw = b"\x00" * (256 * 1024)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[
            {"type": "text", "text": "look at this"},
            {"type": "image", "data": raw, "mime": "image/png"},
        ],
    )

    assert result == {"submitted": True}
    assert len(socket.sent) == 1
    assert "payload_too_large" not in repr(logger.records)
    assert int(settings.MESSAGE_PLANE_PAYLOAD_MAX_BYTES) == 512 * 1024
    # counter.sizes[0] is the probe: len(ormsgpack.packb(payload)).
    assert counter.sizes[0] <= int(settings.MESSAGE_PLANE_PAYLOAD_MAX_BYTES)
    # base64 only. Anything at or above 2x means the raw duplicate came back and
    # the headroom this test claims is fiction.
    assert counter.sizes[0] < 1.4 * len(raw)


@pytest.mark.plugin_unit
def test_inline_image_is_not_duplicated_into_the_legacy_binary_data_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The canonical part is the only carrier, and no other compat field moved.

    The legacy control-plane queue is used purely as a way to read back the very
    envelope _build_wire_payload produces; the key set is asserted whole so
    removing the raw duplicate cannot quietly take a neighbouring compat field
    with it.
    """
    monkeypatch.setattr(context_module, "zmq", None)
    queue = _Queue()
    ctx, _logger = _context(tmp_path, message_queue=queue)
    raw = b"\x89PNG" + b"\x00" * 512

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[
            {"type": "text", "text": "look"},
            {"type": "image", "data": raw, "mime": "image/png"},
        ],
    )

    assert result == {"submitted": True}
    payload = queue.items[0]
    assert payload["binary_data"] is None
    assert base64.b64decode(payload["parts"][1]["binary_base64"]) == raw
    # Every other legacy compat field is still derived and still present.
    assert payload["mime"] == "image/png"
    assert payload["binary_url"] is None
    assert payload["content"] == "look"
    assert payload["message_type"] == "proactive_notification"
    assert payload["description"] == ""
    assert payload["delivery"] == "proactive"
    assert payload["reply"] is True
    assert payload["unsafe"] is False
    assert set(payload) == {
        "type",
        "message_id",
        "plugin_id",
        "time",
        "schema",
        "source",
        "priority",
        "coalesce_key",
        "visibility",
        "ai_behavior",
        "parts",
        "metadata",
        "target_lanlan",
        "message_type",
        "content",
        "binary_data",
        "binary_url",
        "mime",
        "description",
        "unsafe",
        "delivery",
        "reply",
    }


@pytest.mark.plugin_unit
def test_binary_data_passed_beside_explicit_parts_still_reaches_the_wire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one shape where the legacy field is the ONLY carrier keeps it.

    translate_push_message ignores ``binary_data=`` when the caller also passes
    an explicit ``parts=`` list, so those bytes ride in no part. Dropping the
    field for this shape too would be silent data loss rather than a saving.
    """
    monkeypatch.setattr(context_module, "zmq", None)
    queue = _Queue()
    ctx, _logger = _context(tmp_path, message_queue=queue)
    raw = b"unreferenced-by-any-part"

    with pytest.warns(DeprecationWarning):
        result = ctx.push_message(
            visibility=["chat"],
            ai_behavior="read",
            parts=[{"type": "text", "text": "look"}],
            binary_data=raw,
            mime="image/png",
        )

    assert result == {"submitted": True}
    payload = queue.items[0]
    assert payload["binary_data"] == raw
    assert all("binary_base64" not in part for part in payload["parts"])


@pytest.mark.plugin_unit
def test_oversized_inline_push_is_rejected_on_the_legacy_queue_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The size guard must hold on the third exit, not just the two ZMQ ones.

    ``push_message`` has three places that accept responsibility for a payload:
    the batched fast plane, the synchronous plane, and this legacy control-plane
    queue, reached when zmq is missing or MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT is
    overridden to empty. The first two grew the probe; this one did not, so on
    exactly those configurations an oversized push was enqueued and reported as
    submitted -- the invisible non-delivery the rejection exists to end, still
    fully intact behind a deployment flag (CodeRabbit on PR #2999).

    The sibling test that also asserts an empty fallback queue does so with the
    ZMQ plane ACTIVE, where the earlier exit rejects first and this branch is
    never entered; it therefore cannot see this regression. Hence zmq=None here.
    """
    monkeypatch.setattr(context_module, "zmq", None)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=queue)

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
    assert queue.items == []
    reported = repr(logger.records)
    assert "payload_too_large" in reported
    assert "ctx.images.upload" in reported


@pytest.mark.plugin_unit
def test_within_budget_push_still_reaches_the_legacy_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new guard must not turn the legacy path into a dead end.

    Pins the other side of the branch added above: a payload inside the cap is
    still enqueued and still reports submitted. Without this, deleting the
    ``oversized is None`` check -- or widening it to reject everything -- would
    leave the suite green on the half that matters to every ordinary push.
    """
    monkeypatch.setattr(context_module, "zmq", None)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    queue = _Queue()
    ctx, _logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "small enough"}],
    )

    assert result == {"submitted": True}
    assert len(queue.items) == 1


@pytest.mark.plugin_unit
def test_oversized_text_only_push_is_rejected_without_inline_carriers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A push with no inline bytes is measured too, and told the truth about why.

    The size probe used to return early when the payload carried no inline
    carrier, to keep a second msgpack pack off the high-frequency text cue path.
    But the host measures the WHOLE envelope, so an oversized text or metadata
    push was still discarded there as payload_too_big after push_message() had
    answered submitted=True -- the invisible non-delivery this guard exists to
    end, left open for the cheapest payload to walk through (CodeRabbit).

    Measured before removing the skip: packb on a typical 248 B text cue is
    0.19 us. That is what the hole was buying.

    The remedy text matters as much as the verdict here: with no carrier there
    is nothing base64-encoded to blame, so advice about ctx.images.upload() or
    about a 4/3 wire ratio would send the author looking for an attachment that
    does not exist.
    """
    monkeypatch.setattr(context_module, "zmq", None)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="read",
        parts=[{"type": "text", "text": "x" * 8192}],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "payload_too_large",
    }
    assert queue.items == []
    reported = repr(logger.records)
    assert "payload_too_large" in reported
    assert "inline=none" in reported
    # No inline carrier -> none of the attachment-shaped advice applies.
    assert "ctx.images.upload" not in reported
    assert "base64" not in reported


@pytest.mark.plugin_unit
def test_metadata_driven_overflow_does_not_blame_a_tiny_inline_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remedy must follow what actually spent the budget.

    ``_inline_carrier_totals`` only ever sees inline carriers, so a push whose
    bulk is text or metadata still ranks its one tiny image as "dominant" and
    used to tell the author to run ctx.images.upload(). Following that advice
    changes nothing: the image was never the problem, and the push stays over
    the cap -- advice pointing the wrong way is worse than none, because the
    author believes they have already fixed it (Codex).

    So the branch now asks a different question first: with every inline
    carrier removed, is this push still oversized? Here it is, by a wide
    margin, and the remedy has to say so.
    """
    monkeypatch.setattr(context_module, "zmq", None)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[
            {"type": "text", "text": "y" * 16384},
            _inline_image_part(1),
        ],
    )

    assert result == {
        "ok": False,
        "submitted": False,
        "reason": "payload_too_large",
    }
    assert queue.items == []
    reported = repr(logger.records)
    assert "payload_too_large" in reported
    # The one-byte image is not the fix, so it must not be offered as one.
    assert "ctx.images.upload" not in reported
    assert "not what blew this cap" in reported


@pytest.mark.plugin_unit
def test_image_driven_overflow_still_points_at_the_upload_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The counterpart: when the image IS the cause, keep naming the helper.

    Without this, the fix above could degrade into "never mention the upload
    helper" and the suite would not notice -- the case the helper exists for
    is exactly an oversized inline image.
    """
    monkeypatch.setattr(context_module, "zmq", None)
    monkeypatch.setattr(settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    queue = _Queue()
    ctx, logger = _context(tmp_path, message_queue=queue)

    result = ctx.push_message(
        visibility=["chat"],
        ai_behavior="respond",
        parts=[{"type": "text", "text": "look"}, _inline_image_part(8192)],
    )

    assert result["reason"] == "payload_too_large"
    reported = repr(logger.records)
    assert "ctx.images.upload" in reported
    assert "not what blew this cap" not in reported
