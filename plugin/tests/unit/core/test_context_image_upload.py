from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch
import time
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from plugin.core.context import PluginContext


pytestmark = pytest.mark.plugin_unit


class _Logger:
    def debug(self, *_args: object, **_kwargs: object) -> None:
        return None


class _ImageTransport:
    def __init__(
        self,
        responses: asyncio.Queue[dict[str, object]],
        *,
        result_url: str = "http://127.0.0.1:48916/media/test-image",
    ) -> None:
        self.responses = responses
        self.result_url = result_url
        self.uploaded: list[tuple[str, str, bytes]] = []
        self.on_response = None

    async def send_image(
        self,
        request_id: str,
        *,
        mime: str,
        data: bytes,
        timeout: float,
    ) -> None:
        self.uploaded.append((request_id, mime, data))
        response = {
            "type": "IMAGE_UPLOAD_RESULT",
            "request_id": request_id,
            "result": {
                "type": "image",
                "url": self.result_url,
                "mime": "image/jpeg",
            },
        }
        if self.on_response is not None:
            self.on_response(response)
        else:
            await self.responses.put(response)


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGBA", (4, 3), (255, 0, 0, 128)).save(output, format="PNG")
    return output.getvalue()


def _image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (7, 5), "green").save(output, format=image_format)
    return output.getvalue()


def test_images_upload_returns_a_push_message_compatible_image_part(tmp_path: Path) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
    )
    transport.on_response = ctx._dispatch_direct_response

    image_part = asyncio.run(ctx.images.upload(_png_bytes(), mime="image/png"))

    assert image_part == {
        "type": "image",
        "url": "http://127.0.0.1:48916/media/test-image",
        "mime": "image/jpeg",
    }
    assert len(transport.uploaded) == 1
    _request_id, uploaded_mime, uploaded_data = transport.uploaded[0]
    assert uploaded_mime == "image/jpeg"
    assert uploaded_data.startswith(b"\xff\xd8")
    with Image.open(BytesIO(uploaded_data)) as uploaded_image:
        assert uploaded_image.mode == "RGB"
        assert uploaded_image.size == (4, 3)


@pytest.mark.parametrize("invalid_url", ["", "   "])
def test_images_upload_rejects_empty_result_url(
    tmp_path: Path,
    invalid_url: str,
) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses, result_url=invalid_url)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
    )
    transport.on_response = ctx._dispatch_direct_response

    with pytest.raises(RuntimeError, match="valid image part"):
        asyncio.run(ctx.images.upload(_png_bytes(), mime="image/png"))


@pytest.mark.parametrize(
    ("image_format", "declared_mime"),
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
    ],
)
def test_images_upload_accepts_common_browser_image_formats(
    tmp_path: Path,
    image_format: str,
    declared_mime: str,
) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
    )
    transport.on_response = ctx._dispatch_direct_response

    asyncio.run(
        ctx.images.upload(
            _image_bytes(image_format),
            mime=declared_mime,
        )
    )

    _request_id, uploaded_mime, uploaded_data = transport.uploaded[0]
    assert uploaded_mime == "image/jpeg"
    with Image.open(BytesIO(uploaded_data)) as uploaded_image:
        assert uploaded_image.format == "JPEG"
        assert uploaded_image.mode == "RGB"
        assert uploaded_image.size == (7, 5)


def test_images_upload_composites_rgb_color_key_transparency_on_white(
    tmp_path: Path,
) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
    )
    transport.on_response = ctx._dispatch_direct_response
    image = Image.new("RGB", (20, 10), "blue")
    for x in range(10):
        for y in range(10):
            image.putpixel((x, y), (255, 0, 0))
    output = BytesIO()
    image.save(output, format="PNG", transparency=(255, 0, 0))

    asyncio.run(ctx.images.upload(output.getvalue(), mime="image/png"))

    with Image.open(BytesIO(transport.uploaded[0][2])) as uploaded_image:
        left = uploaded_image.getpixel((2, 5))
        right = uploaded_image.getpixel((17, 5))
    assert min(left) > 240
    assert right[2] > 200 and right[0] < 50 and right[1] < 50


