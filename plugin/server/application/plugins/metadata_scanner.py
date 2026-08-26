from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from plugin._types.events import EventHandler, EventMeta
from plugin.core import registry as registry_module
from plugin.core.state import state


_HOST_API_TOKEN_ENV = "NEKO_PLUGIN_HOST_API_TOKEN"
_RESULT_PREFIX = "NEKO_PLUGIN_METADATA_RESULT:"
_MAX_METADATA_RESULT_BYTES = 1024 * 1024
_WORKER_BOOTSTRAP = (
    "import os,sys;"
    "_stdout_fd=sys.stdout.fileno();"
    "_protocol_fd=os.dup(_stdout_fd);"
    "_devnull_fd=os.open(os.devnull,os.O_WRONLY);"
    "os.dup2(_devnull_fd,_stdout_fd);"
    "os.dup2(_devnull_fd,sys.stderr.fileno());"
    "os.close(_devnull_fd);"
    "from plugin.server.application.plugins.metadata_scanner "
    "import _worker_main;_worker_main(_protocol_fd)"
)


def _metadata_worker_command() -> list[str]:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return [sys.executable, "--neko-plugin-metadata-worker"]
    return [sys.executable, "-c", _WORKER_BOOTSTRAP]


def _handler_key_belongs_to_plugin(key: str, plugin_id: str) -> bool:
    return key.startswith(f"{plugin_id}.") or key.startswith(f"{plugin_id}:")


class PluginMetadataScanError(RuntimeError):
    def __init__(self, error_type: str, message: str) -> None:
        super().__init__(message)
        self.error_type = error_type


@dataclass(slots=True)
class IsolatedPluginMetadata:
    entries_preview: list[dict[str, object]]
    handlers: dict[str, dict[str, object]]
    entry_methods: dict[str, str]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _event_meta_payload(meta: object) -> dict[str, object]:
    raw = getattr(meta, "__dict__", None)
    if isinstance(raw, dict):
        normalized = _json_safe(raw)
        if isinstance(normalized, dict):
            return normalized

    return {
        "event_type": str(getattr(meta, "event_type", "plugin_entry") or "plugin_entry"),
        "id": str(getattr(meta, "id", "") or ""),
        "name": _json_safe(getattr(meta, "name", "")),
        "description": _json_safe(getattr(meta, "description", "")),
        "input_schema": _json_safe(getattr(meta, "input_schema", None)),
        "kind": str(getattr(meta, "kind", "action") or "action"),
        "auto_start": bool(getattr(meta, "auto_start", False)),
        "enabled": bool(getattr(meta, "enabled", True)),
        "dynamic": bool(getattr(meta, "dynamic", False)),
        "metadata": _json_safe(getattr(meta, "metadata", None)),
    }


def _scan_in_worker(request: Mapping[str, object]) -> dict[str, object]:
    # Imports stay inside the credential-free worker. In particular, importing
    # an untrusted plugin module must never happen in the credential-bearing
    # agent/plugin-server process.
    from plugin.core.host import _import_plugin_module
    from plugin.core.registry import (
        _ensure_python_requirement_paths,
        _extract_entries_preview,
        scan_static_metadata,
    )
    from plugin.logging_config import get_logger

    plugin_id = str(request["plugin_id"])
    module_path = str(request["module_path"])
    class_name = str(request["class_name"])
    config_path = Path(str(request["config_path"]))
    conf_obj = request.get("conf")
    pdata_obj = request.get("pdata")
    conf = dict(conf_obj) if isinstance(conf_obj, Mapping) else {}
    pdata = dict(pdata_obj) if isinstance(pdata_obj, Mapping) else {}
    requirement_paths_obj = request.get("python_requirement_paths")
    requirement_paths = (
        [Path(str(item)) for item in requirement_paths_obj]
        if isinstance(requirement_paths_obj, list)
        else []
    )
    logger = get_logger("server.application.plugins.metadata_worker")

    _ensure_python_requirement_paths(requirement_paths, logger, plugin_id)
    module_obj = _import_plugin_module(module_path, config_path, logger)
    cls_obj = getattr(module_obj, class_name)
    if not isinstance(cls_obj, type):
        raise TypeError(
            f"Plugin '{plugin_id}' entry class '{class_name}' is invalid"
        )

    scan_static_metadata(plugin_id, cls_obj, conf, pdata)
    entries_preview = _extract_entries_preview(plugin_id, cls_obj, conf, pdata)

    prefix_dot = f"{plugin_id}."
    prefix_colon = f"{plugin_id}:"
    handlers: dict[str, dict[str, object]] = {}
    with state.acquire_event_handlers_read_lock():
        for key, handler in state.event_handlers.items():
            if isinstance(key, str) and (
                key.startswith(prefix_dot) or key.startswith(prefix_colon)
            ):
                handlers[key] = _event_meta_payload(handler.meta)

    entry_methods = {
        str(entry_id): str(method_name)
        for (mapped_plugin_id, entry_id), method_name in registry_module.plugin_entry_method_map.items()
        if mapped_plugin_id == plugin_id
    }
    return {
        "ok": True,
        "entries_preview": _json_safe(entries_preview),
        "handlers": handlers,
        "entry_methods": entry_methods,
    }


