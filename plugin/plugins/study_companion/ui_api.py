from __future__ import annotations

from typing import Any


def build_open_ui_payload(*, plugin_id: str, available: bool) -> dict[str, Any]:
    path = f"/plugin/{plugin_id}/ui/" if available else ""
    message_key = "ui.open.available" if available else "ui.open.unavailable"
    return {
        "available": available,
        "path": path,
        "message_key": message_key,
    }
