from __future__ import annotations

from plugin.sdk_v2 import shared


def test_shared_package_exposes_implementation_status() -> None:
    assert shared.IMPLEMENTATION_STATUS["storage"] == "facade"
    assert shared.IMPLEMENTATION_STATUS["bus"] == "contract-only"
    assert shared.IMPLEMENTATION_STATUS["runtime"] == "facade"
    assert shared.IMPLEMENTATION_STATUS["core"] == "mixed"
