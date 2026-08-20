from __future__ import annotations

from plugin.core.host import PluginHost


class _RecordingLogger:
    def __init__(self) -> None:
        self.debug_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def debug(self, *args: object, **kwargs: object) -> None:
        self.debug_calls.append((args, kwargs))


class _CommunicationManager:
    def __init__(self) -> None:
        self.received: tuple[str, dict[str, object], float | None] | None = None

    async def trigger(
        self, entry_id: str, args: dict[str, object], timeout: float | None
    ) -> dict[str, bool]:
        self.received = (entry_id, args, timeout)
        return {"ok": True}


async def test_plugin_host_trigger_does_not_log_argument_values() -> None:
    host = PluginHost.__new__(PluginHost)
    host.plugin_id = "study_companion"
    host.logger = _RecordingLogger()
    host.comm_manager = _CommunicationManager()
    args = {
        "document_name": "chapter.md",
        "document_text": "private document sentinel",
    }

    result = await host.trigger("study_document_analyze_start", args, timeout=3.0)

    assert result == {"ok": True}
    assert host.comm_manager.received == (
        "study_document_analyze_start",
        args,
        3.0,
    )
    assert "private document sentinel" not in repr(host.logger.debug_calls)
    assert "document_text" in repr(host.logger.debug_calls)
