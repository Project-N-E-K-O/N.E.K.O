from __future__ import annotations

import ormsgpack
import pytest

from plugin.message_plane.ingest_server import _loads


@pytest.mark.plugin_unit
def test_ingest_decoder_rejects_payload_without_host_credential() -> None:
    raw = ormsgpack.packb(
        {
            "v": 1,
            "kind": "delta_batch",
            "from": "victim-plugin",
            "items": [],
        }
    )

    with pytest.raises(ValueError, match="credential"):
        _loads(raw, expected_token="host-secret")


@pytest.mark.plugin_unit
def test_ingest_decoder_accepts_and_removes_host_credential() -> None:
    raw = ormsgpack.packb(
        {
            "v": 1,
            "kind": "delta_batch",
            "from": "control_plane",
            "_auth": "host-secret",
            "items": [],
        }
    )

    assert _loads(raw, expected_token="host-secret") == {
        "v": 1,
        "kind": "delta_batch",
        "from": "control_plane",
        "items": [],
    }


@pytest.mark.plugin_unit
def test_bridge_signature_is_accepted_by_the_plane_it_starts() -> None:
    """The two ends must share ONE credential, not merely each have one.

    Both sides having "a token" is not the property that matters, and a
    mismatch is silent: the ingest server only bumps its dropped counter, so
    every record would vanish with a green log. The decoder tests above use a
    literal secret on both sides and therefore cannot see that. This one wires
    the real producer to the real consumer.
    """
    from plugin.server.messaging.plane_bridge import _dumps, ingest_auth_token

    record = {
        "v": 1,
        "kind": "delta_batch",
        "from": "control_plane",
        "items": [],
    }

    assert _loads(_dumps(record), expected_token=ingest_auth_token()) == record


@pytest.mark.plugin_unit
def test_plane_runner_refuses_to_start_without_a_credential() -> None:
    """An unauthenticated plane accepts writes from any local process."""
    from plugin.message_plane.runner import (
        MessagePlaneEndpoints,
        PythonMessagePlaneRunner,
    )

    endpoints = MessagePlaneEndpoints(
        rpc="tcp://127.0.0.1:1",
        pub="tcp://127.0.0.1:2",
        ingest="tcp://127.0.0.1:3",
    )

    with pytest.raises(ValueError, match="auth token"):
        PythonMessagePlaneRunner(endpoints=endpoints, auth_token="")
