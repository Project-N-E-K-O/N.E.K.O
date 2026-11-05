"""Real chat process -> event bridge -> foreign-loop router -> plugin SDK."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from pathlib import Path
import socket
import threading
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.plugin_integration


def _chat_process(connection, voice):
    async def scenario():
        from main_logic import agent_event_bus as bus
        from main_logic.core.turn import TurnMixin

        # No real utterance sinks or user configuration should be touched.
        bus._user_utterance_sinks = []
        bus.SESSION_PUB_ADDR = bus.ANALYZE_PUSH_ADDR = bus.AGENT_PUSH_ADDR = (
            "tcp://127.0.0.1:*"
        )

        async def ignore(_event):
            pass

        bridge = bus.MainServerAgentBridge(ignore)
        try:
            await bridge.start()
            bus._main_bridge_ref = bridge
            connection.send(
                {
                    "pid": os.getpid(),
                    "addresses": [
                        sock.getsockopt_string(bus.zmq.LAST_ENDPOINT)
                        for sock in (bridge.pub, bridge.analyze_push, bridge.pull)
                    ],
                }
            )
            while True:
                command = await asyncio.to_thread(connection.recv)
                if command == "stop":
                    break
                if command == "probe":
                    await bridge.publish_session_event(
                        {"event_type": "test_subscription_ready"}
                    )
                    connection.send(True)
                elif command == "publish":
                    tasks = []
                    manager = SimpleNamespace(
                        lanlan_name="context-test",
                        _fire_task=lambda coro: tasks.append(asyncio.create_task(coro)),
                    )
                    TurnMixin._publish_user_utterance_to_plugin_bus(
                        manager,
                        "SYNTHETIC_USER_CONTEXT",
                        is_voice_source=voice,
                    )
                    await asyncio.gather(*tasks)
                    connection.send(True)
        finally:
            bus._main_bridge_ref = None
            await bridge.stop()
            connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("voice", [True, False])
async def test_separate_chat_reaches_public_sdk_on_both_calling_loops(
    monkeypatch, tmp_path, voice
):
    from app.agent_server import api_runtime
    from main_logic import agent_event_bus as bus
    from plugin.core import host as host_module
    from plugin.core.host import PluginHost
    from plugin.core.state import state
    from plugin.server.messaging import plane_bridge
    from plugin.server.messaging.request_router import PluginRouter

    monkeypatch.setenv("NEKO_DO_NOT_TRACK", "1")
    monkeypatch.setenv("DO_NOT_TRACK", "1")
    monkeypatch.setattr(state, "_user_context_store", {})
    monkeypatch.setattr(state, "_plugin_comm_queue", None)
    monkeypatch.setattr(state, "plugin_control_ingress", None)
    monkeypatch.setattr(api_runtime, "_user_plugins_enabled", lambda: True)
    monkeypatch.setattr(plane_bridge, "publish_record", lambda **kwargs: False)
    monkeypatch.setattr(
        host_module, "_refresh_child_storage_layout_env", lambda _: None
    )

    spawn = multiprocessing.get_context("spawn")
    parent, child = spawn.Pipe()
    process = spawn.Process(target=_chat_process, args=(child, voice))
    bridge = host = thread = None
    addresses, router_owner, errors = [], {}, []
    process.start()
    child.close()
    try:
        assert await asyncio.to_thread(parent.poll, 20), "Chat process did not start"
        startup = parent.recv()
        assert startup["pid"] != os.getpid()
        addresses = startup["addresses"]
        for name, address in zip(
            ("SESSION_PUB_ADDR", "ANALYZE_PUSH_ADDR", "AGENT_PUSH_ADDR"), addresses
        ):
            monkeypatch.setattr(bus, name, address)
        ready, received = asyncio.Event(), asyncio.Event()

        async def consume(event):
            if event.get("event_type") == "test_subscription_ready":
                ready.set()
                return
            try:
                await api_runtime._on_session_event(event)
            finally:
                received.set()

        bridge = bus.AgentServerEventBridge(consume)
        await bridge.start()
        # Synchronize subscription readiness, without duplicating user events
        # to hide a dropped first PUB message.
        async with asyncio.timeout(5):
            while not ready.is_set():
                parent.send("probe")
                assert await asyncio.to_thread(parent.poll, 2)
                assert parent.recv() is True
                try:
                    await asyncio.wait_for(ready.wait(), 0.05)
                except TimeoutError:
                    pass
        parent.send("publish")
        assert await asyncio.to_thread(parent.poll, 5)
        assert parent.recv() is True
        await asyncio.wait_for(received.wait(), 3)
        assert len(state.get_user_context("context-test")) == 1

        router_ready = threading.Event()

        async def route():
            loop = asyncio.get_running_loop()
            loop.set_debug(True)
            router, stop = PluginRouter(), asyncio.Event()
            router_owner.update(loop=loop, router=router, stop=stop)
            try:
                await router.start()
                router_ready.set()
                await stop.wait()
            finally:
                await router.stop()

        def serve():
            try:
                asyncio.run(route())
            except BaseException as exc:
                errors.append(type(exc).__name__)
                router_ready.set()

        thread = threading.Thread(target=serve, name="user-context-router")
        thread.start()
        assert await asyncio.to_thread(router_ready.wait, 3)
        assert not errors
        directory = tmp_path / "user_context_probe"
        directory.mkdir()
        fixture = (
            Path(__file__).resolve().parents[1]
            / "fixtures/plugin_user_context_fixture.py"
        )
        (directory / "__init__.py").write_text(
            fixture.read_text(encoding="utf-8"), encoding="utf-8"
        )
        entry = "plugins.user_context_probe:UserContextPlugin"
        config = directory / "plugin.toml"
        config.write_text(
            f"[plugin]\nid='user_context_probe'\nname='user_context_probe'\nentry='{entry}'\n",
            encoding="utf-8",
        )
        host = PluginHost("user_context_probe", entry, config, source_only=True)
        monkeypatch.setattr(
            state,
            "get_plugin_hosts_snapshot_cached",
            lambda timeout=1: {"user_context_probe": host},
        )
        await host.start(startup_timeout=10, startup_failure="abort")
        result = await host.trigger("read", {}, timeout=6)
        assert result["async"] == result["worker"]
        assert len(result["async"]) == 1
        record = result["async"][0]
        assert record["type"] == "user_message"
        assert record["is_voice"] is voice
        assert record["lanlan"] == "context-test"
        assert record["event_id"] and isinstance(record["_ts"], float)
        assert record["content"] == "SYNTHETIC_USER_CONTEXT"
        assert state.get_user_context("unrelated-character") == []
        # Stop from the foreign loop; late cleanup must still finish on owner.
        await asyncio.wait_for(router_owner["router"].stop(), 3)
    finally:
        try:
            if host is not None and host.process.is_alive():
                await host.shutdown(timeout=3)
        finally:
            if thread is not None:
                router_owner["loop"].call_soon_threadsafe(router_owner["stop"].set)
                await asyncio.to_thread(thread.join, 4)
                assert not thread.is_alive() and not errors
            if bridge is not None:
                await bridge.stop()
            if process.is_alive():
                parent.send("stop")
                await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 3)
            parent.close()
            assert not process.is_alive()
            for address in addresses:
                with socket.socket() as probe:
                    probe.settimeout(0.1)
                    assert (
                        probe.connect_ex(("127.0.0.1", int(address.rsplit(":", 1)[1])))
                        != 0
                    )
