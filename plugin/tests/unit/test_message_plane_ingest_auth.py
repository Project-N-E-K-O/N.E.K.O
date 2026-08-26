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