def test_images_upload_scales_large_input_without_blocking_the_event_loop(tmp_path: Path) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
    )
    transport.on_response = ctx._dispatch_direct_response
    output = BytesIO()
    Image.new("RGB", (3000, 1000), "blue").save(output, format="PNG")

    async def _run() -> None:
        loop_was_responsive = asyncio.Event()

        async def _tick() -> None:
            await asyncio.sleep(0)
            loop_was_responsive.set()

        tick = asyncio.create_task(_tick())
        await ctx.images.upload(output.getvalue())
        await tick
        assert loop_was_responsive.is_set()

    asyncio.run(_run())

    with Image.open(BytesIO(transport.uploaded[0][2])) as uploaded_image:
        assert max(uploaded_image.size) == 2048
        assert uploaded_image.size == (2048, 683)


def test_images_upload_rejects_excessive_pixel_count_before_decode(tmp_path: Path) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
    )
    output = BytesIO()
    Image.new("1", (4097, 4097)).save(output, format="PNG")

    with pytest.raises(ValueError, match="16 megapixel decode limit"):
        asyncio.run(ctx.images.upload(output.getvalue()))

    assert transport.uploaded == []


def test_context_close_cancels_an_upload_waiting_for_its_response(tmp_path: Path) -> None:
    class _NoResponseTransport:
        def __init__(self, sent: asyncio.Event) -> None:
            self.sent = sent

        async def send_image(self, *_args: object, **_kwargs: object) -> None:
            self.sent.set()
            return None

    async def _run() -> None:
        sent = asyncio.Event()
        transport = _NoResponseTransport(sent)
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "plugin.toml",
            logger=_Logger(),  # type: ignore[arg-type]
            status_queue=None,
            _response_queue=asyncio.Queue(),
            _image_transport=transport,
        )
        upload = asyncio.create_task(ctx.images.upload(_png_bytes(), timeout=10.0))
        await asyncio.wait_for(sent.wait(), timeout=1.0)
        ctx.close()
        with pytest.raises(asyncio.CancelledError):
            await upload

    asyncio.run(_run())


def test_images_upload_times_out_when_the_service_does_not_respond(tmp_path: Path) -> None:
    class _NoResponseTransport:
        async def send_image(self, *_args: object, **_kwargs: object) -> None:
            return None

    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=asyncio.Queue(),
        _image_transport=_NoResponseTransport(),
    )

    async def _run() -> None:
        with pytest.raises(TimeoutError, match="image upload timed out"):
            await ctx.images.upload(_png_bytes(), timeout=0.05)

    asyncio.run(asyncio.wait_for(_run(), timeout=1.0))


@pytest.mark.parametrize(
    "lifecycle_name",
    [
        "startup",
        "command_loop_start",
        "freeze",
        "unfreeze",
        "shutdown",
        "config_change",
    ],
)
def test_images_upload_fails_fast_in_lifecycle_handlers(
    tmp_path: Path,
    lifecycle_name: str,
) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
    )

    async def _run() -> None:
        with ctx._handler_scope(f"lifecycle.{lifecycle_name}"):
            with pytest.raises(
                RuntimeError,
                match="not available from lifecycle handlers",
            ):
                await ctx.images.upload(_png_bytes(), timeout=10.0)

    asyncio.run(asyncio.wait_for(_run(), timeout=1.0))
    assert transport.uploaded == []


def test_images_upload_fails_fast_while_plugin_is_freezing(tmp_path: Path) -> None:
    responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    transport = _ImageTransport(responses)
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=responses,
        _image_transport=transport,
        _image_uploads_blocked=True,
    )

    with pytest.raises(RuntimeError, match="while the plugin is freezing"):
        asyncio.run(ctx.images.upload(_png_bytes(), timeout=10.0))

    assert transport.uploaded == []


