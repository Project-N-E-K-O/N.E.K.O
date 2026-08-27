from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path

from PIL import Image
import pytest

from plugin.core.communication import PluginCommunicationResourceManager
from plugin.core.context import PluginContext
from plugin.core.image_store import get_image_store
from plugin.core.zmq_transport import CH_RESP, CH_STS, ChildTransport, HostTransport


pytestmark = pytest.mark.plugin_integration


class _Logger:
    def debug(self, *_args: object, **_kwargs: object) -> None:
        return None

    def warning(self, *_args: object, **_kwargs: object) -> None:
        return None


def _png_bytes(color: str = "blue") -> bytes:
    output = BytesIO()
    Image.new("RGB", (8, 6), color).save(output, format="PNG")
    return output.getvalue()


def test_public_image_upload_crosses_the_dedicated_media_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49888")

    async def _run() -> None:
        get_image_store().clear()
        host = HostTransport()
        child = ChildTransport(
            host.downlink_endpoint,
            host.uplink_endpoint,
            host.image_uplink_endpoint,
        )
        manager = PluginCommunicationResourceManager(plugin_id="demo", transport=host)
        responses: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "plugin.toml",
            logger=_Logger(),  # type: ignore[arg-type]
            status_queue=None,
            _response_queue=responses,
            _image_transport=child,
        )

        async def _pump_responses() -> None:
            while True:
                item = await child.recv_downlink(timeout_ms=100)
                if item is not None and item[0] == CH_RESP:
                    if not ctx._dispatch_direct_response(item[1]):
                        await responses.put(item[1])

        await manager.start()
        pump = asyncio.create_task(_pump_responses())
        try:
            parts = await asyncio.gather(
                ctx.images.upload(_png_bytes("blue"), mime="image/png"),
                ctx.images.upload(_png_bytes("red"), mime="image/png"),
            )
            assert parts[0]["url"] != parts[1]["url"]
            for part in parts:
                assert str(part["url"]).startswith("http://127.0.0.1:49888/media/")
                image_id = str(part["url"]).rsplit("/", 1)[-1]
                stored = get_image_store().get(image_id)
                assert part["type"] == "image"
                assert part["mime"] == "image/jpeg"
                assert stored is not None
                assert stored.data.startswith(b"\xff\xd8")
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await manager.shutdown()
            child.close()
            host.close()

    asyncio.run(_run())


def test_media_backpressure_does_not_block_control_uplink() -> None:
    """A saturated image socket must not head-of-line block status traffic."""

    async def _run() -> None:
        host = HostTransport()
        child = ChildTransport(
            host.downlink_endpoint,
            host.uplink_endpoint,
            host.image_uplink_endpoint,
        )
        payload = b"x" * (512 * 1024)
        flood = [
            asyncio.create_task(
                child.send_image(
                    f"flood-{index}",
                    mime="image/jpeg",
                    data=payload,
                    timeout=0.25,
                )
            )
            for index in range(32)
        ]
        try:
            outcomes = await asyncio.gather(*flood, return_exceptions=True)
            assert any(isinstance(item, TimeoutError) for item in outcomes)
            await asyncio.wait_for(
                asyncio.to_thread(
                    child.send_uplink,
                    CH_STS,
                    {"status": "control-still-responsive"},
                ),
                timeout=3.0,
            )
            control_message = await asyncio.wait_for(
                host.recv(timeout_ms=500),
                timeout=3.0,
            )
            assert control_message == (
                CH_STS,
                {"status": "control-still-responsive"},
            )
        finally:
            for task in flood:
                task.cancel()
            await asyncio.gather(*flood, return_exceptions=True)
            child.close()
            host.close()

    asyncio.run(_run())


def test_image_upload_from_a_timer_thread_resolves_on_its_own_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEKO_USER_PLUGIN_SERVER_PORT", "49888")

    async def _run() -> None:
        get_image_store().clear()
        host = HostTransport()
        child = ChildTransport(
            host.downlink_endpoint,
            host.uplink_endpoint,
            host.image_uplink_endpoint,
        )
        manager = PluginCommunicationResourceManager(plugin_id="demo", transport=host)
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "plugin.toml",
            logger=_Logger(),  # type: ignore[arg-type]
            status_queue=None,
            _response_queue=asyncio.Queue(),
            _image_transport=child,
        )

        async def _pump_responses() -> None:
            while True:
                item = await child.recv_downlink(timeout_ms=100)
                if item is not None and item[0] == CH_RESP:
                    ctx._dispatch_direct_response(item[1])

        await manager.start()
        pump = asyncio.create_task(_pump_responses())
        try:
            part = await asyncio.to_thread(
                lambda: asyncio.run(ctx.images.upload(_png_bytes("green")))
            )
            image_id = str(part["url"]).rsplit("/", 1)[-1]
            assert get_image_store().get(image_id) is not None
        finally:
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            await manager.shutdown()
            child.close()
            host.close()

    asyncio.run(_run())


def test_image_transport_rejects_oversized_payload_before_sending() -> None:
    async def _run() -> None:
        host = HostTransport()
        child = ChildTransport(
            host.downlink_endpoint,
            host.uplink_endpoint,
            host.image_uplink_endpoint,
        )
        try:
            with pytest.raises(ValueError, match="transport limit"):
                await child.send_image(
                    "too-large",
                    mime="image/jpeg",
                    data=b"x" * (8 * 1024 * 1024 + 1),
                    timeout=1.0,
                )
            assert await host.recv_image(timeout_ms=50) is None
        finally:
            child.close()
            host.close()

    asyncio.run(_run())

