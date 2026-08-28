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



def test_shutdown_completes_promptly_with_a_real_send_in_flight() -> None:
    """Real sockets, because the property is a libzmq property.

    An earlier revision of this guard stubbed both contexts so term() became a
    list append. That made the assertion vacuous AND let a false premise stand:
    the code was restructured to defer termination on the belief that term()
    would wait out the sender's timeout. It does not -- zmq_ctx_term first
    interrupts blocked calls with ETERM and only then waits -- and stubbing it
    is precisely why the belief survived review.

    So this drives a genuinely blocked send on a real full socket and measures.
    """
    import threading
    import time

    import zmq

    host = HostTransport()
    child = ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.image_uplink_endpoint,
    )
    # No consumer, tiny outbound queue: the send below genuinely blocks.
    child._img_sock.setsockopt(zmq.SNDHWM, 1)

    entered = threading.Event()
    outcome: list[object] = []

    def _blocked_send() -> None:
        entered.set()
        try:
            # A long budget on purpose: if shutdown waited for the sender
            # rather than interrupting it, this is what it would wait for.
            for _ in range(200):
                child._send_image_sync(b"meta", b"x" * 65536, timeout=60.0)
        except BaseException as exc:  # noqa: BLE001 - recording the outcome
            outcome.append(type(exc).__name__)

    sender = threading.Thread(target=_blocked_send, daemon=True)
    sender.start()
    assert entered.wait(5), "sender never started"
    time.sleep(0.4)  # let it reach the blocking poll

    started = time.monotonic()
    child.close()
    elapsed = time.monotonic() - started

    sender.join(10)
    try:
        host.close()
    except Exception:
        pass

    # Far below the 60s budget the sender asked for: term interrupted it.
    assert elapsed < 5.0, f"shutdown waited {elapsed:.1f}s on an in-flight send"
    assert outcome, "the blocked sender was never unblocked"
