from __future__ import annotations

import pickle
import threading
import time
from pathlib import Path

import pytest
import ormsgpack
import zmq

from plugin.core import zmq_transport


_PICKLE_EXECUTED = False


def _mark_pickle_executed() -> dict[str, object]:
    global _PICKLE_EXECUTED
    _PICKLE_EXECUTED = True
    return {}


class _MaliciousPayload:
    def __reduce__(self):
        return (_mark_pickle_executed, ())


@pytest.mark.plugin_unit
def test_uplink_decoder_rejects_pickle_without_executing_it() -> None:
    global _PICKLE_EXECUTED
    _PICKLE_EXECUTED = False
    wire_payload = pickle.dumps((zmq_transport.CH_COMM, _MaliciousPayload()))

    with pytest.raises(ValueError, match="invalid uplink payload"):
        zmq_transport._decode_uplink(wire_payload, expected_token="host-channel-token")

    assert _PICKLE_EXECUTED is False


@pytest.mark.plugin_unit
def test_uplink_messagepack_round_trip_preserves_channel_and_payload() -> None:
    payload = {
        "type": "PLUGIN_TO_PLUGIN",
        "from_plugin": "demo-plugin",
        "request_id": "request-1",
        "enabled": True,
        "note": "round-trip",
        "binary": b"safe-bytes",
    }

    encoded = zmq_transport._encode_uplink(
        "host-channel-token",
        zmq_transport.CH_COMM,
        payload,
    )

    assert zmq_transport._decode_uplink(
        encoded,
        expected_token="host-channel-token",
    ) == (
        zmq_transport.CH_COMM,
        payload,
    )


@pytest.mark.plugin_unit
def test_uplink_decoder_rejects_another_plugin_host_channel_token() -> None:
    encoded = zmq_transport._encode_uplink(
        "attacker-channel-token",
        zmq_transport.CH_COMM,
        {
            "type": "PLUGIN_TO_PLUGIN",
            "from_plugin": "victim-plugin",
            "request_id": "request-1",
        },
    )

    with pytest.raises(ValueError, match="invalid uplink credential"):
        zmq_transport._decode_uplink(
            encoded,
            expected_token="victim-channel-token",
        )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_message_uplink_is_physically_isolated_from_control_results() -> None:
    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        message_uplink_endpoint=host.message_uplink_endpoint,
    )
    try:
        child.channel_sender(zmq_transport.CH_MSG).put_nowait({"type": "MESSAGE_PUSH"})

        assert await host.recv_message(timeout_ms=1000) == (
            zmq_transport.CH_MSG,
            {"type": "MESSAGE_PUSH"},
        )
        assert await host.recv(timeout_ms=100) is None
    finally:
        child.close()
        host.close()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_fast_message_sender_batches_on_the_authenticated_message_uplink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plugin.settings as plugin_settings

    monkeypatch.setattr(
        plugin_settings,
        "PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE",
        2,
    )
    monkeypatch.setattr(
        plugin_settings,
        "PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS",
        10_000,
    )
    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        message_uplink_endpoint=host.message_uplink_endpoint,
    )
    try:
        sender = child.channel_sender(zmq_transport.CH_MSG)
        sender.put_fast_nowait({"message_id": "one"})
        sender.put_fast_nowait({"message_id": "two"})

        assert await host.recv_message(timeout_ms=1000) == (
            zmq_transport.CH_MSG_BATCH,
            {
                "items": [
                    {"message_id": "one"},
                    {"message_id": "two"},
                ],
            },
        )
        assert await host.recv(timeout_ms=100) is None
    finally:
        child.close()
        host.close()


@pytest.mark.plugin_unit
def test_fast_message_batcher_records_failed_batch_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged = threading.Event()
    records: list[str] = []

    class _FailingTransport:
        def send_uplink_nowait(self, _channel: str, _payload: object) -> None:
            raise RuntimeError("private-transport-detail")

    class _Logger:
        def warning(self, message: str) -> None:
            records.append(message)
            logged.set()

    monkeypatch.setattr(zmq_transport, "logger", _Logger(), raising=False)
    batcher = zmq_transport._AuthenticatedMessageBatcher(
        _FailingTransport(),  # type: ignore[arg-type]
        batch_size=1,
        flush_interval_ms=5,
        max_queue=10,
        reject_ratio=0.9,
        enqueue_timeout_s=0,
    )
    batcher.start()
    try:
        batcher.enqueue({"message_id": "dropped"})
        was_logged = logged.wait(timeout=1.0)
    finally:
        batcher.stop()

    assert was_logged is True
    assert batcher._dropped == 1
    assert "items=1" in records[0]
    assert "total_dropped=1" in records[0]
    assert "RuntimeError" in records[0]
    assert "private-transport-detail" not in records[0]


