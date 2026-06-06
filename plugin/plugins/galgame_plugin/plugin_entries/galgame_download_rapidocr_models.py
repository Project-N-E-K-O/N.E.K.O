from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameDownloadRapidocrModelsMixin:
    @plugin_entry(
        id="galgame_download_rapidocr_models",
        name=tr("entries.galgame_download_rapidocr_models.name", default='下载 RapidOCR 模型'),
        description=tr("entries.galgame_download_rapidocr_models.description", default='为当前 (lang_type, ocr_version) 选择优先从百度云下载缺失的 RapidOCR 模型文件到插件模型缓存目录，下载失败时自动回退至 ModelScope。bundled 默认（ch+PP-OCRv4）不需要下载。'),
        input_schema={
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False},
            },
        },
        timeout=600.0,
        llm_result_fields=["summary"],
    )
    async def galgame_download_rapidocr_models(self, force: bool = False, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        if not self._rapidocr_models_lock.acquire(blocking=False):
            return Err(SdkError(self._install_in_progress_message("RapidOCR Models")))
        try:
            current_run_id = self._resolve_current_run_id(_)
            progress_callback = self._resolve_install_progress_callback(current_run_id)
            from plugin.plugins._shared.rapidocr.rapidocr_support import download_rapidocr_models
            from plugin.server.routes._install_task_store import update_install_task_state

            download_result = await download_rapidocr_models(
                logger=self.logger,
                install_target_dir_raw=self._cfg.rapidocr_install_target_dir,
                ocr_version=self._cfg.rapidocr_ocr_version,
                lang_type=self._cfg.rapidocr_lang_type,
                timeout_seconds=float(self._cfg.ocr_reader_install_timeout_seconds or 180.0),
                force=bool(force),
                plugin_id="galgame_plugin",
                task_id=current_run_id or None,
                progress_callback=progress_callback,
                before_completed_callback=clear_install_inspection_cache,
                install_state_updater=update_install_task_state,
            )
            clear_install_inspection_cache()
            await self._poll_bridge(force=True)
            downloaded = download_result.get("downloaded") or []
            summary = (
                f"RapidOCR models ready ({len(downloaded)} file(s) downloaded)"
                if downloaded
                else "RapidOCR models already present"
            )
            return Ok(
                {
                    "summary": summary,
                    "download_result": download_result,
                    "status": await self._build_status_payload_async(),
                }
            )
        except Exception as exc:
            return Err(SdkError(self._format_install_entry_error("rapidocr_models", "RapidOCR Models", exc)))
        finally:
            self._rapidocr_models_lock.release()
