from __future__ import annotations

import pytest

from plugin.server.messaging.handlers.common import resolve_common_fields


@pytest.mark.plugin_unit
def test_resolve_common_fields_validation() -> None:
    assert resolve_common_fields({}) is None
    assert resolve_common_fields({"from_plugin": " ", "request_id": "r1"}) is None
    assert resolve_common_fields({"from_plugin": "p1", "request_id": " "}) is None

    got = resolve_common_fields({"from_plugin": " p1 ", "request_id": " r1 ", "timeout": 1.5})
    assert got == ("p1", "r1", 1.5)
