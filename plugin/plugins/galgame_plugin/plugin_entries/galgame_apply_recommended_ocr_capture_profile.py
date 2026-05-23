from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameApplyRecommendedOcrCaptureProfileMixin:
    @plugin_entry(
        id="galgame_apply_recommended_ocr_capture_profile",
        name=tr("entries.galgame_apply_recommended_ocr_capture_profile.name", default='应用推荐 OCR 截图校准'),
        description=tr("entries.galgame_apply_recommended_ocr_capture_profile.description", default='在用户确认后应用当前 OCR Reader 推荐的截图 profile，并记录回滚点。'),
        input_schema={
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "default": False},
                "enable_auto_apply": {"type": "boolean", "default": False},
                "allow_manual_override": {"type": "boolean", "default": False},
            },
            "required": ["confirm"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_apply_recommended_ocr_capture_profile(
        self,
        confirm: bool = False,
        enable_auto_apply: bool = False,
        allow_manual_override: bool = False,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        if not bool(confirm):
            return Err(SdkError("confirm=true is required before applying a recommended OCR profile"))
        with self._state_lock:
            runtime = json_copy(self._state.ocr_reader_runtime)
        old_auto_apply = bool(self._ocr_capture_profile_auto_apply_enabled)
        self._ocr_capture_profile_auto_apply_enabled = bool(enable_auto_apply)
        try:
            payload = await self._apply_recommended_ocr_capture_profile_payload(
                runtime,
                allow_manual_override=bool(allow_manual_override),
                reason="manual_apply_recommended_capture_profile",
            )
        except ValueError as exc:
            self._ocr_capture_profile_auto_apply_enabled = old_auto_apply
            return Err(SdkError(str(exc)))
        except Exception as exc:
            self._ocr_capture_profile_auto_apply_enabled = old_auto_apply
            return Err(SdkError(f"apply recommended OCR capture profile failed: {exc}"))
        return Ok(payload)