@pytest.mark.plugin_unit
def test_fast_message_batcher_flushes_a_local_partial_batch_on_stop() -> None:
    sent: list[tuple[str, dict[str, object]]] = []

    class _Transport:
        def send_uplink_nowait(
            self,
            channel: str,
            payload: dict[str, object],
        ) -> None:
            sent.append((channel, payload))

    class _QueueWithOneAcceptedItem:
        def __init__(self) -> None:
            self._returned_item = False

        def get(self, timeout: float) -> dict[str, str]:
            _ = timeout
            if not self._returned_item:
                self._returned_item = True
                return {"message_id": "accepted-before-stop"}
            raise zmq_transport.queue.Empty

        def empty(self) -> bool:
            return self._returned_item

    class _StopBetweenFlushCheckAndLoopCondition:
        def __init__(self) -> None:
            self._checks = 0

        def is_set(self) -> bool:
            self._checks += 1
            return self._checks >= 3

    batcher = zmq_transport._AuthenticatedMessageBatcher(
        _Transport(),  # type: ignore[arg-type]
        batch_size=10,
        flush_interval_ms=10_000,
        max_queue=10,
        reject_ratio=0.9,
        enqueue_timeout_s=0,
    )
    batcher._queue = _QueueWithOneAcceptedItem()  # type: ignore[assignment]
    batcher._stop = _StopBetweenFlushCheckAndLoopCondition()  # type: ignore[assignment]

    batcher._run()

    assert sent == [
        (
            zmq_transport.CH_MSG_BATCH,
            {"items": [{"message_id": "accepted-before-stop"}]},
        )
    ]


@pytest.mark.plugin_unit
def test_fast_message_batcher_preserves_zero_as_an_unbounded_queue() -> None:
    batcher = zmq_transport._AuthenticatedMessageBatcher(
        object(),  # type: ignore[arg-type]
        batch_size=256,
        flush_interval_ms=5,
        max_queue=0,
        reject_ratio=0.9,
        enqueue_timeout_s=0,
    )

    for index in range(3):
        batcher.enqueue({"message_id": str(index)})

    assert batcher._queue.qsize() == 3


@pytest.mark.plugin_unit
def test_fast_message_sender_rejects_after_transport_close() -> None:
    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        message_uplink_endpoint=host.message_uplink_endpoint,
    )
    sender = child.channel_sender(zmq_transport.CH_MSG)
    child.close()
    caught: Exception | None = None
    try:
        sender.put_fast_nowait({"message_id": "after-close"})
    except Exception as exc:  # noqa: BLE001 - asserting the public failure below
        caught = exc
    finally:
        batcher = child._message_batcher
        if batcher is not None:
            batcher.stop()
        host.close()

    assert isinstance(caught, RuntimeError)
    assert str(caught) == "plugin transport is closed"


@pytest.mark.plugin_unit
def test_uplink_result_normalizes_path_values() -> None:
    encoded = zmq_transport._encode_uplink(
        "host-channel-token",
        zmq_transport.CH_RES,
        {
            "req_id": "request-1",
            "success": True,
            "data": {
                "path": Path("artifacts/result.json"),
            },
            "error": None,
        },
    )

    _channel, payload = zmq_transport._decode_uplink(
        encoded,
        expected_token="host-channel-token",
    )

    assert payload["data"]["path"] == str(Path("artifacts/result.json"))


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "container",
    [
        pytest.param(("alpha", "beta"), id="tuple"),
        pytest.param({"alpha", "beta"}, id="set"),
        pytest.param(frozenset({"alpha", "beta"}), id="frozenset"),
    ],
)
def test_uplink_result_rejects_non_json_container_types(container: object) -> None:
    encoded = zmq_transport._encode_uplink(
        "host-channel-token",
        zmq_transport.CH_RES,
        {
            "req_id": "request-1",
            "success": True,
            "data": {"labels": container},
            "error": None,
        },
    )

    _channel, payload = zmq_transport._decode_uplink(
        encoded,
        expected_token="host-channel-token",
    )

    assert payload == {
        "req_id": "request-1",
        "success": False,
        "data": None,
        "error": "Plugin result is not MessagePack-serializable",
    }


@pytest.mark.plugin_unit
def test_unserializable_uplink_result_returns_an_immediate_error() -> None:
    class _UnsupportedResult:
        pass

    encoded = zmq_transport._encode_uplink(
        "host-channel-token",
        zmq_transport.CH_RES,
        {
            "req_id": "request-1",
            "success": True,
            "data": {"value": _UnsupportedResult()},
            "error": None,
        },
    )

    _channel, payload = zmq_transport._decode_uplink(
        encoded,
        expected_token="host-channel-token",
    )

    assert payload == {
        "req_id": "request-1",
        "success": False,
        "data": None,
        "error": "Plugin result is not MessagePack-serializable",
    }


