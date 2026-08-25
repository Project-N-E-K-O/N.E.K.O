from __future__ import annotations

import pickle

import pytest

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