def test_normalization_is_bounded_process_wide() -> None:
    """The gate lives inside the decode, so it holds across loops and threads.

    A per-loop semaphore bounded nothing here: the plugin host calls
    asyncio.run() per handler, so each got its own slots. Instrumented at
    Image.open rather than at normalize_image_to_jpeg, because patching the
    latter would bypass the gate this test exists to check.
    """
    from PIL import Image as PILImage

    from plugin.sdk.shared.core import images as images_mod

    live = 0
    peak = 0
    lock = threading.Lock()
    real_open = PILImage.open

    def _slow_open(*args, **kwargs):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            time.sleep(0.03)
            return real_open(*args, **kwargs)
        finally:
            with lock:
                live -= 1

    payload = _png_bytes()
    errors: list[BaseException] = []

    def _worker() -> None:
        try:
            images_mod.normalize_image_to_jpeg(payload)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    with patch.object(PILImage, "open", _slow_open):
        threads = [threading.Thread(target=_worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)

    assert not errors, errors
    assert peak >= 2, "the test must actually overlap, or it proves nothing"
    assert peak <= images_mod.MAX_CONCURRENT_NORMALIZATIONS


def test_an_abandoned_decode_still_holds_its_slot() -> None:
    """asyncio.to_thread cannot cancel a running thread.

    The gate is held by the thread, so a cancelled caller cannot hand the slot
    to the next upload while the abandoned bitmap is still in memory.
    """
    from plugin.sdk.shared.core import images as images_mod

    started = threading.Event()
    may_finish = threading.Event()

    async def _run() -> None:
        def _blocking(_data: bytes) -> bytes:
            with images_mod._normalize_gate:
                started.set()
                may_finish.wait(5)
                return b"jpeg"

        abandoned = asyncio.ensure_future(asyncio.to_thread(_blocking, b"a"))
        await asyncio.to_thread(started.wait, 5)
        abandoned.cancel()
        await asyncio.gather(abandoned, return_exceptions=True)

        # One slot is still held by the abandoned thread, so only one more may
        # enter — proven by a non-blocking acquire of the remaining slot.
        assert images_mod._normalize_gate.acquire(blocking=False) is True
        assert images_mod._normalize_gate.acquire(blocking=False) is False
        images_mod._normalize_gate.release()

        may_finish.set()

    asyncio.run(_run())


def test_upload_total_time_is_bounded_by_the_requested_timeout(tmp_path: Path) -> None:
    """The legs share one deadline instead of each restarting the clock.

    A slow decode — queueing behind the process-wide gate counts — used to leave
    the transport a fresh full timeout on each leg, so `timeout=T` could take
    past 2T and overrun the caller's own deadline.
    """
    from plugin.sdk.shared.core import images as images_mod

    seen_send_timeouts: list[float] = []

    class _SlowTransport(_ImageTransport):
        async def send_image(self, request_id, *, mime, data, timeout):  # type: ignore[override]
            seen_send_timeouts.append(timeout)
            return await super().send_image(request_id, mime=mime, data=data, timeout=timeout)

    def _slow_normalize(payload: bytes, **_kwargs: object) -> bytes:
        time.sleep(0.25)
        return b"jpeg"

    async def _run() -> None:
        responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        transport = _SlowTransport(responses)
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "plugin.toml",
            logger=_Logger(),  # type: ignore[arg-type]
            status_queue=None,
            _response_queue=responses,
            _image_transport=transport,
        )
        transport.on_response = ctx._dispatch_direct_response
        with patch.object(images_mod, "normalize_image_to_jpeg", _slow_normalize):
            await ctx.images.upload(_png_bytes(), mime="image/png", timeout=1.0)

    asyncio.run(_run())

    assert seen_send_timeouts, "the transport leg must have been reached"
    # The decode already spent 0.25s of the 1.0s budget, so the send leg must
    # receive strictly less than the full timeout rather than a fresh one.
    assert seen_send_timeouts[0] < 1.0
    assert seen_send_timeouts[0] > 0