@pytest.mark.plugin_unit
def test_message_uplink_ceiling_is_derived_from_the_ingest_payload_cap() -> None:
    """The ceiling tracks the two host settings it is derived from.

    Asserting only ``socket == _message_uplink_max_bytes()`` would pass for any
    formula at all, including one that dropped a term, so the derivation itself
    is pinned here against the settings module rather than restated as a
    literal.
    """
    import plugin.settings as plugin_settings

    assert zmq_transport._message_uplink_max_bytes() == (
        int(plugin_settings.MESSAGE_PLANE_PAYLOAD_MAX_BYTES)
        * int(plugin_settings.PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE)
        + zmq_transport._MESSAGE_ENVELOPE_HEADROOM_BYTES
    )
    # A batch is the largest frame legitimate traffic produces, so the ceiling
    # must clear one full batch of maximum-size payloads. If this ever drops
    # below that, real pushes start disappearing on the wire.
    assert zmq_transport._message_uplink_max_bytes() > (
        int(plugin_settings.MESSAGE_PLANE_PAYLOAD_MAX_BYTES)
        * int(plugin_settings.PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE)
    )


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_oversized_authenticated_message_never_reaches_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A correctly-signed but oversized message frame is not delivered.

    ``push_message()`` rejects oversized payloads locally, but that check runs
    in plugin code: a plugin that writes an authenticated frame straight onto
    the message uplink skips it entirely. Only a bound on the socket keeps the
    host from receiving and decoding the frame.

    Note what is *not* asserted: no exception. libzmq enforces MAXMSGSIZE in
    the receiving engine, so the frame is dropped (and the peer disconnected)
    before ``recv()`` is reached -- the observable behaviour is silence.
    """
    import plugin.settings as plugin_settings

    # Shrink the derived ceiling so the test can overshoot it with a payload
    # measured in KB rather than the ~128MB the shipped defaults imply.
    monkeypatch.setattr(plugin_settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    monkeypatch.setattr(plugin_settings, "PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE", 2)
    ceiling = zmq_transport._message_uplink_max_bytes()

    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        message_uplink_endpoint=host.message_uplink_endpoint,
    )
    try:
        assert host._msg_sock.getsockopt(zmq.MAXMSGSIZE) == ceiling

        sender = child.channel_sender(zmq_transport.CH_MSG)

        # Liveness first: an in-bounds frame on the same socket does arrive, so
        # the silence asserted below is the ceiling and not a dead connection.
        sender.put_nowait({"type": "MESSAGE_PUSH", "message_id": "small"})
        assert await host.recv_message(timeout_ms=2000) == (
            zmq_transport.CH_MSG,
            {"type": "MESSAGE_PUSH", "message_id": "small"},
        )

        oversized = {
            "type": "MESSAGE_PUSH",
            "message_id": "oversized",
            "content": "x" * (ceiling * 2),
        }
        # Guard the guard: if a future headroom change made this payload fit
        # under the ceiling, the assertion below would pass for the wrong
        # reason.
        assert len(
            zmq_transport._encode_uplink(
                host.uplink_token,
                zmq_transport.CH_MSG,
                oversized,
            )
        ) > ceiling
        sender.put_nowait(oversized)

        assert await host.recv_message(timeout_ms=1500) is None
    finally:
        child.close()
        host.close()


@pytest.mark.plugin_unit
def test_control_uplink_ceiling_clears_the_export_push_it_was_derived_from() -> None:
    """The control uplink has its OWN ceiling now, not the message plane's.

    It used to borrow that one, and this test used to pin the equality. The
    borrowed number is ``payload_max * batch_max``, and the batch multiplier is
    definitionally wrong for a channel that is never batched: it handed every
    control frame about 128 MiB, which with this socket's 5,000-message
    high-water mark is not a bound. So the equality is gone on purpose.

    What survives is the property the old number was chosen for -- the ceiling
    still has to clear the widest control frame that can be measured, a CH_COMM
    export push carrying base64 of at most EXPORT_INLINE_BINARY_MAX_BYTES. The
    other half of the derivation, a CH_RES carrying tool images, has its own
    test beside this one.
    """
    import plugin.settings as plugin_settings

    widest_measurable_control_frame = (
        int(plugin_settings.EXPORT_INLINE_BINARY_MAX_BYTES) * 4 // 3
    )
    assert zmq_transport._control_uplink_max_bytes() > widest_measurable_control_frame


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_oversized_smuggled_batch_never_reaches_the_control_uplink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized frame aimed at the *control* endpoint is not delivered.

    The message uplink's ceiling is worth nothing if a plugin can point the
    same batch at the other socket: it holds both endpoints and the uplink
    token, so it can sign a frame and write it straight onto the control
    uplink, bypassing ``_uplink_socket``'s routing entirely. Refusing the
    channel after ``recv()`` would not help -- libzmq has already read the
    whole frame into memory by then -- so only a ceiling on this socket keeps
    the allocation from happening.

    As on the message plane, nothing raises: libzmq enforces MAXMSGSIZE in the
    receiving engine, drops the frame, and tears down the peer. The observable
    behaviour is silence.
    """
    import plugin.settings as plugin_settings

    # Shrink the derived ceiling so the test can overshoot it with KBs rather
    # than the ~128MB the shipped defaults imply.
    monkeypatch.setattr(plugin_settings, "MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 4096)
    monkeypatch.setattr(plugin_settings, "PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE", 2)
    ceiling = zmq_transport._control_uplink_max_bytes()

    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        message_uplink_endpoint=host.message_uplink_endpoint,
    )
    try:
        assert host._ul_sock.getsockopt(zmq.MAXMSGSIZE) == ceiling

        # Liveness first: an in-bounds control frame does arrive on this
        # socket, so the silence asserted below is the ceiling and not a dead
        # connection.
        child.channel_sender(zmq_transport.CH_STS).put_nowait({"type": "STATUS"})
        assert await host.recv(timeout_ms=2000) == (
            zmq_transport.CH_STS,
            {"type": "STATUS"},
        )

        smuggled = zmq_transport._encode_uplink(
            host.uplink_token,
            zmq_transport.CH_MSG_BATCH,
            {"items": [{"message_id": "smuggled", "content": "x" * (ceiling * 2)}]},
        )
        # Guard the guard: if a future headroom change made this frame fit
        # under the ceiling, the assertion below would pass for the wrong
        # reason.
        assert len(smuggled) > ceiling
        # Written straight at the control socket, the way a plugin holding the
        # endpoint would -- channel_sender() would route it to the message
        # socket instead.
        with child._ul_lock:
            child._ul_sock.send(smuggled)

        assert await host.recv(timeout_ms=1500) is None
    finally:
        child.close()
        host.close()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "channel",
    [
        pytest.param(zmq_transport.CH_MSG, id="single-message"),
        pytest.param(zmq_transport.CH_MSG_BATCH, id="batch"),
    ],
)
async def test_control_uplink_refuses_message_channels(channel: str) -> None:
    """The control uplink refuses message traffic even when it fits.

    The size ceiling bounds what one smuggled frame costs; this closes the
    route itself. Without it a plugin could keep pushing under-ceiling message
    frames at the control endpoint and have them routed as messages by
    ``_consume_uplink``'s compatibility branch, sidestepping the message
    plane's own accounting.
    """
    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        message_uplink_endpoint=host.message_uplink_endpoint,
    )
    try:
        payload = (
            {"items": [{"message_id": "smuggled"}]}
            if channel == zmq_transport.CH_MSG_BATCH
            else {"type": "MESSAGE_PUSH", "message_id": "smuggled"}
        )
        smuggled = zmq_transport._encode_uplink(
            host.uplink_token,
            channel,
            payload,
        )
        with child._ul_lock:
            child._ul_sock.send(smuggled)

        with pytest.raises(ValueError, match="invalid uplink channel"):
            await host.recv(timeout_ms=2000)

        # The same channel is still accepted on the socket that owns it, so
        # the refusal above is scoped to the control uplink and has not
        # disabled the message plane.
        child.channel_sender(zmq_transport.CH_MSG).put_nowait(
            {"type": "MESSAGE_PUSH", "message_id": "legitimate"}
        )
        assert await host.recv_message(timeout_ms=2000) == (
            zmq_transport.CH_MSG,
            {"type": "MESSAGE_PUSH", "message_id": "legitimate"},
        )
    finally:
        child.close()
        host.close()


