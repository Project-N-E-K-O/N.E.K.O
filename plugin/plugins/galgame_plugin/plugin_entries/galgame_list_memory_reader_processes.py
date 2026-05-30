from __future__ import annotations
from ._common import *  # noqa: F401, F403


class _GalgameListMemoryReaderProcessesMixin:
    @plugin_entry(
        id="galgame_list_memory_reader_processes",
        name=tr("entries.galgame_list_memory_reader_processes.name", default='列出 Memory Reader 候选进程'),
        description=tr("entries.galgame_list_memory_reader_processes.description", default='返回 Memory Reader 可选进程，包含 exe 路径、检测到的引擎和识别原因。'),
        input_schema={
            "type": "object",
            "properties": {
                "include_unknown": {"type": "boolean", "default": True},
            },
        },
        llm_result_fields=["summary"],
    )
    async def galgame_list_memory_reader_processes(
        self,
        include_unknown: bool = True,
        **_,
    ):
        if self._memory_reader_manager is None:
            return Err(SdkError("memory_reader manager is not initialized"))
        try:
            payload = await asyncio.to_thread(
                self._memory_reader_manager.list_processes_snapshot,
                include_unknown=bool(include_unknown),
            )
        except Exception as exc:
            return Err(SdkError(f"list Memory Reader processes failed: {exc}"))
        payload["summary"] = (
            f"processes={int(payload.get('candidate_count') or 0)} "
            f"mode={payload.get('target_selection_mode') or 'auto'}"
        )
        return Ok(payload)
