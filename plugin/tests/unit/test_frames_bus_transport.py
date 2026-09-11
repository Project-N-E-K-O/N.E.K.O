"""Guards for the ``frames`` bus transport (host publish -> plugin pull).

What this pins, and why each one is a real failure mode rather than a
restatement of the code:

* The store is registered by BOTH message-plane entry points. Only one of them
  registering is silent: ingest answers an unknown store with
  ``_record_drop("store_unresolved")`` and tells the publisher nothing, so
  standalone mode would drop every frame with a green log.
* ``frames`` is its own store with its own, small capacity. ``messages`` is
  ruled out because proactive_bridge subscribes to the ``"messages."`` prefix
  and would json.loads every frame on the proactive delivery thread; the
  generic 20000-deep per-topic deque is ruled out because at frame sizes that
  is gigabytes of the user's screen history resident in agent_server.
* The payload bound is measured, not assumed. Ingest drops an oversized
  payload on the far side of a socket, so a publisher that does not measure
  learns nothing.
* One base64 copy. A raw-bytes twin packs fine and then blows the same cap at
  roughly 2.3x, which is again a silent far-side drop.
* Frames yield when the shared bridge queue is behind, so a lossy 200 KB
  producer cannot starve the small records that share it.
"""
from __future__ import annotations

import socket
import threading
import time
import uuid
from typing import Any, Dict, Iterator, List

import ormsgpack
import pytest

from plugin.core.bus.frames import FrameClient, FrameRecord
from plugin.message_plane import main as mp_main
from plugin.message_plane.ingest_server import MessagePlaneIngestServer
from plugin.message_plane.rpc_server import MessagePlaneRpcServer
from plugin.message_plane.stores import (
    FRAMES_STORE_NAME,
    FRAMES_TOPIC,
    build_default_store_registry,
)
from plugin.server.messaging import plane_bridge

pytestmark = pytest.mark.plugin_unit


# -- store registration -------------------------------------------------


def test_frames_store_has_its_own_small_capacity() -> None:
    import plugin.settings as settings

    assert settings.MESSAGE_PLANE_FRAMES_STORE_MAXLEN != settings.MESSAGE_PLANE_STORE_MAXLEN
    assert 2 <= settings.MESSAGE_PLANE_FRAMES_STORE_MAXLEN <= 8

    registry = build_default_store_registry(maxlen=20000, frames_maxlen=4)
    frames = registry.get(FRAMES_STORE_NAME)
    assert frames is not None
    assert frames.maxlen == 4
    # Not folded into an existing store: both alternatives break a real reader.
    assert FRAMES_STORE_NAME not in ("messages", "events")
    assert registry.get("messages") is not frames


class _StubServer:
    """Stands in for the pub/ingest/rpc servers so an entry point can be RUN.

    Asserting on the source text of the two entry points would pass on a
    registration that is never reached; running them with the sockets stubbed
    out exercises the actual registration statement.
    """

    last_stores: Any = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.stores = kwargs.get("stores")
        if self.stores is not None:
            type(self).last_stores = self.stores

    def serve_forever(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def close(self) -> None:
        return None

    def publish(self, *args: Any, **kwargs: Any) -> None:
        return None


class _StubSignal:
    SIGINT = 2
    SIGTERM = 15

    @staticmethod
    def signal(*_args: Any, **_kwargs: Any) -> None:
        # Never install a real handler: run_message_plane's SIGINT handler
        # raises SystemExit, and leaving it behind would arm that for the rest
        # of the pytest process.
        return None


def test_standalone_entry_point_registers_the_frames_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mp_main, "signal", _StubSignal)
    _StubServer.last_stores = None
    for name in ("MessagePlanePubServer", "MessagePlaneIngestServer", "MessagePlaneRpcServer"):
        monkeypatch.setattr(mp_main, name, _StubServer, raising=True)

    mp_main.run_message_plane(
        rpc_endpoint="inproc://unused-rpc",
        pub_endpoint="inproc://unused-pub",
        ingest_endpoint="inproc://unused-ingest",
        auth_token="test-token",
    )

    registry = _StubServer.last_stores
    assert registry is not None
    assert registry.get(FRAMES_STORE_NAME) is not None