def test_upload_bounds_the_wait_for_a_decode_slot(tmp_path: Path) -> None:
    """A saturated decode gate must not blow the caller's advertised timeout.

    The gate is process-wide and deliberately held by the WORKER THREAD, because
    asyncio.to_thread cannot cancel a running thread. That makes the queueing --
    not the decode -- the part that can honestly be bounded: a caller that gives
    up while still waiting for a slot has started nothing, so it strands no
    thread and leaves no slot half-held.

    The slots are freed by a watchdog rather than held for the whole test, so an
    unbounded wait fails on the elapsed assertion instead of hanging CI forever.
    """
    from plugin.sdk.shared.core import images as images_mod

    opened: list[object] = []
    real_open = images_mod.Image.open

    def _tracking_open(*args: object, **kwargs: object) -> object:
        opened.append(args)
        return real_open(*args, **kwargs)

    async def _run() -> float:
        responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        transport = _ImageTransport(responses)
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "plugin.toml",
            logger=_Logger(),  # type: ignore[arg-type]
            status_queue=None,
            _response_queue=responses,
            _image_transport=transport,
        )
        transport.on_response = ctx._dispatch_direct_response
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await ctx.images.upload(_png_bytes(), mime="image/png", timeout=0.3)
        return time.monotonic() - started

    slots = images_mod.MAX_CONCURRENT_NORMALIZATIONS
    guard = threading.Lock()
    freed = False

    def _free_slots() -> None:
        nonlocal freed
        with guard:
            if freed:
                return
            freed = True
            for _ in range(slots):
                images_mod._normalize_gate.release()

    for _ in range(slots):
        images_mod._normalize_gate.acquire()
    watchdog = threading.Timer(2.0, _free_slots)
    watchdog.start()
    try:
        with patch.object(images_mod.Image, "open", _tracking_open):
            elapsed = asyncio.run(_run())
    finally:
        watchdog.cancel()
        _free_slots()

    # Returned on its own budget rather than riding the watchdog's release.
    assert elapsed < 1.0, f"caller overran its 0.3s budget: {elapsed:.2f}s"
    # Gave up while still queueing, so no decode was ever started.
    assert opened == [], "the decode must not start once the budget is gone"
    # The gate is intact afterwards: a normal decode still finds its slots.
    assert images_mod.normalize_image_to_jpeg(_png_bytes())


def test_host_side_normalize_waits_indefinitely_for_a_slot() -> None:
    """`slot_timeout=None` is the host default and must never time out.

    character_runtime calls this synchronously with no deadline of its own, so
    adding the bound must not turn host-side normalization into a flaky failure.
    """
    from plugin.sdk.shared.core import images as images_mod

    released = threading.Event()
    images_mod._normalize_gate.acquire()
    images_mod._normalize_gate.acquire()

    def _release_later() -> None:
        time.sleep(0.4)
        images_mod._normalize_gate.release()
        images_mod._normalize_gate.release()
        released.set()

    threading.Thread(target=_release_later, daemon=True).start()
    # Blocks ~0.4s rather than raising; far longer than any default timeout.
    payload = images_mod.normalize_image_to_jpeg(_png_bytes())
    assert released.is_set()
    assert payload


def _stub_child_transport():
    """A ChildTransport with every ZMQ object stubbed.

    The contexts are stubbed deliberately: term() has its own teardown
    semantics that are not what these pin, and driving real sockets here hung
    for ten minutes on the first attempt. A guard that can hang is worse than
    no guard.
    """
    import threading
    from types import SimpleNamespace

    from plugin.core import zmq_transport as zt

    child = zt.ChildTransport.__new__(zt.ChildTransport)
    child._closed = False
    child._img_lock = threading.Lock()
    child._img_closed = []
    child._img_sock = SimpleNamespace(
        close=lambda **_kw: child._img_closed.append(True),
        poll=lambda **_kw: True,
        send_multipart=lambda *a, **k: None,
    )
    child._dl_sock = SimpleNamespace(close=lambda **_kw: None)
    child._ul_sock = SimpleNamespace(close=lambda **_kw: None)
    child._async_ctx = SimpleNamespace(term=lambda: None)
    child._sync_ctx = SimpleNamespace(term=lambda: None)
    return child