@pytest.mark.plugin_unit
def test_uplink_decoder_still_accepts_both_planes_by_default() -> None:
    """The refusal lives on the socket, not in the decoder.

    ``ChildTransport`` falls back to sending message channels on the control
    socket when it is built without a dedicated message endpoint, and
    ``_consume_uplink`` still routes CH_MSG for host transports that expose no
    ``recv_message`` at all. Those pair only with each other -- a real
    ``HostTransport`` always binds both sockets and always advertises the
    message endpoint -- so refusing message channels on *this* host's control
    socket must not turn into a blanket refusal inside the decoder.
    """
    for channel in (zmq_transport.CH_MSG, zmq_transport.CH_MSG_BATCH):
        encoded = zmq_transport._encode_uplink(
            "host-channel-token",
            channel,
            {"type": "MESSAGE_PUSH"},
        )

        assert zmq_transport._decode_uplink(
            encoded,
            expected_token="host-channel-token",
        ) == (channel, {"type": "MESSAGE_PUSH"})


@pytest.mark.plugin_unit
def test_the_control_ceiling_covers_the_widest_legitimate_control_frame():
    """The control uplink gets its own number, and it has to be big enough.

    It used to borrow the message plane's ``payload_max * batch_max``. The
    batch multiplier is definitionally wrong for a channel that is never
    batched -- it handed every control frame about 128 MiB, which with this
    socket's 5,000-message high-water mark is not a bound.

    Tightening it is only safe while it still clears the widest frame real
    traffic produces, and today that is a CH_RES carrying tool images. Pinning
    the RELATIONSHIP rather than the literal: raise the tool image ceiling
    without raising this and the test goes red, instead of tool results being
    silently torn off the socket by libzmq.

    Mutation: point ``_control_uplink_max_bytes`` back at
    ``_message_uplink_max_bytes``, or drop the ceiling below the tool-result
    worst case.
    """
    from main_logic.tool_calling import (
        _MAX_TOOL_IMAGE_B64_BYTES,
        _MAX_TOOL_IMAGES,
    )

    from plugin.core.zmq_transport import (
        _control_uplink_max_bytes,
        _message_uplink_max_bytes,
    )

    # 量的是**打包后的整帧**，不是几个常量相加。第一版只对比了图片字节，
    # 而 output 和图片走同一帧：实测两张满尺寸图 + 60 KB 文本就超限，于是
    # 那一版把「每帧 128 MiB」换成了「合法结果被静默扯掉」。
    import ormsgpack

    from main_logic.tool_calling import _MAX_TOOL_IMAGE_VISION_PROMPT_CHARS
    from plugin.settings import MESSAGE_PLANE_PAYLOAD_MAX_BYTES

    ceiling = _control_uplink_max_bytes()
    worst_case_frame = len(ormsgpack.packb([
        "t" * 43,          # per-host token
        "res",             # CH_RES
        {
            "req_id": "r" * 36,
            "ok": True,
            "result": {
                "output": {"text": "z" * MESSAGE_PLANE_PAYLOAD_MAX_BYTES},
                "images": [
                    {
                        "data_b64": "A" * _MAX_TOOL_IMAGE_B64_BYTES,
                        "mime": "image/jpeg",
                        "vision_prompt": "p" * _MAX_TOOL_IMAGE_VISION_PROMPT_CHARS,
                    }
                    for _ in range(_MAX_TOOL_IMAGES)
                ],
            },
        },
    ]))

    assert ceiling >= worst_case_frame, (
        f"控制上行上限 {ceiling} 装不下一次合法的带图工具结果 "
        f"{worst_case_frame}——超限的帧会被 libzmq 在接收引擎里丢掉并断开对端，"
        "recv() 不报错，宿主连字节都看不到，调用方只知道「没收到」"
    )
    # 而且必须真的比消息上行紧：借用那个数正是这条意见指出的问题。
    assert ceiling < _message_uplink_max_bytes(), (
        "控制上行又借用了消息上行的界（含不适用的批量乘数）"
    )