def test_embedded_runner_registers_the_frames_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from plugin.message_plane import ingest_server as ingest_mod
    from plugin.message_plane import pub_server as pub_mod
    from plugin.message_plane import rpc_server as rpc_mod
    from plugin.message_plane.runner import MessagePlaneEndpoints, PythonMessagePlaneRunner

    _StubServer.last_stores = None
    # The runner imports these inside the function, so patch them at the source.
    monkeypatch.setattr(ingest_mod, "MessagePlaneIngestServer", _StubServer)
    monkeypatch.setattr(pub_mod, "MessagePlanePubServer", _StubServer)
    monkeypatch.setattr(rpc_mod, "MessagePlaneRpcServer", _StubServer)

    runner = PythonMessagePlaneRunner(
        endpoints=MessagePlaneEndpoints(
            rpc="inproc://unused-rpc",
            pub="inproc://unused-pub",
            ingest="inproc://unused-ingest",
        ),
        auth_token="test-token",
    )
    try:
        runner.start()
    finally:
        runner.stop()

    registry = _StubServer.last_stores
    assert registry is not None
    assert registry.get(FRAMES_STORE_NAME) is not None


def test_pull_client_targets_the_registered_store_and_topic() -> None:
    """The store/topic the reader asks for must be the ones the host writes.

    BusRpcClientBase hardcodes ``topic="all"``: a frames record published under
    any other topic would be invisible to ``ctx.bus.frames.get()`` while every
    layer below reported success.
    """
    assert FrameClient._store_name == FRAMES_STORE_NAME

    class _Ctx:
        plugin_id = "reader"

    op, args = FrameClient(_Ctx())._build_query_args(max_count=3)
    assert args["store"] == FRAMES_STORE_NAME
    assert args["topic"] == FRAMES_TOPIC
    assert op == "bus.get_recent"


# -- publish helper: bounds ---------------------------------------------


