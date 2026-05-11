from __future__ import annotations

from typing import Any


def build_open_ui_payload(*, plugin_id: str, available: bool) -> dict[str, Any]:
    path = f"/plugin/{plugin_id}/ui/" if available else ""
    return {
        "available": bool(available),
        "path": path,
        "message": "UI registered" if available else "UI is not registered",
    }
