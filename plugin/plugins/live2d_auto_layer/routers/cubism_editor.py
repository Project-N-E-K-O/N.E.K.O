from __future__ import annotations

from pathlib import Path

from plugin.sdk.plugin import Err, Ok, SdkError, plugin_entry, tr, ui
from plugin.sdk.shared.core.router import PluginRouter

from ..core.config import OUTPUT_DIR, PLUGIN_ROOT
from ..core.cubism import (
    DEFAULT_APPROVAL_WAIT_SECONDS,
    DEFAULT_CUBISM_HOST,
    DEFAULT_CUBISM_PORT,
    CubismEditorApiError,
    CubismEditorClient,
)

TOKEN_PATH = OUTPUT_DIR / "cubism_editor_token.json"


class CubismEditorRouter(PluginRouter):
    """Cubism Editor External Application Integration entries."""

    def __init__(self):
        super().__init__(name="cubism_editor")

    @ui.action(
        label=tr("actions.cubismStatus.label", default="Cubism status"),
        tone="default",
        group="cubism",
        order=10,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_cubism_status",
        name=tr("entries.cubismStatus.name", default="Check Cubism Editor API status"),
        description=tr(
            "entries.cubismStatus.description",
            default="Check whether Live2D Cubism Editor External Application Integration is listening.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "host": {"type": "string", "default": DEFAULT_CUBISM_HOST},
                "port": {"type": "integer", "default": DEFAULT_CUBISM_PORT},
            },
        },
        llm_result_fields=["status", "host", "port", "port_open", "token_saved"],
    )
    async def cubism_status(self, host: str = DEFAULT_CUBISM_HOST, port: int = DEFAULT_CUBISM_PORT, **_):
        client = _client(host=host, port=port)
        return Ok(client.status().to_dict())

    @ui.action(
        label=tr("actions.cubismRegister.label", default="Register Cubism app"),
        tone="primary",
        group="cubism",
        order=20,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_cubism_register",
        name=tr("entries.cubismRegister.name", default="Register with Cubism Editor"),
        description=tr(
            "entries.cubismRegister.description",
            default="Register this plugin with Cubism Editor and store the returned token for future connections.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "host": {"type": "string", "default": DEFAULT_CUBISM_HOST},
                "port": {"type": "integer", "default": DEFAULT_CUBISM_PORT},
                "approval_wait_seconds": {"type": "number", "default": DEFAULT_APPROVAL_WAIT_SECONDS},
            },
        },
        timeout=180,
        llm_result_fields=[
            "registered",
            "approved",
            "token_saved",
            "approval_wait_timed_out",
            "approval_wait_seconds",
            "host",
            "port",
        ],
    )
    async def cubism_register(
        self,
        host: str = DEFAULT_CUBISM_HOST,
        port: int = DEFAULT_CUBISM_PORT,
        approval_wait_seconds: float = DEFAULT_APPROVAL_WAIT_SECONDS,
        **_,
    ):
        try:
            result = await _client(host=host, port=port).register_plugin(
                approval_wait_seconds=float(approval_wait_seconds),
            )
        except Exception as exc:
            self.logger.warning("live2d_cubism_register failed: {}", exc, exc_info=True)
            return Err(_sdk_error(exc))
        return Ok(result)

    @ui.action(
        label=tr("actions.cubismEditorState.label", default="Cubism editor state"),
        tone="default",
        group="cubism",
        order=30,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_cubism_editor_state",
        name=tr("entries.cubismEditorState.name", default="Read Cubism Editor state"),
        description=tr(
            "entries.cubismEditorState.description",
            default="Read Cubism Editor approval state, current edit mode, current model/document UID, and open documents.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "host": {"type": "string", "default": DEFAULT_CUBISM_HOST},
                "port": {"type": "integer", "default": DEFAULT_CUBISM_PORT},
            },
        },
        timeout=30,
        llm_result_fields=["approved", "current_edit_mode", "current_model", "current_document"],
    )
    async def cubism_editor_state(self, host: str = DEFAULT_CUBISM_HOST, port: int = DEFAULT_CUBISM_PORT, **_):
        try:
            result = await _client(host=host, port=port).editor_state()
        except Exception as exc:
            self.logger.warning("live2d_cubism_editor_state failed: {}", exc, exc_info=True)
            return Err(_sdk_error(exc))
        return Ok(result)

    @ui.action(
        label=tr("actions.cubismSendLog.label", default="Send Cubism log"),
        tone="primary",
        group="cubism",
        order=40,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_cubism_send_log",
        name=tr("entries.cubismSendLog.name", default="Send a log message to Cubism Editor"),
        description=tr(
            "entries.cubismSendLog.description",
            default="Send an informational or warning message to the Cubism Editor log palette.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "log_type": {"type": "string", "enum": ["info", "warning"], "default": "info"},
                "display": {"type": "boolean", "default": True},
                "host": {"type": "string", "default": DEFAULT_CUBISM_HOST},
                "port": {"type": "integer", "default": DEFAULT_CUBISM_PORT},
            },
            "required": ["message"],
        },
        timeout=30,
        llm_result_fields=["sent"],
    )
    async def cubism_send_log(
        self,
        message: str = "",
        log_type: str = "info",
        display: bool = True,
        host: str = DEFAULT_CUBISM_HOST,
        port: int = DEFAULT_CUBISM_PORT,
        **_,
    ):
        try:
            result = await _client(host=host, port=port).send_log(
                message,
                log_type=log_type,
                display=display,
            )
        except Exception as exc:
            self.logger.warning("live2d_cubism_send_log failed: {}", exc, exc_info=True)
            return Err(_sdk_error(exc))
        return Ok(result)

    @ui.action(
        label=tr("actions.cubismWaitMocExport.label", default="Wait MOC export"),
        tone="primary",
        group="cubism",
        order=50,
        refresh_context=False,
    )
    @plugin_entry(
        id="live2d_cubism_wait_moc_export",
        name=tr("entries.cubismWaitMocExport.name", default="Wait for Cubism MOC export"),
        description=tr(
            "entries.cubismWaitMocExport.description",
            default="Enable MOC export notifications and wait for Cubism Editor to export a .moc3/model3 package.",
        ),
        input_schema={
            "type": "object",
            "properties": {
                "timeout_seconds": {"type": "number", "default": 30},
                "host": {"type": "string", "default": DEFAULT_CUBISM_HOST},
                "port": {"type": "integer", "default": DEFAULT_CUBISM_PORT},
            },
        },
        timeout=300,
        llm_result_fields=["event", "timed_out", "data"],
    )
    async def cubism_wait_moc_export(
        self,
        timeout_seconds: float = 30,
        host: str = DEFAULT_CUBISM_HOST,
        port: int = DEFAULT_CUBISM_PORT,
        **_,
    ):
        try:
            result = await _client(host=host, port=port).wait_for_moc_export(
                timeout_seconds=float(timeout_seconds),
            )
        except Exception as exc:
            self.logger.warning("live2d_cubism_wait_moc_export failed: {}", exc, exc_info=True)
            return Err(_sdk_error(exc))
        return Ok(result)


def _client(*, host: str, port: int) -> CubismEditorClient:
    return CubismEditorClient(
        host=host or DEFAULT_CUBISM_HOST,
        port=int(port or DEFAULT_CUBISM_PORT),
        token_path=TOKEN_PATH,
        plugin_path=Path(PLUGIN_ROOT).resolve(),
    )


def _sdk_error(exc: Exception) -> SdkError:
    if isinstance(exc, CubismEditorApiError):
        suffix = f" ({exc.error_type})" if exc.error_type else ""
        return SdkError(f"{exc}{suffix}")
    return SdkError(str(exc))