def test_close_neither_waits_forever_nor_races_an_in_flight_send():
    """Shutdown must not inherit an upload's timeout, and must not race it.

    _send_image_sync holds _img_lock for the whole upload, and the public
    upload timeout has no natural upper bound, so an unconditional acquire let
    a backpressured media socket wedge shutdown indefinitely.

    Closing the socket anyway is NOT the fix: libzmq sockets are not thread
    safe, so closing one while another thread is inside poll/send is undefined
    behaviour -- trading a hang for a crash. Shutdown gives up the socket and
    the holder closes it on its way out.
    """
    import threading
    import time

    from plugin.core import zmq_transport as zt

    child = _stub_child_transport()
    holder_ready = threading.Event()
    release = threading.Event()

    def _hold() -> None:
        with child._img_lock:
            holder_ready.set()
            release.wait(60)

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    assert holder_ready.wait(5), "helper never took the lock"
    try:
        started = time.monotonic()
        child.close()
        elapsed = time.monotonic() - started
    finally:
        release.set()
        holder.join(5)

    assert elapsed < zt._IMG_LOCK_SHUTDOWN_WAIT_S + 2, (
        f"close() waited {elapsed:.1f}s on a held image lock"
    )
    assert child._img_closed == [], (
        "closed the socket out from under a thread holding the lock"
    )
    # Shutdown is still recorded, which is what lets the holder finish the job.
    assert child._closed is True


def test_an_in_flight_send_closes_the_socket_when_shutdown_beat_it():
    """The other half: whoever holds the lock last performs the close."""
    child = _stub_child_transport()
    child.close()
    assert child._img_closed == [True], "uncontended close should have closed it"

    # Simulate the race: shutdown already flagged, socket handed back.
    child._img_closed.clear()
    child._closed = False
    child._send_image_sync(b"meta", b"payload", timeout=1.0)
    assert child._img_closed == [], "no shutdown in progress, nothing to close"

    def _flag_shutdown_midway(**_kw):
        child._closed = True
        return True

    child._img_sock.poll = _flag_shutdown_midway
    child._send_image_sync(b"meta", b"payload", timeout=1.0)
    assert child._img_closed == [True], (
        "a send that outlived the shutdown must close the socket itself"
    )

def _ctx_with_downlink(tmp_path, transport, *, ready: bool):
    import threading as _threading

    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=None,
        _image_transport=transport,
    )
    ctx._downlink_ready = _threading.Event()
    if ready:
        ctx._downlink_ready.set()
    return ctx


def test_upload_waits_for_the_downlink_instead_of_sending_into_a_void(tmp_path):
    """Timer / custom-event threads start before the downlink loop reads.

    An upload launched in that window has no reader for its reply, so it could
    only time out. Waiting is the honest answer rather than refusing: those
    handlers are legitimate uploaders the moment the loop is up, so a refusal
    keyed on handler NAME would go on rejecting them afterwards.
    """
    sent: list[str] = []

    class _RecordingTransport:
        async def send_image(self, request_id, *, mime, data, timeout):
            sent.append(request_id)

    async def _run() -> None:
        ctx = _ctx_with_downlink(tmp_path, _RecordingTransport(), ready=False)
        loop = asyncio.get_running_loop()
        with pytest.raises(TimeoutError) as raised:
            # Outer bound, same reason as the elapsed-budget test below: a
            # regression that decouples the wait from the caller's deadline
            # would make this hang rather than fail.
            await asyncio.wait_for(
                ctx._upload_image(
                    b"jpeg", mime="image/jpeg",
                    deadline=loop.time() + 0.3, timeout=0.3,
                ),
                timeout=2.0,
            )
        assert "downlink not ready" in str(raised.value)

    asyncio.run(_run())
    # Never reached the transport: nothing was sent to a loop that was not
    # listening, so no request id is left dangling on the host side.
    assert sent == []


