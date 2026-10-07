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
    bridge._run(stop, bridge._subscribed, bridge._startup_finished)
    assert bridge._subscribed.is_set()
    assert calls[0][0] == "connect"
    assert calls[1] == ("subscribe", "messages.")


def test_subscriber_recovers_when_publisher_binds_later(monkeypatch):
    """Real TCP sockets: early connect must still deliver after a late PUB bind."""
    monkeypatch.setattr(module, "_resolve_agent_push_addr", lambda: "inproc://unused-perf-regression")
    publisher = zmq.Context.instance().socket(zmq.PUB)
    publisher.linger = 0
    bridge = None
    received = threading.Event()
    try:
        # Reserve a port, let the bridge's SUB connect while it is still unbound,
        # then bind the PUB. A parallel process can grab the released port in the
        # gap, so retry the whole arrange on a bind conflict instead of flaking
        # CI over something that is not the behaviour under test.
        bound = False
        for _ in range(5):
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            endpoint = f"tcp://127.0.0.1:{port}"
            monkeypatch.setenv("NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT", endpoint)
            received = threading.Event()
            bridge = module.ProactiveBridge()
            monkeypatch.setattr(bridge, "_dispatch", lambda payload, sock: received.set())
            bridge.start()
            assert bridge.wait_until_subscribed(3)
            try:
                publisher.bind(endpoint)
            except zmq.ZMQError as exc:
                # Compare constants from the same libzmq runtime, rather than
                # platform-dependent error text or Python's Winsock errno.
                if exc.errno != zmq.EADDRINUSE:
                    raise
                bridge.stop()
                bridge = None
                continue
            bound = True
            break
        assert bound, "could not bind a reserved loopback port after retries"
        # No PUB existed when readiness was signaled. Exercise reconnect, not
        # a single first packet, which PUB/SUB never guarantees to retain.
        deadline = time.monotonic() + 5
        while not received.is_set() and time.monotonic() < deadline:
            publisher.send_multipart([b"messages.test", json.dumps({"payload": {"plugin_id": "test"}}).encode()])
            received.wait(.02)
        assert received.is_set()
    finally:
        if bridge is not None:
            bridge.stop()
        publisher.close(linger=0)


def test_socket_setup_failure_does_not_signal_ready(monkeypatch):
    """A failure building the delivery (PUSH) socket must leave the bridge not-ready.

    Regression guard: ``_subscribed`` used to be set right after SUBSCRIBE,
    before ``push_sock`` existed. An exception in between (e.g. EMFILE on
    ``ctx.socket``) left the thread dead with ``_subscribed`` set, so
    ``wait_for_proactive_subscriber`` returned True and startup latched a bridge
    that could receive but never forward -- the exact silent non-delivery the
    readiness signal exists to prevent.
    """
    stop = threading.Event()

    class SubSocket:
        linger = 0

        def setsockopt(self, *args):
            pass

        def connect(self, endpoint):
            pass

        def setsockopt_string(self, option, topic):
            pass

        def close(self, **kwargs):
            pass

    def fake_socket(kind):
        if kind == zmq.SUB:
            return SubSocket()
        raise zmq.ZMQError("simulated fd exhaustion on the PUSH socket")

    context = SimpleNamespace(socket=fake_socket)
    monkeypatch.setattr(module.zmq.Context, "instance", lambda: context)

    bridge = module.ProactiveBridge()
    bridge._run(stop, bridge._subscribed, bridge._startup_finished)
    assert not bridge._subscribed.is_set(), (
        "readiness signalled despite a dead forwarder"
    )


@pytest.mark.parametrize("failure_stage", ["context", "push"])
def test_setup_failure_releases_an_existing_waiter(monkeypatch, failure_stage):
    """An already waiting caller sees failure without exhausting its timeout."""
    setup_entered = threading.Event()
    release_failure = threading.Event()
    waiter_entered = threading.Event()
    waiter_finished = threading.Event()
    closed = []
    results = []

    class SubSocket:
        def setsockopt(self, *args):
            pass

        def connect(self, endpoint):
            pass

        def setsockopt_string(self, *args):
            pass

        def close(self, **kwargs):
            closed.append("sub")

    def fail():
        setup_entered.set()
        assert release_failure.wait(5)
        raise zmq.ZMQError("simulated setup failure")

    def socket_factory(kind):
        if kind == zmq.SUB:
            return SubSocket()
        fail()

    def context_factory():
        if failure_stage == "context":
            fail()
        return SimpleNamespace(socket=socket_factory)

    monkeypatch.setattr(module.zmq.Context, "instance", context_factory)
    bridge = module.ProactiveBridge()
    bridge.start()
    waiter = None
    try:
        assert setup_entered.wait(3)
        original_wait = bridge._startup_finished.wait

        def observed_wait(timeout):
            waiter_entered.set()
            return original_wait(timeout)

        monkeypatch.setattr(bridge._startup_finished, "wait", observed_wait)

        def wait_for_setup():
            results.append(bridge.wait_until_subscribed(30))
            waiter_finished.set()

        waiter = threading.Thread(target=wait_for_setup, daemon=True)
        waiter.start()
        assert waiter_entered.wait(3)
        release_failure.set()
        assert waiter_finished.wait(3), "setup failure left the caller blocked"
        assert results == [False]
        assert not bridge.is_alive()
        assert closed == (["sub"] if failure_stage == "push" else [])
    finally:
        release_failure.set()
        bridge.stop()
        if waiter is not None:
            waiter.join(3)
