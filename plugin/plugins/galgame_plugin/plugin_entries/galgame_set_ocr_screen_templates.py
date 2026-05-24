from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetOcrScreenTemplatesMixin:
    @plugin_entry(
        id="galgame_set_ocr_screen_templates",
        name=tr("entries.galgame_set_ocr_screen_templates.name", default='设置 OCR 屏幕模板'),
        description=tr("entries.galgame_set_ocr_screen_templates.description", default='保存 OCR 屏幕分类模板；模板仅影响 OCR Reader，不影响 Bridge SDK / Memory Reader。'),
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
    async def galgame_set_ocr_screen_templates(
        self,
        screen_templates: list[dict[str, Any]] | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        if not isinstance(screen_templates, list):
            return Err(SdkError("screen_templates must be an array"))
        sanitized = build_config(
            {"ocr_reader": {"screen_templates": screen_templates}}
        ).ocr_reader_screen_templates
        old_templates = json_copy(self._cfg.ocr_reader_screen_templates)
        self._cfg.ocr_reader_screen_templates = json_copy(sanitized)
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._cfg.ocr_reader_screen_templates = old_templates
                return Err(SdkError(f"apply OCR screen templates failed: {exc}"))

        with self._state_lock:
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_ocr_screen_templates(
                self._cfg.ocr_reader_screen_templates
            )
        except Exception as exc:
            self._cfg.ocr_reader_screen_templates = old_templates
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame OCR screen template rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist OCR screen templates failed: {exc}"))

        payload = {
            "screen_templates": json_copy(self._cfg.ocr_reader_screen_templates),
            "summary": f"OCR screen templates={len(self._cfg.ocr_reader_screen_templates)}",
        }
        return Ok(payload)
