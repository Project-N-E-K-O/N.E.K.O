from __future__ import annotations

from importlib import import_module

__all__ = [
    "RunCancelRequest",
    "RunRecord",
    "ExportCategory",
    "ExportListResponse",
    "InvalidRunTransition",
    "validate_run_transition",
    "create_run",
    "get_run",
    "cancel_run",
    "shutdown_runs",
    "list_export_for_run",
    "list_runs",
    "ws_run_endpoint",
    "issue_run_token",
    "blob_store",
]

_EXPORT_MAP: dict[str, tuple[str, str]] = {
    "RunCancelRequest": ("plugin.server.runs.manager", "RunCancelRequest"),
    "RunRecord": ("plugin.server.runs.manager", "RunRecord"),
    "ExportCategory": ("plugin.server.runs.manager", "ExportCategory"),
    "ExportListResponse": ("plugin.server.runs.manager", "ExportListResponse"),
    "InvalidRunTransition": ("plugin.server.runs.manager", "InvalidRunTransition"),
    "validate_run_transition": ("plugin.server.runs.manager", "validate_run_transition"),
    "create_run": ("plugin.server.runs.manager", "create_run"),
    "get_run": ("plugin.server.runs.manager", "get_run"),
    "cancel_run": ("plugin.server.runs.manager", "cancel_run"),
    "shutdown_runs": ("plugin.server.runs.manager", "shutdown_runs"),
    "list_export_for_run": ("plugin.server.runs.manager", "list_export_for_run"),
    "list_runs": ("plugin.server.runs.manager", "list_runs"),
    "ws_run_endpoint": ("plugin.server.runs.websocket", "ws_run_endpoint"),
    "issue_run_token": ("plugin.server.runs.tokens", "issue_run_token"),
    "blob_store": ("plugin.server.runs.storage", "blob_store"),
}


def __getattr__(name: str) -> object:
    export = _EXPORT_MAP.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = export
    module = import_module(module_name)
    return getattr(module, attr_name)
