from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetLlmVisionMixin:
    @plugin_entry(
        id="galgame_set_llm_vision",
        name=tr("entries.galgame_set_llm_vision.name", default='设置 LLM 视觉辅助'),
        description=tr("entries.galgame_set_llm_vision.description", default='切换 OCR Agent 低置信度场景的截图直传，并设置图片最大边长。'),
        input_schema={
            "type": "object",
            "properties": {
                "vision_enabled": {"type": "boolean"},
                "vision_max_image_px": {
                    "type": "integer",
                    "minimum": 64,
                    "maximum": 2048,
                    "default": 768,
                },
            },
            "required": ["vision_enabled"],
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_llm_vision(
        self,
        vision_enabled: bool,
        vision_max_image_px: int | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        try:
            normalized_max_px = int(
                vision_max_image_px
                if vision_max_image_px is not None
                else self._cfg.llm_vision_max_image_px
            )
        except (TypeError, ValueError):
            return Err(SdkError("vision_max_image_px must be an integer"))
        if normalized_max_px < 64 or normalized_max_px > 2048:
            return Err(SdkError("vision_max_image_px must be between 64 and 2048"))

        old_enabled = self._cfg.llm_vision_enabled
        old_max_px = self._cfg.llm_vision_max_image_px
        self._cfg.llm_vision_enabled = bool(vision_enabled)
        self._cfg.llm_vision_max_image_px = normalized_max_px
        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._cfg.llm_vision_enabled = old_enabled
                self._cfg.llm_vision_max_image_px = old_max_px
                return Err(SdkError(f"apply LLM vision failed: {exc}"))

        with self._state_lock:
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_llm_vision(
                vision_enabled=self._cfg.llm_vision_enabled,
                vision_max_image_px=self._cfg.llm_vision_max_image_px,
            )
        except Exception as exc:
            self._cfg.llm_vision_enabled = old_enabled
            self._cfg.llm_vision_max_image_px = old_max_px
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame LLM vision rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist LLM vision failed: {exc}"))

        payload = {
            "vision_enabled": self._cfg.llm_vision_enabled,
            "vision_max_image_px": self._cfg.llm_vision_max_image_px,
            "summary": (
                f"LLM vision enabled={self._cfg.llm_vision_enabled} "
                f"max_image_px={self._cfg.llm_vision_max_image_px}"
            ),
        }
        return Ok(payload)
