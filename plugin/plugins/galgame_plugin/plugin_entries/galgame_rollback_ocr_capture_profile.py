from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameRollbackOcrCaptureProfileMixin:
    @plugin_entry(
        id="galgame_rollback_ocr_capture_profile",
        name=tr("entries.galgame_rollback_ocr_capture_profile.name", default='回滚 OCR 推荐截图校准'),
        description=tr("entries.galgame_rollback_ocr_capture_profile.description", default='回滚最近一次由推荐 profile 应用产生的 OCR 截图校准。'),
        input_schema={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "default": False},
            },
            "required": ["confirm"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_rollback_ocr_capture_profile(
        self,
        confirm: bool = False,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        if not bool(confirm):
            return Err(SdkError("confirm=true is required before rolling back OCR profile"))
        try:
            payload = await self._rollback_pending_ocr_capture_profile(
                reason="manual_rollback_recommended_capture_profile"
            )
        except Exception as exc:
            return Err(SdkError(f"rollback OCR capture profile failed: {exc}"))
        if payload is None:
            return Err(SdkError("no pending recommended OCR capture profile rollback"))
        return Ok(payload)
