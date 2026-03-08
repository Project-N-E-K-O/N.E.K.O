from __future__ import annotations

from plugin.sdk_v2 import shared


def test_shared_package_exposes_implementation_status() -> None:
    assert shared.IMPLEMENTATION_STATUS["storage"] == "implemented"
    assert shared.IMPLEMENTATION_STATUS["bus"] == "contract-only"
    assert shared.IMPLEMENTATION_STATUS["runtime"] == "mixed"
    assert shared.IMPLEMENTATION_STATUS["core"] == "mixed"
