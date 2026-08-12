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