# ── shutdown must not race the batcher onto its own socket ─────────────


@pytest.mark.plugin_unit
def test_stop_gives_up_draining_instead_of_leaving_the_worker_running() -> None:
    """``_run`` keeps flushing while the queue is non-empty, even after stop.

    The queue holds up to 100,000 items, so a loaded shutdown outlasts any
    join timeout — and the caller closes the socket that thread is sending on
    next. libzmq sockets are not thread safe, so that is undefined behaviour,
    not a lost batch. ``stop`` therefore escalates rather than returning while
    the worker is still going.

    Mutation: delete the ``_abandon`` set/re-join in ``stop``.
    """
    sent: list = []

    class _Transport:
        def send_uplink_nowait(self, channel, payload):
            sent.append(channel)

    class _NeverEmptyQueue:
        def get(self, timeout=None):
            return {"message_id": "endless"}

        def empty(self) -> bool:
            return False

    batcher = zmq_transport._AuthenticatedMessageBatcher(
        _Transport(),  # type: ignore[arg-type]
        batch_size=1,
        flush_interval_ms=10_000,
        max_queue=10,
        reject_ratio=0.9,
        enqueue_timeout_s=0,
    )
    batcher._queue = _NeverEmptyQueue()  # type: ignore[assignment]
    batcher.start()

    exited = batcher.stop(timeout=0.05)

    assert exited is True, "排空排不完，stop 却回来了——调用方接着就去关它的 socket"
    assert batcher._thread is None or not batcher._thread.is_alive()
    assert sent, "前提没成立：worker 根本没发过东西"


@pytest.mark.plugin_unit
def test_the_message_socket_is_closed_under_the_send_lock() -> None:
    """Holding the send lock is the proof that no send is in progress.

    ``send_uplink_nowait`` does ``with lock: sock.send(...)``, so closing while
    that lock is free cannot land inside a send. Asserting on the ORDER, not on
    the mere fact that the lock exists.

    Mutation: close ``_msg_sock`` without acquiring ``_msg_lock``.
    """
    events: list[str] = []

    class _Lock:
        def acquire(self, timeout=None) -> bool:
            events.append("acquire")
            return True

        def release(self) -> None:
            events.append("release")

    class _Sock:
        def close(self, linger=None) -> None:
            events.append("close")

    transport = zmq_transport.ChildTransport.__new__(zmq_transport.ChildTransport)
    transport._closed = False
    transport._close_lock = threading.Lock()
    transport._message_batcher = None
    transport._dl_sock = None
    transport._ul_sock = None
    transport._ul_lock = threading.Lock()
    transport._msg_sock = _Sock()
    transport._msg_lock = _Lock()
    transport._img_lock = threading.Lock()
    transport._ctx = None

    try:
        transport.close()
    except Exception:
        # Other teardown steps need a real context; the ordering above has
        # already been recorded by then.
        pass

    assert "close" in events, "根本没关这个 socket——context 终止会永久阻塞在它上面"
    assert events.index("acquire") < events.index("close"), (
        "先关后拿锁等于没拿：batcher 线程可能正卡在 send 里"
    )


# ── the uplink sends must both be bounded ──────────────────────────────


