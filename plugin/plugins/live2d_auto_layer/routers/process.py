from __future__ import annotations

import asyncio

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui
from plugin.sdk.shared.core.router import PluginRouter

from ..core.constants import DEFAULT_PARTS

_METHODS = {"anime_face", "grounded_sam", "color"}


class ProcessRouter(PluginRouter):
    """Layer extraction and session management entries."""

    def __init__(self):
        super().__init__(name="process")

    @ui.action(
        label=tr("actions.splitImage.label", default="Split image"),
        tone="success",
        group="process",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="live2d_split_image",
        name=tr("entries.splitImage.name", default="Split image into Live2D layers"),
        description=tr(
            "entries.splitImage.description",
            default="Run the Live2D Auto Layer pipeline on a local image path and export PNG layers, preview, zip, and manifest.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "Local image path"},
                "session_id": {"type": "string", "description": "Optional output session id"},
                "method": {
                    "type": "string",
                    "enum": ["anime_face", "grounded_sam", "color"],
                    "default": "anime_face",
                },
                "parts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Layer parts to extract for anime_face mode",
                },
                "feather_radius": {"type": "integer", "default": 2},
                "gpt_api_key": {"type": "string", "description": "Optional GPT Vision API key"},
            },
            "required": ["input_path"],
        },
        timeout=900,
        llm_result_fields=["message", "manifest_path", "preview_path", "zip_path", "warnings"],
    )
    async def split_image(
        self,
        input_path: str = "",
        session_id: str = "",
        method: str = "anime_face",
        parts: list[str] | None = None,
        feather_radius: int = 2,
        gpt_api_key: str = "",
        **_,
    ):
        if not str(input_path or "").strip():
            return Err(SdkError("input_path is required"))
        if method not in _METHODS:
            return Err(SdkError(f"method must be one of: {', '.join(sorted(_METHODS))}"))
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.split_image,
                input_path=input_path,
                session_id=session_id.strip() or None,
                method=method,  # type: ignore[arg-type]
                parts=_clean_parts(parts),
                feather_radius=int(feather_radius),
                gpt_api_key=str(gpt_api_key or ""),
            )
        except Exception as exc:
            self.logger.warning("live2d_split_image failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(result.to_dict())

    @ui.action(
        label=tr("actions.listSessions.label", default="List sessions"),
        tone="default",
        group="sessions",
        order=10,
        refresh_context=True,
    )
    @plugin_entry(
        id="live2d_list_sessions",
        name=tr("entries.listSessions.name", default="List Live2D layer sessions"),
        description=tr("entries.listSessions.description", default="List exported Live2D Auto Layer sessions."),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["count", "sessions"],
    )
    async def list_sessions(self, **_):
        sessions = self.main_plugin.layers.list_sessions()
        return Ok({"count": len(sessions), "sessions": sessions})

    @ui.action(
        label=tr("actions.getSession.label", default="Get session"),
        tone="default",
        group="sessions",
        order=20,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_get_session",
        name=tr("entries.getSession.name", default="Get Live2D layer session"),
        description=tr("entries.getSession.description", default="Read a Live2D Auto Layer session manifest by session id."),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    )
    async def get_session(self, session_id: str = "", **_):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        try:
            result = self.main_plugin.layers.get_session(clean_id)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        if result is None:
            return Err(SdkError(f"session not found: {clean_id}"))
        return Ok(result.to_dict())

    @ui.action(
        label=tr("actions.deleteSession.label", default="Delete session"),
        tone="danger",
        group="sessions",
        order=30,
        confirm=tr("actions.deleteSession.confirm", default="Delete this Live2D layer session?"),
        refresh_context=True,
    )
    @plugin_entry(
        id="live2d_delete_session",
        name=tr("entries.deleteSession.name", default="Delete Live2D layer session"),
        description=tr("entries.deleteSession.description", default="Delete a Live2D Auto Layer output session by session id."),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
    )
    async def delete_session(self, session_id: str = "", **_):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        try:
            deleted = self.main_plugin.layers.delete_session(clean_id)
        except Exception as exc:
            return Err(SdkError(str(exc)))
        return Ok({"deleted": deleted, "session_id": clean_id})


def _clean_parts(parts: list[str] | None) -> list[str]:
    if not isinstance(parts, list) or not parts:
        return list(DEFAULT_PARTS)
    cleaned = [str(part).strip() for part in parts if str(part).strip()]
    return cleaned or list(DEFAULT_PARTS)