class _SpyBridge:
    def __init__(self, *, accept: bool = True) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._accept = accept

    def enqueue_delta(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return self._accept


@pytest.fixture()
def spy_bridge(monkeypatch: pytest.MonkeyPatch) -> _SpyBridge:
    spy = _SpyBridge()
    monkeypatch.setattr(plane_bridge, "_bridge", spy)
    return spy


def _frame(**kwargs: Any) -> Dict[str, Any]:
    kwargs.setdefault("image_base64", "QUJD")
    kwargs.setdefault("source", "screen")
    return plane_bridge.build_frame_record(**kwargs)


def test_publish_frame_goes_to_the_frames_store_not_messages(spy_bridge: _SpyBridge) -> None:
    assert plane_bridge.publish_frame(_frame()) is True
    assert len(spy_bridge.calls) == 1
    call = spy_bridge.calls[0]
    assert call["store"] == FRAMES_STORE_NAME
    assert call["topic"] == FRAMES_TOPIC
    assert call["max_queue_depth"] is not None


def test_oversize_frame_is_refused_before_it_costs_a_queue_slot(
    monkeypatch: pytest.MonkeyPatch, spy_bridge: _SpyBridge
) -> None:
    monkeypatch.setattr(plane_bridge, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)

    ok = _frame(image_base64="A" * 1024)
    assert len(ormsgpack.packb(ok)) <= 4096
    assert plane_bridge.publish_frame(ok) is True

    too_big = _frame(image_base64="A" * 8192)
    assert len(ormsgpack.packb(too_big)) > 4096
    assert plane_bridge.publish_frame(too_big) is False

    # Exactly one record reached the bridge: the oversized one never queued.
    assert len(spy_bridge.calls) == 1


def test_the_measured_bound_is_the_one_ingest_enforces() -> None:
    """A local guess would drift from the cap that actually drops the record."""
    import plugin.settings as settings

    from plugin.message_plane import ingest_server

    assert plane_bridge.MESSAGE_PLANE_PAYLOAD_MAX_BYTES == settings.MESSAGE_PLANE_PAYLOAD_MAX_BYTES
    assert ingest_server.MESSAGE_PLANE_PAYLOAD_MAX_BYTES == settings.MESSAGE_PLANE_PAYLOAD_MAX_BYTES


def test_a_raw_bytes_twin_is_refused(spy_bridge: _SpyBridge) -> None:
    record = _frame()
    record["image_bytes"] = b"\x00\x01\x02"
    assert plane_bridge.publish_frame(record) is False
    assert spy_bridge.calls == []

    nested = _frame(metadata={"thumbnails": [{"blob": bytearray(b"xy")}]})
    assert plane_bridge.publish_frame(nested) is False
    assert spy_bridge.calls == []


def test_frames_yield_once_the_shared_bridge_queue_is_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 200 KB lossy producer must not starve the small records beside it."""
    bridge = plane_bridge._Bridge()
    bridge._enabled = True  # never started: nothing drains the queue
    monkeypatch.setattr(plane_bridge, "_bridge", bridge)
    monkeypatch.setattr(plane_bridge, "MESSAGE_PLANE_FRAMES_BRIDGE_MAX_PENDING", 3)

    assert plane_bridge.publish_frame(_frame()) is True
    assert plane_bridge.publish_frame(_frame()) is True
    assert plane_bridge.publish_frame(_frame()) is True
    assert plane_bridge.publish_frame(_frame()) is False

    # ...while an ordinary record still gets through: the ceiling is the
    # frame's, not the queue's.
    plane_bridge.publish_record(store="events", record={"type": "x"})
    assert bridge._q.qsize() == 4


# -- end to end: host publish -> plugin pull ----------------------------


def _free_tcp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


class _ReaderCtx:
    """Minimal stand-in for PluginContext: only what BusRpcClientBase reads."""

    def __init__(self, rpc: Any) -> None:
        self.plugin_id = "frame_reader"
        self._mp_rpc_client = rpc


@pytest.fixture()
def live_plane(monkeypatch: pytest.MonkeyPatch) -> Iterator[Dict[str, Any]]:
    """Real ingest + rpc servers and a real bridge, wired end to end.

    ingest rides ``inproc://`` (the bridge and the server share
    ``zmq.Context.instance()``); rpc must be tcp because MessagePlaneRpcClient
    builds its own thread-local context, which inproc cannot cross.
    """
    from plugin.core.message_plane_transport import MessagePlaneRpcClient

    token = "e2e-token"
    ingest_ep = "inproc://neko-frames-e2e-" + uuid.uuid4().hex
    rpc_ep = "tcp://127.0.0.1:" + str(_free_tcp_port())

    registry = build_default_store_registry(maxlen=64, frames_maxlen=4)
    ingest = MessagePlaneIngestServer(
        endpoint=ingest_ep, stores=registry, pub_server=None, auth_token=token
    )
    rpc = MessagePlaneRpcServer(endpoint=rpc_ep, pub_server=None, stores=registry)

    ingest_thread = threading.Thread(target=ingest.serve_forever, daemon=True)
    rpc_thread = threading.Thread(target=rpc.serve_forever, daemon=True)
    ingest_thread.start()
    rpc_thread.start()

    bridge = plane_bridge._Bridge()
    bridge._enabled = True
    bridge._endpoint = ingest_ep
    # The bridge stamps its own module-level credential; the plane must be the
    # one it starts, so hand the same secret to ingest.
    monkeypatch.setattr(plane_bridge, "_INGEST_AUTH_TOKEN", token)
    monkeypatch.setattr(plane_bridge, "_bridge", bridge)
    bridge.start()

    client = MessagePlaneRpcClient(plugin_id="frame_reader", endpoint=rpc_ep)
    try:
        yield {"ctx": _ReaderCtx(client), "registry": registry}
    finally:
        # Every ZMQ socket opened here must be closed before the process
        # exits: pyzmq terms the shared context at exit and that call BLOCKS
        # on an open socket, which turns a red test into a hung CI job. The
        # bridge closes its PUSH socket from its own thread, so joining it is
        # part of the close, not politeness -- a test that fails fast reaches
        # interpreter exit before that thread's 0.2s poll wakes up.
        bridge.stop()
        ingest.stop()
        rpc.stop()
        for thread in (bridge._thread, ingest_thread, rpc_thread):
            if thread is not None:
                thread.join(timeout=2.0)
        try:
            rpc.close()
        except Exception:
            pass
        # MessagePlaneRpcClient owns a thread-local context and DEALER socket
        # and offers no close(); leaving them open wedges the same atexit term.
        for attr, closer in (("sock", "close"), ("ctx", "term")):
            target = getattr(getattr(client, "_tls", None), attr, None)
            if target is not None:
                try:
                    getattr(target, closer)()
                except Exception:
                    pass


def _read_frames(
    ctx: Any,
    *,
    until: Any,
    timeout_s: float = 5.0,
) -> List[FrameRecord]:
    """Poll ``ctx.bus.frames.get()`` until ``until(records)`` or the deadline.

    The publish path is asynchronous (bridge thread -> socket -> ingest
    thread), so the condition has to be the FULL one the test asserts. Waiting
    only for "enough records" would let an assertion run against a half-drained
    store and go red on timing rather than on behaviour.
    """
    deadline = time.time() + timeout_s
    records: List[FrameRecord] = []
    while True:
        records = list(FrameClient(ctx).get(max_count=10, timeout=2.0))
        if until(records):
            return records
        if time.time() >= deadline:
            return records
        time.sleep(0.05)


def test_a_published_frame_is_readable_through_the_plugin_accessor(
    live_plane: Dict[str, Any],
) -> None:
    accepted = plane_bridge.publish_frame(
        plane_bridge.build_frame_record(
            image_base64="SGVsbG8gZnJhbWU=",
            source="screen",
            captured_at=1234.5,
            turn_id="turn-7",
            generation=3,
            mime="image/jpeg",
            frame_id="frame-abc",
        )
    )
    assert accepted is True

    records = _read_frames(live_plane["ctx"], until=lambda rs: len(rs) >= 1)
    assert len(records) == 1
    frame = records[0]
    assert frame.image_base64 == "SGVsbG8gZnJhbWU="
    assert frame.source == "screen"
    assert frame.captured_at == 1234.5
    assert frame.turn_id == "turn-7"
    assert frame.generation == 3
    assert frame.mime == "image/jpeg"
    assert frame.frame_id == "frame-abc"


def test_the_store_keeps_only_the_last_few_frames(live_plane: Dict[str, Any]) -> None:
    """The contract is "the last few frames", not a log."""
    for i in range(10):
        plane_bridge.publish_frame(
            plane_bridge.build_frame_record(
                image_base64="frame-" + str(i), source="screen", frame_id="f" + str(i)
            )
        )

    expected = ["f6", "f7", "f8", "f9"]
    records = _read_frames(
        live_plane["ctx"],
        until=lambda rs: [r.frame_id for r in rs] == expected,
    )
    # Exactly the last four survive: the deque is 4 deep, so the six older
    # frames are gone even though every one of them was accepted.
    assert [r.frame_id for r in records] == expected


def test_a_light_read_keeps_the_dedupe_keys_and_drops_the_image(
    live_plane: Dict[str, Any],
) -> None:
    """``light=True`` returns the index alone -- and the index has to be enough.

    ``FrameClient`` itself always asks for the full record, but ``light=True``
    is a supported read on every store and the frames contract tells a puller
    to dedupe on ``generation`` / ``id``. Both therefore have to survive a trip
    that drops the payload. Before ``generation`` was projected into the index,
    a light read reported ``generation=None`` for a frame that has one, so the
    dedupe the module documents was silently a no-op.
    """
    accepted = plane_bridge.publish_frame(
        plane_bridge.build_frame_record(
            image_base64="bGlnaHQtZnJhbWU=",
            source="screen",
            captured_at=99.5,
            # 0 is a real generation and the falsy one: a fallback written with
            # ``or`` instead of ``is None`` would lose exactly this value.
            generation=0,
            frame_id="frame-light",
        )
    )
    assert accepted is True

    # The publish path is asynchronous; the light read below has no retry of
    # its own, so wait for arrival on the full read first.
    _read_frames(live_plane["ctx"], until=lambda rs: len(rs) >= 1)

    response = live_plane["ctx"]._mp_rpc_client.request(
        op="bus.get_recent",
        args={
            "store": FRAMES_STORE_NAME,
            "topic": FRAMES_TOPIC,
            "limit": 10,
            "light": True,
        },
        timeout=2.0,
    )
    items = response["result"]["items"]
    assert len(items) == 1
    # The whole point of light: the 200KB-class field never crosses the socket.
    assert "payload" not in items[0]

    record = FrameRecord.from_index(items[0]["index"])
    assert record.image_base64 is None
    assert record.frame_id == "frame-light"
    assert record.generation == 0
    assert record.source == "screen"


# ── bus clients must follow the plane to its fallback RPC port ─────────


@pytest.mark.plugin_unit
def test_bus_clients_resolve_the_rpc_endpoint_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dual of the ingest-endpoint fix, on the read path.

    ``plugin.settings`` computes ``MESSAGE_PLANE_ZMQ_RPC_ENDPOINT`` when it is
    imported, which is before ``build_message_plane_runner()`` runs. A port
    collision moves the plane and republishes the address through the
    environment, so anything holding the constant talks to the occupied port
    and every ``ctx.bus.*.get()`` fails.

    Mutation: read ``MESSAGE_PLANE_ZMQ_RPC_ENDPOINT`` in ``_ensure_rpc``
    instead of calling the resolver.
    """
    from types import SimpleNamespace

    from plugin.core.bus import _client_base

    created: dict[str, str] = {}

    class _FakeRpc:
        def __init__(self, *, plugin_id: str, endpoint: str) -> None:
            created["endpoint"] = endpoint

    monkeypatch.setattr(_client_base, "_MessagePlaneRpcClient", _FakeRpc)
    monkeypatch.setenv("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT", "tcp://127.0.0.1:48865")

    _client_base._ensure_rpc(SimpleNamespace(plugin_id="p"))

    assert created["endpoint"] == "tcp://127.0.0.1:48865", (
        "bus client 还连着 import 期冻结的 RPC 端口——端口一冲突全部读失败"
    )