class _NeverWritableSock:
    """A socket that is never ready to send, i.e. sitting at its HWM."""

    def __init__(self) -> None:
        self.sends = 0

    def poll(self, timeout_ms, flags=None):
        return 0

    def send(self, data, flags=None):  # pragma: no cover - must not be reached
        self.sends += 1
        raise AssertionError("poll said not writable; send should not be attempted")


def _transport_with(sock, lock):
    t = zmq_transport.ChildTransport.__new__(zmq_transport.ChildTransport)
    t._uplink_token = "tok"
    t._ul_sock = sock
    t._msg_sock = sock
    t._ul_lock = lock
    t._msg_lock = lock
    # The senders' finally consults it: a shutdown that started while this
    # thread held the lock makes this thread the one that closes the socket.
    t._closed = False
    return t


@pytest.mark.plugin_unit
def test_a_saturated_uplink_times_out_instead_of_wedging_the_loop() -> None:
    """``timeout`` was accepted and never applied — the send blocked forever.

    That is the plugin event loop, and a bounded shutdown waits on it.

    Mutation: drop the ``poll`` guard and go back to a plain ``sock.send``.
    """
    sock = _NeverWritableSock()
    transport = _transport_with(sock, threading.Lock())

    started = time.monotonic()
    with pytest.raises(zmq_transport.queue.Full):
        transport.send_uplink(zmq_transport.CH_STS, {"x": 1}, timeout=0.2)
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, "没有在预算内返回"
    assert sock.sends == 0


@pytest.mark.plugin_unit
def test_the_nowait_send_does_not_wait_on_a_held_lock_forever() -> None:
    """It is called "nowait"; a bounded ``send_uplink`` still holds the lock.

    Mutation: go back to an unconditional ``with lock``.
    """
    lock = threading.Lock()
    transport = _transport_with(_NeverWritableSock(), lock)

    lock.acquire()
    try:
        started = time.monotonic()
        with pytest.raises(zmq_transport.queue.Full):
            transport.send_uplink_nowait(zmq_transport.CH_STS, {"x": 1})
        assert time.monotonic() - started < 5.0, "在锁上无限等——名字是假的"
    finally:
        lock.release()


