from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameAutoRecalibrateOcrDialogueProfileMixin:
    @plugin_entry(
        id="galgame_auto_recalibrate_ocr_dialogue_profile",
        name=tr("entries.galgame_auto_recalibrate_ocr_dialogue_profile.name", default='自动重新校准 OCR 对白区'),
        description=tr("entries.galgame_auto_recalibrate_ocr_dialogue_profile.description", default='对当前已附着 OCR 目标窗口自动重校准对白区，并保存到当前窗口分辨率。'),
        input_schema={"type": "object", "properties": {}},
        timeout=120.0,
        llm_result_fields=["summary", "sample_text"],
    )
    async def galgame_auto_recalibrate_ocr_dialogue_profile(self, **_):
        if self._ocr_reader_manager is None:
            return Err(SdkError("ocr_reader manager is not initialized"))
        try:
            recalibrated = await asyncio.to_thread(
                self._ocr_reader_manager.auto_recalibrate_dialogue_profile
            )
            payload = await self._save_ocr_capture_profile_payload(
                process_name=str(recalibrated.get("process_name") or ""),
                stage=OCR_CAPTURE_PROFILE_STAGE_DIALOGUE,
                capture_profile=dict(recalibrated.get("capture_profile") or {}),
                clear=False,
                save_scope=OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
                width=int(recalibrated.get("window_width") or 0),
                height=int(recalibrated.get("window_height") or 0),
            )
        except ValueError as exc:
            return Err(SdkError(str(exc)))
        except Exception as exc:
            return Err(SdkError(f"auto recalibrate OCR dialogue profile failed: {exc}"))
        payload.update(
            {
                "sample_text": str(recalibrated.get("sample_text") or ""),
                "save_scope": OCR_CAPTURE_PROFILE_SAVE_SCOPE_WINDOW_BUCKET,
                "bucket_key": str(recalibrated.get("bucket_key") or payload.get("bucket_key") or ""),
                "window_width": int(
                    recalibrated.get("window_width") or payload.get("window_width") or 0
                ),
                "window_height": int(
                    recalibrated.get("window_height") or payload.get("window_height") or 0
                ),
                "summary": str(recalibrated.get("summary") or payload.get("summary") or ""),
            }
        )
        return Ok(payload)
