from __future__ import annotations

import asyncio
import hashlib
import pickle
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