@pytest.mark.plugin_unit
def test_a_held_lock_defers_the_close_to_the_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not closing is the correct fallback, not closing anyway.

    ``ctx.term()`` interrupts the lock holder's poll/send with ETERM, its
    ``finally`` sees ``_closed`` and closes the socket, and term returns — the
    measured behaviour the media socket already relies on. Closing it from here
    instead would be undefined behaviour while that send is in flight.

    Mutation: close the socket when the lock was not acquired.
    """
    closed: list[str] = []

    class _Sock:
        def close(self, linger=None) -> None:
            closed.append("msg")

    class _HeldLock:
        def acquire(self, timeout=None) -> bool:
            return False

        def release(self) -> None:  # pragma: no cover - never taken
            raise AssertionError("released a lock that was never acquired")

    transport = zmq_transport.ChildTransport.__new__(zmq_transport.ChildTransport)
    transport._closed = False
    transport._message_batcher = None
    transport._dl_sock = None
    transport._ul_sock = None
    transport._ul_lock = threading.Lock()
    transport._msg_sock = _Sock()
    transport._msg_lock = _HeldLock()
    transport._img_lock = threading.Lock()

    try:
        transport.close()
    except Exception:
        pass

    assert closed == [], (
        "锁还被别人拿着就把 socket 关了——对面可能正在 send 里，这是 UB"
    )

    # ...and the sender, once interrupted, is the one that closes it.
    transport._msg_lock = threading.Lock()
    transport._uplink_token = "tok"
    with pytest.raises(Exception):
        transport.send_uplink_nowait(zmq_transport.CH_MSG, {"x": 1})
    assert closed == ["msg"], "被打断的发送方没有在自己的 finally 里把 socket 关掉"


@pytest.mark.plugin_unit
def test_an_oversized_control_frame_is_refused_at_the_sender() -> None:
    """Silence here is not a dropped frame — it is a dropped connection.

    libzmq enforces MAXMSGSIZE in the receiver's engine and closes the
    offending peer, so one valid-but-large tool result would tear down the
    control uplink with nothing on either side saying why. The ceiling is a
    transport fact and the SDK puts no limit on tool output, so the sender's
    job is to say so, not to pretend it fits.

    Mutation: delete the ``_refuse_oversized_uplink_frame`` call in
    ``send_uplink``.
    """
    sock = _NeverWritableSock()
    transport = _transport_with(sock, threading.Lock())
    cap = zmq_transport._control_uplink_max_bytes()

    with pytest.raises(ValueError) as excinfo:
        transport.send_uplink(zmq_transport.CH_RES, {"output": "x" * (cap + 4096)})

    message = str(excinfo.value)
    assert str(cap) in message, "错误信息里没有上限，看到日志的人无从判断"
    assert sock.sends == 0


@pytest.mark.plugin_unit
def test_the_message_channel_keeps_its_silent_drop() -> None:
    """The refusal is deliberately control-only.

    The message path validates per item upstream and has a test pinning that an
    oversized frame is dropped silently *without* killing the connection.
    Extending the refusal there changed that behaviour and broke it — so the
    asymmetry is the point, not an oversight.
    """
    big = b"x" * (zmq_transport._message_uplink_max_bytes() + 4096)

    zmq_transport._refuse_oversized_uplink_frame(zmq_transport.CH_MSG, big)
    zmq_transport._refuse_oversized_uplink_frame(zmq_transport.CH_MSG_BATCH, big)


@pytest.mark.plugin_unit
@pytest.mark.parametrize("force_abandon", [False, True])
def test_a_restarted_batcher_actually_sends_again(force_abandon: bool) -> None:
    """``start()`` is a restart entry — its own ``is_alive`` guard says so.

    Both shutdown flags have to be lowered there, and both are exercised:
    an ordinary stop sets only ``_stop``, while a stop whose join times out
    also sets ``_abandon``. Covering just the first leaves "forgot to clear
    ``_abandon``" alive — which is what the first version of this test did.

    A new thread that walks into either flag leaves ``_run`` immediately and
    sends nothing, silently: this worker is fire-and-forget, so nothing raises
    and nothing looks missing until someone notices messages stopped arriving.

    Mutation: delete either ``clear()`` in ``start``.
    """
    sent: list = []

    class _Transport:
        def send_uplink_nowait(self, channel, payload):
            sent.append(payload)

    class _NeverEmptyQueue:
        def get(self, timeout=None):
            return {"message_id": "endless"}

        def empty(self) -> bool:
            return False

    batcher = zmq_transport._AuthenticatedMessageBatcher(
        _Transport(),  # type: ignore[arg-type]
        batch_size=1,
        flush_interval_ms=5,
        max_queue=8,
        reject_ratio=0.9,
        enqueue_timeout_s=0.5,
    )
    real_queue = batcher._queue
    if force_abandon:
        # A queue that never drains makes the join time out, so ``stop`` has to
        # escalate and set ``_abandon``.
        batcher._queue = _NeverEmptyQueue()  # type: ignore[assignment]

    batcher.start()
    if not force_abandon:
        batcher.enqueue({"message_id": "first"})
    for _ in range(200):
        if sent:
            break
        time.sleep(0.01)
    assert sent, "前提没成立：第一条就没发出去"

    batcher.stop(timeout=0.05 if force_abandon else 1.0)
    if force_abandon:
        assert batcher._abandon.is_set(), "前提没成立：这一路没走到升级"
        batcher._queue = real_queue  # type: ignore[assignment]

    sent.clear()
    batcher.start()
    try:
        batcher.enqueue({"message_id": "second"})
        for _ in range(200):
            if sent:
                break
            time.sleep(0.01)
        assert sent, "重启之后一条都没发——关停位没在 start 里落下"
    finally:
        batcher.stop(timeout=1.0)


# ── pack and unpack must agree on their options ────────────────────────


def _assert_same_including_key_types(got, expected, path: str = "data") -> None:
    """Compare dicts by key TYPE as well as value.

    ``==`` alone is too weak for this contract: Python hashes ``True`` and ``1``
    to the same slot and compares them equal, and ``2024 == 2024.0``, so
    ``{True: 1} == {1: 1}`` and ``{2024: 3} == {2024.0: 3}`` are both True. A
    decoder that quietly widened bool keys to int, or int keys to float, would
    slip past a plain equality assertion — and preserving the key type is
    precisely what OPT_NON_STR_KEYS is for (CodeRabbit).
    """
    assert type(got) is type(expected), f"{path}: {type(got)} != {type(expected)}"
    if isinstance(expected, dict):
        got_keys = sorted(got.keys(), key=repr)
        exp_keys = sorted(expected.keys(), key=repr)
        assert [(type(k), k) for k in got_keys] == [(type(k), k) for k in exp_keys], (
            f"{path}: 键的类型或取值变了 {[(type(k).__name__, k) for k in got_keys]} "
            f"!= {[(type(k).__name__, k) for k in exp_keys]}"
        )
        for k in exp_keys:
            match = next(kk for kk in got_keys if type(kk) is type(k) and kk == k)
            _assert_same_including_key_types(got[match], expected[k], f"{path}[{k!r}]")
    elif isinstance(expected, list):
        assert len(got) == len(expected), f"{path}: 长度变了"
        for i, (g, e) in enumerate(zip(got, expected)):
            _assert_same_including_key_types(g, e, f"{path}[{i}]")
    else:
        assert got == expected, f"{path}: {got!r} != {expected!r}"


@pytest.mark.plugin_unit
@pytest.mark.parametrize(
    "value",
    [
        {"by_year": {2024: 3}},          # int keys — a group-by result
        {"flags": {True: "yes"}},        # bool keys
        {"buckets": {1.5: "a"}},         # float keys
        {"nested": {"inner": {7: [1]}}}, # nested, one level down
    ],
)
def test_non_string_keys_survive_the_uplink(value: dict) -> None:
    """``packb`` carries OPT_NON_STR_KEYS; ``unpackb`` must carry it too.

    ormsgpack encodes non-string-keyed maps with an extension type, and reading
    one back without the flag raises ``ValueError: invalid type U16``. The
    consequence is not a visible error: ``_decode_uplink`` re-raises,
    ``_consume_uplink`` logs and continues, the CH_RES never reaches
    ``_dispatch_result``, and the caller waits out PLUGIN_TRIGGER_TIMEOUT for a
    result that was already computed. On origin/main this path was ``pickle``,
    which had no such restriction.

    Mutation: drop ``option=_UPLINK_UNPACK_OPTIONS`` from ``unpackb``.
    """
    payload = {"req_id": "r1", "success": True, "data": value}

    raw = zmq_transport._encode_uplink("tok", zmq_transport.CH_RES, payload)
    channel, decoded = zmq_transport._decode_uplink(raw, expected_token="tok")

    assert channel == zmq_transport.CH_RES
    _assert_same_including_key_types(decoded["data"], value)


@pytest.mark.plugin_unit
def test_the_unpack_options_are_a_subset_of_the_pack_options() -> None:
    """Pins the relationship, so a flag added to one side cannot drift.

    Only the flags that mean something at read time belong here — the other
    three are serialization-only — but every unpack flag must exist on the pack
    side, or the decoder would be asked to read something nobody writes.
    """
    pack = zmq_transport._UPLINK_PACK_OPTIONS
    unpack = zmq_transport._UPLINK_UNPACK_OPTIONS

    assert unpack & pack == unpack, "解包开了一个打包侧没有的 option"
    assert unpack & ormsgpack.OPT_NON_STR_KEYS, (
        "解包丢了 OPT_NON_STR_KEYS——非字符串键的返回值会变成静默超时"
    )


@pytest.mark.plugin_unit
@pytest.mark.parametrize(("configured", "expected"), [("0", 1), ("-5", 1), ("256", 256)])
def test_the_batch_size_setting_is_clamped_where_it_is_loaded(
    configured: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both sides of this constant must read the same number.

    ``_AuthenticatedMessageBatcher`` clamps to ``max(1, ...)`` in its
    constructor; the host's received-batch check in ``communication.py``
    compares against the setting directly. Configured as 0 or negative those
    disagree: the child sends a legal one-item batch and the host rejects every
    one of them, so proactive messages stop being routed with nothing raised.

    Clamping at load is what makes the two agree by construction rather than by
    both remembering to do it.

    Mutation: drop the ``max(1, ...)`` in plugin/settings.py.
    """
    import importlib

    import plugin.settings as settings_mod

    monkeypatch.setenv("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE", configured)
    reloaded = importlib.reload(settings_mod)
    try:
        assert reloaded.PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE == expected

        # 读真实批处理器算出来的值，而不是在测试里把 max(1, ...) 再抄一遍：
        # 抄一遍的话，实现里的钳位公式一改，测试的期望跟着一起变，这条断言
        # 就永远成立（CodeRabbit）。
        batcher = zmq_transport._AuthenticatedMessageBatcher(
            object(),  # type: ignore[arg-type]
            batch_size=int(configured),
            flush_interval_ms=5,
            max_queue=8,
            reject_ratio=0.9,
            enqueue_timeout_s=0,
        )
        assert batcher._batch_size <= reloaded.PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE, (
            f"子进程会发出宿主必然拒收的批量大小："
            f"{batcher._batch_size} > {reloaded.PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE}"
        )
    finally:
        monkeypatch.delenv("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE", raising=False)
        importlib.reload(settings_mod)


