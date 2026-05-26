from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameInstallTextractorMixin:
    @plugin_entry(
        id="galgame_install_textractor",
        name=tr("entries.galgame_install_textractor.name", default='安装 Textractor'),
        description=tr("entries.galgame_install_textractor.description", default='检测并下载安装 TextractorCLI.exe，随后刷新 galgame_plugin 的桥接与读内存状态。'),
        input_schema={
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "default": False},
            },
        },
        timeout=600.0,
        llm_result_fields=["summary"],
    )
    async def galgame_install_textractor(self, force: bool = False, **_):
        if self._cfg is None:
            return Err(SdkError(self._not_configured_message()))
        if not self._textractor_install_lock.acquire(blocking=False):
            return Err(SdkError(self._install_in_progress_message("Textractor")))
        try:
            current_run_id = self._resolve_current_run_id(_)
            progress_callback = self._resolve_install_progress_callback(current_run_id)
            install_textractor_fn = _package_public_attr("install_textractor", install_textractor)
            install_result = await install_textractor_fn(
                logger=self.logger,
                configured_path=self._cfg.memory_reader_textractor_path,
                install_target_dir_raw=self._cfg.memory_reader_install_target_dir,
                release_api_url=self._cfg.memory_reader_install_release_api_url,
                timeout_seconds=self._cfg.memory_reader_install_timeout_seconds,
                textractor_proxy=self._cfg.memory_reader_textractor_proxy,
                force=bool(force),
                task_id=current_run_id or None,
                progress_callback=progress_callback,
            )
            clear_install_inspection_cache()
            self._refresh_dependency_status()
            await self._poll_bridge(force=True)
            return Ok(
                {
                    "summary": str(install_result.get("summary") or self._install_ok_message("textractor", "Textractor")),
                    "install_result": install_result,
                    "status": await self._build_status_payload_async(),
                }
            )
        except Exception as exc:
            return Err(SdkError(self._format_install_entry_error("textractor", "Textractor", exc)))
        finally:
            self._textractor_install_lock.release()
