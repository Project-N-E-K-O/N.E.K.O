from __future__ import annotations

import asyncio
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


def test_concurrent_uploads_bound_how_many_decodes_run_at_once(tmp_path: Path) -> None:
    """Per-image limits do not bound a burst.

    32 MiB in / 16 MP / 8 MiB out each cap ONE image, and the transport queue
    cannot help because the decode happens before anything is submitted. Without
    a slot limit a plugin uploading in a loop schedules every decode at once,
    each holding a full uncompressed bitmap.
    """
    from plugin.sdk.shared.core import images as images_mod

    live = 0
    peak = 0
    real_normalize = images_mod.normalize_image_to_jpeg

    def _counting_normalize(data: bytes) -> bytes:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        try:
            time.sleep(0.02)  # hold the slot long enough for others to pile up
            return real_normalize(data)
        finally:
            live -= 1

    async def _run() -> None:
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
        await asyncio.gather(*(
            ctx.images.upload(_png_bytes(), mime="image/png") for _ in range(8)
        ))

    with patch.object(images_mod, "normalize_image_to_jpeg", _counting_normalize):
        asyncio.run(_run())

    assert peak >= 2, "the test must actually overlap, or it proves nothing"
    assert peak <= images_mod.MAX_CONCURRENT_NORMALIZATIONS


def test_normalize_slot_is_rebound_when_the_loop_changes(tmp_path: Path) -> None:
    """A semaphore bound to a dead loop would deadlock, not bound.

    Plugin processes can restart their loop; the second run must not hang.
    """
    from plugin.sdk.shared.core import images as images_mod

    async def _one() -> object:
        return images_mod._acquire_normalize_slot()

    first = asyncio.run(_one())
    second = asyncio.run(_one())

    assert first is not second
