from __future__ import annotations

import asyncio

import ormsgpack
import pytest
import zmq
import zmq.asyncio

from plugin.core.rpc import RpcDispatcher
from plugin.utils.zeromq_ipc import ZmqIpcServer

pytestmark = pytest.mark.plugin_integration


async def test_compatibility_listener_returns_serialization_fault_and_stays_live():
    async def handler(ctx, request):
        return object() if request.get("bad") else "ok"

    dispatcher = RpcDispatcher({"echo": handler})
    server = ZmqIpcServer("tcp://127.0.0.1:*", dispatcher.dispatch)
    stop = asyncio.Event()
    serving = asyncio.create_task(server.serve_forever(stop))
    client = zmq.asyncio.Context.instance().socket(zmq.DEALER)
    client.setsockopt(zmq.LINGER, 0)
    client.connect(server.endpoint)
    try:
        for request_id, bad in (("bad", True), ("good", False)):
            await client.send_multipart(
                [
                    request_id.encode(),
                    ormsgpack.packb(
                        {
                            "type": "echo",
                            "request_id": request_id,
                            "from_plugin": "a",
                            "bad": bad,
                        }
                    ),
                ]
            )
            frames = await asyncio.wait_for(client.recv_multipart(), 1)
            reply = ormsgpack.unpackb(frames[1])
            if bad:
                assert reply["error"]["code"] == "SERIALIZATION_FAILED"
            else:
                assert reply["result"] == "ok"
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        await server.aclose()
        await dispatcher.close()
        client.close()


async def test_failed_invalid_request_reply_does_not_kill_listener(caplog):
    async def handler(request):
        return {"request_id": request["request_id"], "result": "ok", "error": None}

    server = ZmqIpcServer("tcp://127.0.0.1:*", handler)
    real_socket = server._sock

    class FailFirstSend:
        failed = False

        def __getattr__(self, name):
            return getattr(real_socket, name)

        async def send_multipart(self, frames):
            if not self.failed:
                self.failed = True
                raise TimeoutError("PRIVATE_DIALOG_SENTINEL")
            await real_socket.send_multipart(frames)

    server._sock = FailFirstSend()
    serving = asyncio.create_task(server.serve_forever(asyncio.Event()))
    client = zmq.asyncio.Context.instance().socket(zmq.DEALER)
    client.setsockopt(zmq.LINGER, 0)
    client.connect(server.endpoint)
    try:
        await client.send_multipart([b"bad", ormsgpack.packb([])])
        await client.send_multipart([b"good", ormsgpack.packb({"request_id": "good"})])
        frames = await asyncio.wait_for(client.recv_multipart(), 1)
        assert frames[0] == b"good"
        assert ormsgpack.unpackb(frames[1])["result"] == "ok"
        assert "reply delivery failed" in caplog.text
        assert "PRIVATE_DIALOG_SENTINEL" not in caplog.text
    finally:
        serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)
        await server.aclose()
        client.close()
