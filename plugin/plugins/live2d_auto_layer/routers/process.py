from __future__ import annotations

import asyncio

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui
from plugin.sdk.shared.core.router import PluginRouter

from ..core.constants import AVAILABLE_PARTS, DEFAULT_PARTS

_METHODS = {"anime_face", "grounded_sam", "color"}
_PARTS = set(AVAILABLE_PARTS)


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
                "input_data_url": {"type": "string", "description": "Optional browser-uploaded image data URL"},
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
        input_data_url: str = "",
        session_id: str = "",
        method: str = "anime_face",
        parts: list[str] | None = None,
        feather_radius: int = 2,
        gpt_api_key: str = "",
        **_,
    ):
        if not str(input_path or "").strip() and not str(input_data_url or "").strip():
            return Err(SdkError("input_path or input_data_url is required"))
        if method not in _METHODS:
            return Err(SdkError(f"method must be one of: {', '.join(sorted(_METHODS))}"))
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.split_image,
                input_path=input_path or None,
                input_data_url=input_data_url,
                session_id=session_id.strip() or None,
                method=method,  # type: ignore[arg-type]
                parts=_clean_parts(parts),
                feather_radius=int(feather_radius),
                gpt_api_key=str(gpt_api_key or ""),
            )
        except Exception as exc:
            self.logger.warning("live2d_split_image failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(self.main_plugin.layers.result_to_ui_dict(result))

    @ui.action(
        label=tr("actions.resegmentSession.label", default="Resegment"),
        tone="primary",
        group="process",
        order=20,
        refresh_context=True,
    )
    @plugin_entry(
        id="live2d_resegment_session",
        name=tr("entries.resegmentSession.name", default="Resegment Live2D layer session"),
        description=tr("entries.resegmentSession.description", default="Rebuild foreground from an exported session and rerun segmentation with new options."),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "method": {
                    "type": "string",
                    "enum": ["anime_face", "grounded_sam", "color"],
                    "default": "anime_face",
                },
                "parts": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "feather_radius": {"type": "integer", "default": 2},
                "gpt_api_key": {"type": "string"},
            },
            "required": ["session_id"],
        },
        timeout=900,
        llm_result_fields=["message", "manifest_path", "preview_path", "zip_path", "warnings"],
    )
    async def resegment_session(
        self,
        session_id: str = "",
        method: str = "anime_face",
        parts: list[str] | None = None,
        feather_radius: int = 2,
        gpt_api_key: str = "",
        **_,
    ):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        if method not in _METHODS:
            return Err(SdkError(f"method must be one of: {', '.join(sorted(_METHODS))}"))
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.resegment_session,
                clean_id,
                method=method,  # type: ignore[arg-type]
                parts=_clean_parts(parts),
                feather_radius=int(feather_radius),
                gpt_api_key=str(gpt_api_key or ""),
            )
        except Exception as exc:
            self.logger.warning("live2d_resegment_session failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(self.main_plugin.layers.result_to_ui_dict(result))

    @ui.action(
        label=tr("actions.importLayerSource.label", default="Import layers"),
        tone="success",
        group="process",
        order=25,
        refresh_context=True,
    )
    @plugin_entry(
        id="live2d_import_layer_source",
        name=tr("entries.importLayerSource.name", default="Import external layer source"),
        description=tr(
            "entries.importLayerSource.description",
            default="Import external see-through output layers from a local directory or PSD and export Live2D-ready PNG layers, preview, zip, and manifest.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "layer_source_path": {
                    "type": "string",
                    "description": "Local see-through output directory or PSD path",
                },
                "session_id": {"type": "string", "description": "Optional output session id"},
                "source": {
                    "type": "string",
                    "default": "see_through",
                    "description": "External layer source label",
                },
            },
            "required": ["layer_source_path"],
        },
        timeout=300,
        llm_result_fields=["message", "manifest_path", "preview_path", "zip_path", "warnings"],
    )
    async def import_layer_source(
        self,
        layer_source_path: str = "",
        session_id: str = "",
        source: str = "see_through",
        **_,
    ):
        clean_path = str(layer_source_path or "").strip()
        if not clean_path:
            return Err(SdkError("layer_source_path is required"))
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.import_layer_source,
                clean_path,
                session_id=session_id.strip() or None,
                source=str(source or "see_through").strip() or "see_through",
            )
        except Exception as exc:
            self.logger.warning("live2d_import_layer_source failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(self.main_plugin.layers.result_to_ui_dict(result))

    @ui.action(
        label=tr("actions.exportSession.label", default="Export ZIP"),
        tone="primary",
        group="process",
        order=30,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_export_session",
        name=tr("entries.exportSession.name", default="Export Live2D layer session"),
        description=tr("entries.exportSession.description", default="Return ZIP artifact metadata for a Live2D Auto Layer session."),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
        llm_result_fields=["session_id", "zip_path"],
    )
    async def export_session(self, session_id: str = "", **_):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        result = self.main_plugin.layers.get_session(clean_id)
        if result is None:
            return Err(SdkError(f"session not found: {clean_id}"))
        return Ok({
            "session_id": clean_id,
            "zip_path": result.zip_path,
            "filename": "live2d_layers.zip",
        })

    @ui.action(
        label=tr("actions.exportAutoRigModel.label", default="Export AutoRig model"),
        tone="success",
        group="process",
        order=36,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_export_auto_rig_model",
        name=tr("entries.exportAutoRigModel.name", default="Export NEKO auto-rig model package"),
        description=tr(
            "entries.exportAutoRigModel.description",
            default="Generate a fully automatic NEKO auto-rig ZIP with model JSON, layer textures, generated meshes, parameters, and bindings. This is not a Cubism model3/moc3 package.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "mesh_alpha_threshold": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 255,
                    "default": 10,
                    "description": "Alpha threshold used only for AutoRig mesh/bbox generation. Textures keep their original alpha.",
                },
            },
            "required": ["session_id"],
        },
        llm_result_fields=["session_id", "auto_rig_zip_path", "auto_rig_model_path", "mesh_alpha_threshold", "quality_summary", "message", "warning"],
    )
    async def export_auto_rig_model(
        self,
        session_id: str = "",
        mesh_alpha_threshold: int = 10,
        **_,
    ):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        threshold = _clean_alpha_threshold(mesh_alpha_threshold)
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.export_auto_rig_model,
                clean_id,
                mesh_alpha_threshold=threshold,
            )
        except Exception as exc:
            self.logger.warning("live2d_export_auto_rig_model failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(result)

    @ui.action(
        label=tr("actions.exportPNGTuberModel.label", default="Export PNGTuber model"),
        tone="success",
        group="process",
        order=38,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_export_pngtuber_model",
        name=tr("entries.exportPNGTuberModel.name", default="Export NEKO PNGTuber model package"),
        description=tr(
            "entries.exportPNGTuberModel.description",
            default="Generate an importable NEKO PNGTuber ZIP with model.json, layered_canvas_v1 metadata, ordered PNG layers, and fallback composite images. This is not a Cubism model3/moc3 package.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "model_name": {
                    "type": "string",
                    "description": "Optional display name written to model.json.",
                },
                "enable_basic_blink": {
                    "type": "boolean",
                    "default": False,
                    "description": "When enabled, eye layers are hidden during blink. Disabled by default because generated sessions usually do not include closed-eye replacement layers.",
                },
            },
            "required": ["session_id"],
        },
        llm_result_fields=["session_id", "pngtuber_zip_path", "pngtuber_model_path", "pngtuber_metadata_path", "message", "warning"],
    )
    async def export_pngtuber_model(
        self,
        session_id: str = "",
        model_name: str = "",
        enable_basic_blink: bool = False,
        **_,
    ):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.export_pngtuber_model,
                clean_id,
                model_name=str(model_name or ""),
                enable_basic_blink=bool(enable_basic_blink),
            )
        except Exception as exc:
            self.logger.warning("live2d_export_pngtuber_model failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(result)

    @ui.action(
        label=tr("actions.installPNGTuberModel.label", default="Install PNGTuber model"),
        tone="success",
        group="process",
        order=39,
        refresh_context=True,
    )
    @plugin_entry(
        id="live2d_install_pngtuber_model",
        name=tr("entries.installPNGTuberModel.name", default="Install NEKO PNGTuber model package"),
        description=tr(
            "entries.installPNGTuberModel.description",
            default="Export a session as NEKO PNGTuber layered_canvas_v1 and install it into the user PNGTuber model library.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "model_name": {"type": "string"},
                "preferred_folder": {"type": "string"},
                "enable_basic_blink": {"type": "boolean", "default": False},
            },
            "required": ["session_id"],
        },
        timeout=120,
        llm_result_fields=["success", "session_id", "folder", "url", "model_name", "source_format", "message"],
    )
    async def install_pngtuber_model(
        self,
        session_id: str = "",
        model_name: str = "",
        preferred_folder: str = "",
        enable_basic_blink: bool = False,
        **_,
    ):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.install_pngtuber_model,
                clean_id,
                model_name=str(model_name or ""),
                preferred_folder=str(preferred_folder or ""),
                enable_basic_blink=bool(enable_basic_blink),
            )
        except Exception as exc:
            self.logger.warning("live2d_install_pngtuber_model failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(result)

    @ui.action(
        label=tr("actions.loadAutoRigModel.label", default="Load AutoRig model"),
        tone="primary",
        group="process",
        order=37,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_load_auto_rig_model",
        name=tr("entries.loadAutoRigModel.name", default="Load NEKO auto-rig model"),
        description=tr(
            "entries.loadAutoRigModel.description",
            default="Load and validate the generated NEKO AutoRig model for a session, including texture artifact paths and quality summary.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
            },
            "required": ["session_id"],
        },
        llm_result_fields=["session_id", "format", "canvas_size", "quality_summary"],
    )
    async def load_auto_rig_model(self, session_id: str = "", **_):
        clean_id = str(session_id or "").strip()
        if not clean_id:
            return Err(SdkError("session_id is required"))
        try:
            result = await asyncio.to_thread(
                self.main_plugin.layers.load_auto_rig_model,
                clean_id,
            )
        except Exception as exc:
            self.logger.warning("live2d_load_auto_rig_model failed: {}", exc, exc_info=True)
            return Err(SdkError(str(exc)))
        return Ok(result)

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
        return Ok(self.main_plugin.layers.result_to_ui_dict(result))

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
    cleaned = [
        str(part).strip()
        for part in parts
        if str(part).strip() in _PARTS
    ]
    return cleaned or list(DEFAULT_PARTS)


def _clean_alpha_threshold(value: object) -> int:
    try:
        threshold = int(value)
    except (TypeError, ValueError):
        threshold = 10
    return max(0, min(255, threshold))