@pytest.mark.plugin_unit
@pytest.mark.parametrize(("configured", "expected"), [("0", 1), ("-1", 1), ("100000", 100000)])
def test_the_batcher_queue_limit_is_clamped_where_it_is_loaded(
    configured: str, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``queue.Queue(maxsize=0)`` is UNBOUNDED in Python, not "refuse everything".

    Configured as 0 or negative, the batcher would take an unbounded queue —
    and ``enqueue``'s high-water rejection is skipped by its own
    ``self._max_queue > 0`` condition, so both bounds come off at once and the
    backlog is limited only by memory.

    Mutation: drop the ``max(1, ...)`` in plugin/settings.py.
    """
    import importlib

    import plugin.settings as settings_mod

    monkeypatch.setenv("NEKO_MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE", configured)
    reloaded = importlib.reload(settings_mod)
    try:
        assert reloaded.MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE == expected

        batcher = zmq_transport._AuthenticatedMessageBatcher(
            object(),  # type: ignore[arg-type]
            batch_size=8,
            flush_interval_ms=5,
            max_queue=reloaded.MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE,
            reject_ratio=0.9,
            enqueue_timeout_s=0,
        )
        assert batcher._queue.maxsize > 0, "队列是无界的——两道闸同时失效"
    finally:
        monkeypatch.delenv("NEKO_MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE", raising=False)
        importlib.reload(settings_mod)
