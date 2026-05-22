from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameSetMemoryReaderTargetMixin:
    @plugin_entry(
        id="galgame_set_memory_reader_target",
        name=tr("entries.galgame_set_memory_reader_target.name", default='设置 Memory Reader 目标进程'),
        description=tr("entries.galgame_set_memory_reader_target.description", default='锁定或清除 Memory Reader 的手动进程目标。'),
        input_schema={
            "type": "object",
            "properties": {
                "process_key": {"type": "string", "default": ""},
                "pid": {"type": "integer", "default": 0},
                "exe_path": {"type": "string", "default": ""},
                "process_name": {"type": "string", "default": ""},
                "clear": {"type": "boolean", "default": False},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_set_memory_reader_target(
        self,
        process_key: str = "",
        pid: int = 0,
        exe_path: str = "",
        process_name: str = "",
        clear: bool = False,
        **_,
    ):
        if self._memory_reader_manager is None:
            return Err(SdkError("memory_reader manager is not initialized"))

        if clear:
            target_payload = {
                "mode": "auto",
                "process_key": "",
                "process_name": "",
                "exe_path": "",
                "pid": 0,
                "engine": "",
                "detected_engine": "",
                "detection_reason": "",
                "create_time": 0.0,
                "selected_at": "",
            }
            summary = "Memory Reader process target cleared; using auto detection"
        else:
            try:
                target_payload = await asyncio.to_thread(
                    self._memory_reader_manager.resolve_manual_process_target,
                    process_key=process_key,
                    pid=pid,
                    exe_path=exe_path,
                    process_name=process_name,
                )
            except ValueError as exc:
                return Err(SdkError(str(exc)))
            except Exception as exc:
                return Err(SdkError(f"resolve Memory Reader process target failed: {exc}"))
            summary = (
                f"Memory Reader target locked to {target_payload.get('process_name') or '(unknown)'}"
            )

        try:
            self._persist.persist_memory_reader_target(target_payload)
        except Exception as exc:
            return Err(SdkError(f"persist Memory Reader target failed: {exc}"))

        self._memory_reader_manager.update_process_target(target_payload)
        with self._state_lock:
            self._state.memory_reader_target = json_copy(target_payload)
            self._state_dirty = True
            self._cached_snapshot = None
        background_poll_started = self._start_background_bridge_poll()
        return Ok(
            {
                "process_target": json_copy(target_payload),
                "cleared": bool(clear),
                "summary": summary,
                "background_poll_started": background_poll_started,
            }
        )
