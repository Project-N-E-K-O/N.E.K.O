from __future__ import annotations

import pickle
import threading
from pathlib import Path

import pytest
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
