from __future__ import annotations

import pytest

from plugin.server.domain.errors import ServerDomainError
from plugin.server.messaging.handlers.common import domain_error_payload, resolve_common_fields


@pytest.mark.plugin_unit
def test_resolve_common_fields_validation() -> None:
    assert resolve_common_fields({}) is None
    assert resolve_common_fields({"from_plugin": " ", "request_id": "r1"}) is None
    assert resolve_common_fields({"from_plugin": "p1", "request_id": " "}) is None

    got = resolve_common_fields({"from_plugin": " p1 ", "request_id": " r1 ", "timeout": 1.5})
    assert got == ("p1", "r1", 1.5)


@pytest.mark.plugin_unit
def test_domain_error_payload_with_and_without_details() -> None:
    e1 = ServerDomainError(code="E1", message="m1", status_code=400, details={})
    assert domain_error_payload(e1) == {"code": "E1", "message": "m1"}

    e2 = ServerDomainError(code="E2", message="m2", status_code=500, details={"x": 1})
    assert domain_error_payload(e2) == {"code": "E2", "message": "m2", "details": {"x": 1}}

