from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameGetOcrScreenAwarenessSnapshotMixin:
    @plugin_entry(
        id="galgame_get_ocr_screen_awareness_snapshot",
        name=tr("entries.galgame_get_ocr_screen_awareness_snapshot.name", default='获取 OCR 屏幕感知截图'),
        description=tr("entries.galgame_get_ocr_screen_awareness_snapshot.description", default='返回最近一次 OCR 屏幕感知截图；仅在 Vision 显式开启且短期缓存未过期时可用。'),
        input_schema={"type": "object", "properties": {}},
        llm_result_fields=["summary"],
    )
    async def galgame_get_ocr_screen_awareness_snapshot(self, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        snapshot = self.latest_ocr_vision_snapshot()
        if not isinstance(snapshot, dict) or not snapshot.get("vision_image_base64"):
            return Err(SdkError("no OCR screen awareness snapshot is available; enable Vision and wait for a full-frame OCR capture"))
        payload = {
            "snapshot": snapshot,
            "summary": (
                f"OCR screen awareness snapshot "
                f"{int(snapshot.get('width') or 0)}x{int(snapshot.get('height') or 0)}"
            ),
        }
        return Ok(payload)
