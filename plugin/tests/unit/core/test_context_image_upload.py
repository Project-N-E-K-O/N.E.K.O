from __future__ import annotations

import asyncio
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
    def __init__(self, responses: asyncio.Queue[dict[str, object]]) -> None:
        self.responses = responses
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
                "url": "http://127.0.0.1:48916/media/test-image",
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


def test_context_close_cancels_an_upload_waiting_for_its_response(tmp_path: Path) -> None:
    class _NoResponseTransport:
        def __init__(self) -> None:
            self.sent: asyncio.Event | None = None

        async def send_image(self, *_args: object, **_kwargs: object) -> None:
            self.sent = asyncio.Event()
            self.sent.set()
            return None

    transport = _NoResponseTransport()
    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "plugin.toml",
        logger=_Logger(),  # type: ignore[arg-type]
        status_queue=None,
        _response_queue=asyncio.Queue(),
        _image_transport=transport,
    )

    async def _run() -> None:
        upload = asyncio.create_task(ctx.images.upload(_png_bytes(), timeout=10.0))
        for _ in range(100):
            if transport.sent is not None:
                break
            await asyncio.sleep(0.01)
        assert transport.sent is not None
        await transport.sent.wait()
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
