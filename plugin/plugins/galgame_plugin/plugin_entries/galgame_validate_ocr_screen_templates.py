from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameValidateOcrScreenTemplatesMixin:
    @plugin_entry(
        id="galgame_validate_ocr_screen_templates",
        name=tr("entries.galgame_validate_ocr_screen_templates.name", default='验证 OCR 屏幕模板'),
        description=tr("entries.galgame_validate_ocr_screen_templates.description", default='用当前 OCR 运行时和最近文本回放验证屏幕模板命中结果。'),
        input_schema={
            "type": "object",
            "properties": {
                "screen_templates": {
                    "type": "array",
                    "items": {"type": "object"},
                    "default": [],
                },
            },
            "required": ["screen_templates"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_validate_ocr_screen_templates(
        self,
        screen_templates: list[dict[str, Any]] | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        if not isinstance(screen_templates, list):
            return Err(SdkError("screen_templates must be an array"))
        try:
            payload = self._current_ocr_screen_template_validation_payload(screen_templates)
        except Exception as exc:
            return Err(SdkError(f"validate OCR screen templates failed: {exc}"))
        return Ok(payload)
