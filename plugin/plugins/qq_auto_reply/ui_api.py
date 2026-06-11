from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["qq-auto-reply-ui-api"])


def _ensure_plugin(plugin_id: str) -> None:
    if plugin_id != "qq_auto_reply":
        raise HTTPException(status_code=404, detail=f"Plugin '{plugin_id}' has no QQ UI API")
