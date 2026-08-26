from __future__ import annotations

import asyncio
import hashlib
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
        "type": "LIVE_FRAME_PERMISSION_SET",
        "from_plugin": "demo-plugin",
        "request_id": "request-1",
        "enabled": True,
        "token": "generation-one",
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
            "type": "LIVE_FRAME_PERMISSION_SET",
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
def test_host_and_child_share_a_non_secret_permission_generation() -> None:
    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        downlink_curve=host.downlink_curve_credentials,
    )
    try:
        expected = hashlib.sha256(host.uplink_token.encode("utf-8")).hexdigest()
        assert host.permission_generation == expected
        assert child.permission_generation == expected
        assert host.permission_generation != host.uplink_token
    finally:
        child.close()
        host.close()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_message_uplink_is_physically_isolated_from_control_results() -> None:
    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        downlink_curve=host.downlink_curve_credentials,
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
        downlink_curve=host.downlink_curve_credentials,
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
        downlink_curve=host.downlink_curve_credentials,
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
@pytest.mark.asyncio
async def test_downlink_curve_rejects_an_unauthenticated_competing_receiver() -> None:
    host = zmq_transport.HostTransport()
    child = zmq_transport.ChildTransport(
        host.downlink_endpoint,
        host.uplink_endpoint,
        host.uplink_token,
        downlink_curve=host.downlink_curve_credentials,
    )
    attacker_context = zmq.Context()
    attacker = attacker_context.socket(zmq.PULL)
    attacker.linger = 0
    attacker.connect(host.downlink_endpoint)
    try:
        await asyncio.sleep(0.1)
        await host.send_command({"type": "SECRET", "config": "private"})

        assert await child.recv_downlink(timeout_ms=1000) == (
            zmq_transport.CH_CMD,
            {"type": "SECRET", "config": "private"},
        )
        assert attacker.poll(timeout=100) == 0
    finally:
        attacker.close(linger=0)
        attacker_context.term()
        child.close()
        host.close()


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