@pytest.mark.plugin_unit
def test_no_rpc_consumer_still_reads_the_frozen_constant() -> None:
    """Discovery, not a hand-written list of the two sites fixed today.

    A third consumer added later that imports the constant would be stale in
    exactly the same way, and nothing would fail to say so.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    offenders = []
    for path in (root / "plugin").rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in rel or rel.endswith("plugin/settings.py"):
            continue
        # message_plane/ owns the plane itself: main.py binds it and runner.py
        # is the code that picks the fallback, so both legitimately read the
        # configured value rather than the resolved one.
        if rel.startswith("plugin/message_plane/"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "MESSAGE_PLANE_ZMQ_RPC_ENDPOINT" in text:
            offenders.append(rel)

    assert offenders == [], (
        f"这些地方仍在用 import 期冻结的 RPC 端点，端口冲突时会连到被占的端口：{offenders}"
    )


@pytest.mark.plugin_unit
def test_a_cached_rpc_client_is_rebuilt_when_the_endpoint_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolving at call time is only half the job while the client is cached.

    The cached client keeps the endpoint it was built with and its sockets are
    already connected there, so a plane that restarts onto a different fallback
    port would leave a plugin talking to the old address until it times out
    (CodeRabbit).

    Mutation: drop the endpoint comparison and reuse any cached client.
    """
    from types import SimpleNamespace

    from plugin.core.bus import _client_base

    built: list[str] = []

    class _FakeRpc:
        def __init__(self, *, plugin_id: str, endpoint: str) -> None:
            self._endpoint = endpoint
            built.append(endpoint)

    monkeypatch.setattr(_client_base, "_MessagePlaneRpcClient", _FakeRpc)
    ctx = SimpleNamespace(plugin_id="p")  # 无预置客户端：走 _ensure_rpc 自建这一路

    monkeypatch.setenv("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT", "tcp://127.0.0.1:38865")
    first = _client_base._ensure_rpc(ctx)
    again = _client_base._ensure_rpc(ctx)
    assert again is first, "端点没变却重建了客户端——每次 bus 调用都新建 socket"

    monkeypatch.setenv("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT", "tcp://127.0.0.1:48865")
    moved = _client_base._ensure_rpc(ctx)

    assert moved is not first, "plane 换了端口，缓存客户端还连着老地址"
    assert built == ["tcp://127.0.0.1:38865", "tcp://127.0.0.1:48865"]


@pytest.mark.plugin_unit
def test_an_injected_rpc_client_is_never_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client the caller supplied is theirs, whatever the environment says.

    The in-process end-to-end fixtures in this file wire ``ctx._mp_rpc_client``
    to a client bound to a temporary port. Comparing that against the resolved
    environment value and rebuilding on mismatch swaps it for the default
    endpoint, where nothing is listening — every request then blocks until its
    timeout and the suite hangs. That is not hypothetical: the first version of
    the endpoint-refresh did exactly this.

    Mutation: drop the ``_neko_endpoint_autoresolved`` check and rebuild any
    cached client whose endpoint differs.
    """
    from types import SimpleNamespace

    from plugin.core.bus import _client_base

    def _explode(**_kw):  # pragma: no cover - the failure we guard
        raise AssertionError("rebuilt a client the caller injected")

    monkeypatch.setattr(_client_base, "_MessagePlaneRpcClient", _explode)
    monkeypatch.setenv("NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT", "tcp://127.0.0.1:48865")

    injected = SimpleNamespace(_endpoint="inproc://the-callers-own-plane")
    ctx = SimpleNamespace(plugin_id="p", _mp_rpc_client=injected)

    assert _client_base._ensure_rpc(ctx) is injected