def test_the_downlink_wait_is_charged_to_the_callers_deadline(tmp_path):
    """The wait must not become a SECOND budget stacked on the caller's.

    A plugin that asks for T seconds must still get an answer within T, even
    when most of T was spent waiting for the loop to come up.
    """
    class _NeverTransport:
        async def send_image(self, request_id, *, mime, data, timeout):
            return None

    async def _run() -> float:
        ctx = _ctx_with_downlink(tmp_path, _NeverTransport(), ready=False)
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises((TimeoutError, asyncio.TimeoutError)):
            # Outer bound: if the wait ever gets its own budget instead of
            # drawing on the caller's, the inner loop never expires and this
            # test would hang rather than fail. A guard that can hang is a CI
            # hazard, not a guard -- third time in this PR.
            await asyncio.wait_for(
                ctx._upload_image(
                    b"jpeg", mime="image/jpeg",
                    deadline=loop.time() + 0.3, timeout=0.3,
                ),
                timeout=2.0,
            )
        return loop.time() - started

    elapsed = asyncio.run(_run())
    assert elapsed < 0.8, f"the wait added a second budget: {elapsed:.2f}s"


def test_an_upload_proceeds_once_the_loop_marks_the_downlink_ready(tmp_path):
    """The gate releases; it does not permanently reject."""
    import threading as _threading

    sent: list[str] = []

    class _RecordingTransport:
        async def send_image(self, request_id, *, mime, data, timeout):
            sent.append(request_id)

    async def _run() -> None:
        ctx = _ctx_with_downlink(tmp_path, _RecordingTransport(), ready=False)
        loop = asyncio.get_running_loop()

        # The downlink loop comes up part-way through the wait.
        _threading.Timer(0.1, ctx._downlink_ready.set).start()

        with pytest.raises(TimeoutError):
            # Still times out waiting for a RESPONSE, which is a different
            # failure -- the point is that it got past the gate and sent.
            await ctx._upload_image(
                b"jpeg", mime="image/jpeg",
                deadline=loop.time() + 0.6, timeout=0.6,
            )

    asyncio.run(_run())
    assert sent, "the upload never reached the transport after the loop came up"


def test_the_ready_flag_falls_while_the_loop_awaits_a_config_handler():
    """The flag means "routing right now", not "the loop started once".

    CONFIG_UPDATE is awaited INLINE by the command loop, so while a slow
    config_change handler runs the loop cannot route anyone else's
    IMAGE_UPLOAD_RESULT. A timer upload started in that interval would send
    into a loop that is not reading and could only time out -- the same
    failure as the startup window, from the same cause (Codex P2).

    Asserted on the source because the alternative is standing up the whole
    plugin host process; what matters is the ordering of clear/await/set and
    that the restore is in a finally, which a slow-handler test could pass
    without.
    """
    import inspect
    import re

    from plugin.core import host as host_mod

    src = inspect.getsource(host_mod)
    block = src[src.index('if msg_type == "CONFIG_UPDATE":'):]
    block = block[: block.index('if msg_type == "TRIGGER_CUSTOM":')]

    clear_at = block.index("ctx._downlink_ready.clear()")
    await_at = block.index("await _handle_config_update_command(")
    set_at = block.index("ctx._downlink_ready.set()")

    assert clear_at < await_at < set_at, "flag must fall before the await and rise after"
    # Restored even if the handler raises: otherwise one bad config_change
    # blocks every later upload for the life of the process.
    assert re.search(r"finally:\s*\n\s*ctx\._downlink_ready\.set\(\)", block), (
        "the restore must be in a finally"
    )
