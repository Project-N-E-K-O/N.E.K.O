from __future__ import annotations

from typing import Any


def build_open_ui_payload(*, plugin_id: str, available: bool) -> dict[str, Any]:
    path = f"/plugin/{plugin_id}/ui/" if available else ""
    message = "UI 已注册" if available else "UI 未注册"
    return {
        "available": available,
        "path": path,
        "message": message,
    }
