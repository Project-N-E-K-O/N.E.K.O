"""Real socket retirement across error callback and cancellation interleavings."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import websockets

from main_logic.asr_client._infra import AsrSessionConfig, _RealtimeAsrSessionImpl
from main_logic.asr_client.workers import qwen, step, openai
from main_logic.asr_client.connection_cleanup import connection_registry


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worker_module", [qwen, step, openai], ids=["qwen", "step", "openai"]
)
@pytest.mark.parametrize("callback_delay", [None, 0, 0.03])
async def test_isolated_session_releases_socket(
    monkeypatch, worker_module, callback_delay
):
    trigger, peer_closed = asyncio.Event(), asyncio.Event()

    async def peer(socket):
        update = json.loads(await socket.recv())
        await socket.send(
            json.dumps({"type": "session.updated", "session": update["session"]})
        )
        await trigger.wait()
        await socket.send(
            json.dumps({"type": "error", "error": {"code": "server_error"}})
        )
        await socket.wait_closed()
        peer_closed.set()

    async def on_final(_text):
        pass

    async def on_error(_message):
        if callback_delay is not None:
            await asyncio.sleep(callback_delay)

    clients = []
    original_connect = websockets.connect
    async with websockets.serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def local_connect(_url, **kwargs):
            client = await original_connect(f"ws://127.0.0.1:{port}", **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(worker_module.websockets, "connect", local_connect)
        name = worker_module.__name__.rsplit(".", 1)[-1]
        session = _RealtimeAsrSessionImpl(
            worker_fn=getattr(worker_module, name + "_asr_worker"),
            api_key="test-key",
            config=AsrSessionConfig(endpointing_mode="provider"),
            on_input_transcript=on_final,
            on_connection_error=on_error,
        )
        try:
            await session.connect()
            await session.stream_audio(b"\x01\x00" * 16000, sample_rate_hz=16000)
            async with asyncio.timeout(2):
                while session.transport_written_audio_bytes == 0:
                    await asyncio.sleep(0.001)
            trigger.set()
            async with asyncio.timeout(2):
                while not session._response_task.done():
                    await asyncio.sleep(0.001)
            await session.close()
            try:
                await asyncio.wait_for(peer_closed.wait(), 0.2)
            except TimeoutError:
                pass
            assert peer_closed.is_set()
            assert all(client.state.name == "CLOSED" for client in clients)
            assert session._worker_task.done()
            assert session._response_task.done()
            assert session._callback_task.done()
        finally:
            trigger.set()
            await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_module", [qwen, step], ids=["qwen", "step"])
async def test_internal_clear_waits_for_forced_socket_retirement(
    monkeypatch, worker_module
):
    clients, peers_closed = [], []
    replaced = asyncio.Event()

    async def peer(socket):
        peer_closed = asyncio.Event()
        peers_closed.append(peer_closed)
        try:
            async for raw in socket:
                event = json.loads(raw)
                if event["type"] == "session.update":
                    await socket.send(
                        json.dumps(
                            {"type": "session.updated", "session": event["session"]}
                        )
                    )
                elif event["type"] == "session.finish":
                    await socket.send(json.dumps({"type": "session.finished"}))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            peer_closed.set()

    real_connect = websockets.connect
    async with websockets.serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connect(_url, **kwargs):
            if clients:
                assert clients[-1].state.name == "CLOSED"
            client = await real_connect(f"ws://127.0.0.1:{port}", **kwargs)
            if not clients:
                client.close = AsyncMock(side_effect=OSError("injected close failure"))
            clients.append(client)
            if len(clients) == 2:
                replaced.set()
            return client

        monkeypatch.setattr(worker_module.websockets, "connect", connect)
        name = worker_module.__name__.rsplit(".", 1)[-1]
        session = _RealtimeAsrSessionImpl(
            worker_fn=getattr(worker_module, name + "_asr_worker"),
            api_key="test-key",
            config=AsrSessionConfig(endpointing_mode="provider"),
            on_input_transcript=AsyncMock(),
            on_connection_error=AsyncMock(),
        )
        try:
            await session.connect()
            first_owner = connection_registry(session._request_queue).connections[0]
            await session.clear_audio_buffer()
            await asyncio.wait_for(replaced.wait(), 2)
            await asyncio.wait_for(peers_closed[0].wait(), 1)
            assert first_owner.cleanup_outcome.released
            assert first_owner.cleanup_outcome.graceful_close_error == "OSError"
            clients[0].close.assert_awaited_once()
            assert clients[0].state.name == "CLOSED"
        finally:
            await session.close()
        assert all(client.state.name == "CLOSED" for client in clients)
        assert session._worker_task.done()


@pytest.mark.asyncio
async def test_qwen_close_does_not_wait_for_unresponsive_finish(monkeypatch):
    """Fault retirement fits the runtime's two-second candidate cleanup budget."""
    peer_closed = asyncio.Event()
    clients = []

    async def peer(socket):
        try:
            async for raw in socket:
                event = json.loads(raw)
                if event["type"] == "session.update":
                    await socket.send(json.dumps({"type": "session.updated"}))
                # This provider deliberately never acknowledges session.finish.
        finally:
            peer_closed.set()

    real_connect = websockets.connect
    async with websockets.serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def connect(_url, **kwargs):
            client = await real_connect(f"ws://127.0.0.1:{port}", **kwargs)
            clients.append(client)
            return client

        monkeypatch.setattr(qwen.websockets, "connect", connect)
        session = _RealtimeAsrSessionImpl(
            worker_fn=qwen.qwen_asr_worker,
            api_key="test-key",
            config=AsrSessionConfig(endpointing_mode="provider"),
            on_input_transcript=AsyncMock(),
            on_connection_error=AsyncMock(),
        )
        try:
            await session.connect()
            await asyncio.wait_for(session.close(), timeout=2)
            await asyncio.wait_for(peer_closed.wait(), timeout=0.2)
            assert clients[0].state.name == "CLOSED"
            assert session._worker_task.done()
            assert session._response_task.done()
            assert session._callback_task.done()
        finally:
            await session.close()
