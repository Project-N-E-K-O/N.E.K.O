from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetRapidocrLangMixin:
    @plugin_entry(
        id="galgame_set_rapidocr_lang",
        name=tr("entries.galgame_set_rapidocr_lang.name", default='切换 OCR 识别语言'),
        description=tr(
            "entries.galgame_set_rapidocr_lang.description",
            default='切换 RapidOCR 文字识别语言模型；手动切换语言后关闭自动检测。',
        ),
        input_schema={
            "type": "object",
            "properties": {
                "lang_type": {
                    "type": "string",
                    "enum": ["ch", "japan", "korean", "en"],
                },
                "auto_detect_lang": {
                    "type": "boolean",
                },
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_rapidocr_lang(
        self,
        lang_type: str | None = None,
        auto_detect_lang: bool | None = None,
        **_,
    ):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))

        normalized_lang = str(lang_type or "").strip().lower() or None
        if normalized_lang is not None and normalized_lang not in {"ch", "japan", "korean", "en"}:
            return Err(SdkError(f"invalid lang_type: {lang_type!r}"))
        if normalized_lang is None and auto_detect_lang is None:
            return Err(SdkError("lang_type or auto_detect_lang is required"))

        old_lang = self._cfg.rapidocr_lang_type
        old_auto = self._cfg.rapidocr_auto_detect_lang
        old_last = self._cfg.rapidocr_auto_detect_last_lang
        if normalized_lang is not None:
            self._cfg.rapidocr_lang_type = normalized_lang
            self._cfg.rapidocr_auto_detect_last_lang = normalized_lang
            self._cfg.rapidocr_auto_detect_lang = False
        if normalized_lang is None and auto_detect_lang is not None:
            self._cfg.rapidocr_auto_detect_lang = bool(auto_detect_lang)

        if self._ocr_reader_manager is not None:
            try:
                self._ocr_reader_manager.update_config(self._cfg)
            except Exception as exc:
                self._cfg.rapidocr_lang_type = old_lang
                self._cfg.rapidocr_auto_detect_lang = old_auto
                self._cfg.rapidocr_auto_detect_last_lang = old_last
                return Err(SdkError(f"apply rapidocr lang failed: {exc}"))

        with self._state_lock:
            self._state.next_poll_at_monotonic = 0.0
            self._state_dirty = True
            self._cached_snapshot = None

        try:
            self._config_service.persist_rapidocr_lang(
                lang_type=normalized_lang,
                auto_detect_lang=(
                    bool(auto_detect_lang)
                    if normalized_lang is None and auto_detect_lang is not None
                    else (False if normalized_lang is not None else None)
                ),
                auto_detect_last_lang=(
                    self._cfg.rapidocr_auto_detect_last_lang
                    if normalized_lang is None
                    else normalized_lang
                ),
            )
        except Exception as exc:
            self._cfg.rapidocr_lang_type = old_lang
            self._cfg.rapidocr_auto_detect_lang = old_auto
            self._cfg.rapidocr_auto_detect_last_lang = old_last
            if self._ocr_reader_manager is not None:
                try:
                    self._ocr_reader_manager.update_config(self._cfg)
                except Exception as rollback_exc:
                    _log_plugin_noncritical(
                        self.logger,
                        "warning",
                        "galgame rapidocr lang rollback update_config failed: {}",
                        rollback_exc,
                    )
            return Err(SdkError(f"persist rapidocr lang failed: {exc}"))

        self._refresh_dependency_status()
        self._start_background_bridge_poll()
        return Ok({
            "lang_type": self._cfg.rapidocr_lang_type,
            "auto_detect_lang": self._cfg.rapidocr_auto_detect_lang,
            "summary": (
                f"RapidOCR lang={self._cfg.rapidocr_lang_type} "
                f"auto_detect={'on' if self._cfg.rapidocr_auto_detect_lang else 'off'}"
            ),
        })
