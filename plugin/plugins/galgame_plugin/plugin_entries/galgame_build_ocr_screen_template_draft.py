from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameBuildOcrScreenTemplateDraftMixin:
    @plugin_entry(
        id="galgame_build_ocr_screen_template_draft",
        name=tr("entries.galgame_build_ocr_screen_template_draft.name", default='生成 OCR 屏幕模板草稿'),
        description=tr("entries.galgame_build_ocr_screen_template_draft.description", default='根据当前 OCR 运行时、窗口信息和最近识别文本生成可编辑的屏幕模板草稿。'),
        input_schema={
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "enum": sorted(OCR_CAPTURE_PROFILE_STAGES),
                },
                "region": {"type": "object"},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_build_ocr_screen_template_draft(
        self,
        stage: str | None = None,
        region: dict[str, Any] | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        try:
            payload = self._build_ocr_screen_template_draft_payload(
                stage=stage,
                region=region,
            )
        except Exception as exc:
            return Err(SdkError(f"build OCR screen template draft failed: {exc}"))
        if not isinstance(payload, dict):
            return Err(SdkError("build OCR screen template draft failed: invalid payload"))
        template = payload.get("template")
        template = template if isinstance(template, dict) else {}
        payload["summary"] = (
            f"OCR screen template draft stage={template.get('stage')} "
            f"id={template.get('id')}"
        )
        return Ok(payload)
