"""Exercise subscription setup and late binding without arbitrary startup sleeps."""
from __future__ import annotations

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import zmq

from plugin.server.messaging import proactive_bridge as module

pytestmark = pytest.mark.plugin_unit


def test_subscription_setup_does_not_sleep(monkeypatch):
    """Readiness must follow connect/SUBSCRIBE even when the publisher is absent."""
    calls = []
    stop = threading.Event()

    class Socket:
        def setsockopt(self, *args):
            pass

        def connect(self, endpoint):
            calls.append(("connect", endpoint))

        def setsockopt_string(self, option, topic):
            calls.append(("subscribe", topic))

        def recv_multipart(self):
            stop.set()
            raise zmq.Again()

        def close(self, **kwargs):
            pass

    context = SimpleNamespace(socket=lambda kind: Socket())
    monkeypatch.setattr(module.zmq.Context, "instance", lambda: context)

    def unexpected_sleep(seconds):
        pytest.fail(f"subscription setup delayed by {seconds}s")

    # A module-local replacement avoids modifying the process-wide time module.
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=unexpected_sleep))
    bridge = module.ProactiveBridge()
    bridge._run(stop)
    assert bridge._subscribed.is_set()
    assert calls[0][0] == "connect"
    assert calls[1] == ("subscribe", "messages.")


def test_subscriber_recovers_when_publisher_binds_later(monkeypatch):
    """Real TCP sockets: early connect must still deliver after a late PUB bind."""
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    endpoint = f"tcp://127.0.0.1:{port}"
    monkeypatch.setenv("NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT", endpoint)
    monkeypatch.setattr(module, "_resolve_agent_push_addr", lambda: "inproc://unused-perf-regression")
    received = threading.Event()
    bridge = module.ProactiveBridge()
    monkeypatch.setattr(bridge, "_dispatch", lambda payload, sock: received.set())
    publisher = zmq.Context.instance().socket(zmq.PUB)
    publisher.linger = 0
    try:
        bridge.start()
        assert bridge.wait_until_subscribed(3)
        # No PUB existed when readiness was signaled. Exercise reconnect, not
        # a single first packet, which PUB/SUB never guarantees to retain.
        publisher.bind(endpoint)
        deadline = time.monotonic() + 5
        while not received.is_set() and time.monotonic() < deadline:
            publisher.send_multipart([b"messages.test", json.dumps({"payload": {"plugin_id": "test"}}).encode()])
            received.wait(.02)
        assert received.is_set()
    finally:
        bridge.stop()
        publisher.close(linger=0)
