from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameListOcrWindowsMixin:
    @plugin_entry(
        id="galgame_list_ocr_windows",
        name=tr("entries.galgame_list_ocr_windows.name", default='列出 OCR 候选窗口'),
        description=tr("entries.galgame_list_ocr_windows.description", default='返回当前 OCR Reader 的可选窗口，可选包含只读排除列表。'),
        input_schema={
            "type": "object",
            "properties": {
                "include_excluded": {"type": "boolean", "default": False},
                "force": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_list_ocr_windows(
        self,
        include_excluded: bool = False,
        force: bool = False,
        **_,
    ):
        if self._ocr_reader_manager is None:
            return Err(SdkError("ocr_reader manager is not initialized"))
        try:
            payload = await asyncio.to_thread(
                self._ocr_reader_manager.list_windows_snapshot,
                include_excluded=bool(include_excluded),
                force=bool(force),
            )
        except Exception as exc:
            return Err(SdkError(f"list OCR windows failed: {exc}"))
        payload["summary"] = (
            f"eligible={int(payload.get('candidate_count') or 0)} "
            f"excluded={int(payload.get('excluded_candidate_count') or 0)} "
            f"mode={payload.get('target_selection_mode') or 'auto'}"
        )
        return Ok(payload)