def _worker_main(protocol_fd: int | None = None) -> None:
    # Reserve a private duplicate of the protocol pipe, then redirect the
    # process-wide stdout/stderr descriptors before importing plugin code.
    # This prevents untrusted import output from being buffered without bound
    # by the parent and keeps it off the result channel. os._exit below also
    # prevents plugin-registered atexit hooks from appending a forged record.
    raw_close = os.close
    raw_dup = os.dup
    raw_dup2 = os.dup2
    raw_open = os.open
    raw_write = os.write
    immediate_exit = os._exit
    trusted_json_dumps = json.dumps
    result_prefix = _RESULT_PREFIX
    max_result_bytes = _MAX_METADATA_RESULT_BYTES
    if protocol_fd is None:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
        protocol_fd = raw_dup(stdout_fd)
        devnull_fd = raw_open(os.devnull, os.O_WRONLY)
        raw_dup2(devnull_fd, stdout_fd)
        raw_dup2(devnull_fd, stderr_fd)
        raw_close(devnull_fd)
    try:
        request_obj = json.loads(sys.stdin.read())
        if not isinstance(request_obj, dict):
            raise TypeError("metadata scan request must be an object")
        result = _scan_in_worker(request_obj)
    except BaseException as exc:
        result = {
            "ok": False,
            "error_type": type(exc).__name__,
            "message": str(exc),
        }
    encoded_result = (
        "\n"
        + result_prefix
        + trusted_json_dumps(result, ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    if len(encoded_result) > max_result_bytes:
        result = {
            "ok": False,
            "error_type": "MetadataResultTooLarge",
            "message": (
                "Plugin metadata result exceeds the "
                f"{max_result_bytes}-byte protocol limit"
            ),
        }
        encoded_result = (
            "\n"
            + result_prefix
            + trusted_json_dumps(result, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
    remaining = memoryview(encoded_result)
    while remaining:
        try:
            written = raw_write(protocol_fd, remaining)
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError("metadata worker result pipe closed")
        remaining = remaining[written:]
    raw_close(protocol_fd)
    immediate_exit(0)


def scan_plugin_metadata_isolated(
    *,
    plugin_id: str,
    module_path: str,
    class_name: str,
    config_path: Path,
    conf: Mapping[str, object],
    pdata: Mapping[str, object],
    python_requirement_paths: list[Path] | tuple[Path, ...] = (),
    timeout: float = 30.0,
) -> IsolatedPluginMetadata:
    request = {
        "plugin_id": plugin_id,
        "module_path": module_path,
        "class_name": class_name,
        "config_path": str(config_path),
        "conf": _json_safe(conf),
        "pdata": _json_safe(pdata),
        "python_requirement_paths": [str(path) for path in python_requirement_paths],
    }
    child_env = os.environ.copy()
    child_env.pop(_HOST_API_TOKEN_ENV, None)
    project_root = Path(__file__).resolve().parents[4]

    try:
        completed = subprocess.run(
            _metadata_worker_command(),
            input=json.dumps(request, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(project_root),
            env=child_env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PluginMetadataScanError(
            "TimeoutExpired",
            f"Plugin metadata scan timed out after {timeout:g}s",
        ) from exc
    except OSError as exc:
        raise PluginMetadataScanError(type(exc).__name__, str(exc)) from exc

    payload: dict[str, object] | None = None
    for line in reversed(completed.stdout.splitlines()):
        if not line.startswith(_RESULT_PREFIX):
            continue
        try:
            decoded = json.loads(line[len(_RESULT_PREFIX) :])
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            payload = decoded
            break

    if payload is None:
        stderr = completed.stderr.strip()
        detail = stderr[-1000:] if stderr else f"worker exited with code {completed.returncode}"
        raise PluginMetadataScanError("MetadataWorkerFailed", detail)
    if payload.get("ok") is not True:
        raise PluginMetadataScanError(
            str(payload.get("error_type") or "MetadataScanFailed"),
            str(payload.get("message") or "Plugin metadata scan failed"),
        )

    entries_obj = payload.get("entries_preview")
    handlers_obj = payload.get("handlers")
    methods_obj = payload.get("entry_methods")
    entries_preview = [dict(item) for item in entries_obj if isinstance(item, dict)] if isinstance(entries_obj, list) else []
    handlers = (
        {str(key): dict(value) for key, value in handlers_obj.items() if isinstance(value, dict)}
        if isinstance(handlers_obj, dict)
        else {}
    )
    invalid_handler_keys = [
        key
        for key in handlers
        if not _handler_key_belongs_to_plugin(key, plugin_id)
    ]
    if invalid_handler_keys:
        raise PluginMetadataScanError(
            "InvalidMetadataResult",
            f"Metadata worker returned handler keys outside plugin '{plugin_id}'",
        )
    entry_methods = (
        {str(key): str(value) for key, value in methods_obj.items()}
        if isinstance(methods_obj, dict)
        else {}
    )
    return IsolatedPluginMetadata(
        entries_preview=entries_preview,
        handlers=handlers,
        entry_methods=entry_methods,
    )


def install_isolated_plugin_metadata(
    plugin_id: str,
    metadata: IsolatedPluginMetadata,
) -> None:
    prefix_dot = f"{plugin_id}."
    prefix_colon = f"{plugin_id}:"
    reconstructed: dict[str, EventHandler] = {}
    base_fields = {
        "event_type",
        "id",
        "name",
        "description",
        "input_schema",
        "kind",
        "auto_start",
        "enabled",
        "dynamic",
        "metadata",
    }

    for key, raw_meta in metadata.handlers.items():
        if not _handler_key_belongs_to_plugin(key, plugin_id):
            continue
        event_meta = EventMeta(
            event_type=str(raw_meta.get("event_type") or "plugin_entry"),
            id=str(raw_meta.get("id") or ""),
            name=raw_meta.get("name", ""),  # type: ignore[arg-type]
            description=raw_meta.get("description", ""),  # type: ignore[arg-type]
            input_schema=(
                raw_meta.get("input_schema")
                if isinstance(raw_meta.get("input_schema"), dict)
                else None
            ),
            kind=str(raw_meta.get("kind") or "action"),  # type: ignore[arg-type]
            auto_start=bool(raw_meta.get("auto_start", False)),
            enabled=bool(raw_meta.get("enabled", True)),
            dynamic=bool(raw_meta.get("dynamic", False)),
            metadata=(
                raw_meta.get("metadata")
                if isinstance(raw_meta.get("metadata"), dict)
                else None
            ),
        )
        for field_name, value in raw_meta.items():
            if field_name not in base_fields:
                setattr(event_meta, field_name, value)
        reconstructed[key] = EventHandler(
            meta=event_meta,
            handler=lambda *_args, **_kwargs: None,
        )

    with state.acquire_event_handlers_write_lock():
        runtime_handlers: dict[str, EventHandler] = {}
        for key, handler in state.event_handlers.items():
            if not (key.startswith(prefix_dot) or key.startswith(prefix_colon)):
                continue
            event_meta = getattr(handler, "meta", None)
            handler_metadata = getattr(event_meta, "metadata", None)
            if (
                getattr(event_meta, "dynamic", False) is True
                and isinstance(handler_metadata, dict)
                and handler_metadata.get("_dynamic") is True
                and handler_metadata.get("_registered_via_ipc") is True
            ):
                runtime_handlers[key] = handler
        for key in list(state.event_handlers):
            if key.startswith(prefix_dot) or key.startswith(prefix_colon):
                del state.event_handlers[key]
        state.event_handlers.update(reconstructed)
        # The host may have received ENTRY_UPDATE registrations while the
        # isolated worker was scanning static metadata. Runtime state wins on
        # collisions because it reflects the live plugin process.
        state.event_handlers.update(runtime_handlers)

    for key in list(registry_module.plugin_entry_method_map):
        if key[0] == plugin_id:
            del registry_module.plugin_entry_method_map[key]
    for entry_id, method_name in metadata.entry_methods.items():
        registry_module.plugin_entry_method_map[(plugin_id, entry_id)] = method_name
    state.invalidate_snapshot_cache("handlers")
