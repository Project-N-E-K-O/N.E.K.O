"""Read a plugin's metadata by importing its entry class in a throwaway worker.

Reading metadata means executing an untrusted plugin's module-level code: it
may raise, block forever, spawn helpers, or leak descriptors and threads. Doing
that inside the agent process would take the host down with it, so the import
runs in a subprocess we can time out and kill along with everything it spawned,
and only a JSON result crosses back.
"""

from __future__ import annotations

import json
import math
import os
import queue
import selectors
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Mapping

import psutil

from plugin._types.events import EventHandler, EventMeta
from plugin.core import registry as registry_module
from plugin.core.state import state


_RESULT_PREFIX = "NEKO_PLUGIN_METADATA_RESULT:"
_MAX_METADATA_RESULT_BYTES = 1024 * 1024
_PROCESS_CLEANUP_TIMEOUT = 0.5
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


# 单个插件扫描的上限。
#
# 从 30s 降下来：实测本机 17 个真实插件里最慢的 1.41s、中位 0.97s，所以 10s 仍有
# 约 7 倍余量，冷盘或杀软首次逐个扫解释器时也够。30s 的问题是它乘以插件数——
# 一个卡住的插件能把整轮 discovery 拖到分钟级，而前端只等 30s。
#
# 注意单项上限本身不足以封顶：17 个插件按 5 并发是 4 波，4×10s 仍然超前端预算。
# 真正封顶的是 registry_service 那边的总预算，这里只负责让单个坏插件早点放手。
# Env: NEKO_PLUGIN_METADATA_SCAN_TIMEOUT
from plugin.server.application.plugins._env_budgets import env_int, env_seconds

_DEFAULT_SCAN_TIMEOUT_SECONDS = env_seconds("NEKO_PLUGIN_METADATA_SCAN_TIMEOUT", 10.0)

# 同时最多允许多少个元数据解释器活着。
#
# 每轮 discovery 各建各的线程池，池的 max_workers 只管住"这一次刷新"。刷新路由
# 之间没有串行化，force 又绕过缓存，于是连点几下刷新、失败重试、或几个调用方撞
# 在一起，就能同时拉起 请求数 x workers 个解释器；单个实测常驻约 66 MB，几次重叠
# 足以把内存吃干、把插件服务器带走（codex）。
#
# 闸设在真正起进程的这一层，而不是线程池那一层：除了 discovery 之外，单插件刷新
# 和生命周期路径也会各自扫描，池上的上限管不到它们。
# Env: NEKO_PLUGIN_METADATA_SCAN_CONCURRENCY
MAX_CONCURRENT_METADATA_SCANS = env_int("NEKO_PLUGIN_METADATA_SCAN_CONCURRENCY", 8)
_SCAN_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_METADATA_SCANS)
# 等槽位等到这个数以内，就当这个插件基本拿满了自己的扫描窗口。
_SLOT_WAIT_IS_NEGLIGIBLE = 0.05


def _metadata_worker_command() -> list[str]:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return [sys.executable, "--neko-plugin-metadata-worker"]
    return [sys.executable, "-c", _WORKER_BOOTSTRAP]


def _handler_key_belongs_to_plugin(key: str, plugin_id: str) -> bool:
    return key.startswith(f"{plugin_id}.") or key.startswith(f"{plugin_id}:")


def _terminate_processes(processes: list[psutil.Process]) -> None:
    for process in processes:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    try:
        _, alive = psutil.wait_procs(
            processes,
            timeout=_PROCESS_CLEANUP_TIMEOUT,
        )
    except (psutil.Error, OSError, RuntimeError, ValueError):
        alive = processes
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    if alive:
        try:
            psutil.wait_procs(alive, timeout=_PROCESS_CLEANUP_TIMEOUT)
        except (psutil.Error, OSError, RuntimeError, ValueError):
            pass


def _terminate_worker_tree(
    process: subprocess.Popen[bytes],
    cleanup_lock: threading.RLock | None = None,
) -> None:
    lock = cleanup_lock or threading.RLock()
    with lock:
        if process.returncode is not None:
            return
        try:
            parent = psutil.Process(process.pid)
        except (psutil.Error, OSError, RuntimeError, ValueError):
            parent = None
            descendants = []
        else:
            try:
                descendants = parent.children(recursive=True)
            except (psutil.Error, OSError, RuntimeError, ValueError):
                descendants = []

        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass

        processes = [*descendants, parent] if parent is not None else descendants
        _terminate_processes(processes)

        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                # 进程在 poll() 和 kill() 之间自己退了，或者句柄已经无效——
                # 目的（它不再运行）已经达成，没有可补救的。
                pass


def _terminate_and_reap_worker(
    process: subprocess.Popen[bytes],
    cleanup_lock: threading.RLock,
) -> None:
    with cleanup_lock:
        _terminate_worker_tree(process, cleanup_lock)
        try:
            process.wait(timeout=_PROCESS_CLEANUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                # 同理：已经 kill 过的进程通常立刻可收，但这一步无界等待没有
                # 任何东西护着，收不掉就放手，别把关停卡在这儿。
                process.wait(timeout=_PROCESS_CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass


_STDERR_TAIL_BYTES = 1000
_STDERR_READ_TIMEOUT_SECONDS = 2.0


def _read_worker_stderr(process: subprocess.Popen[bytes]) -> str:
    """Read whatever diagnostics the worker left, without trusting the pipe.

    ``stream.read(n)`` on a pipe returns only at n bytes or EOF, and EOF needs
    every write handle closed — including any a grandchild inherited. Today the
    worker redirects fd 2 to devnull before it imports plugin code, in both the
    ``-c`` bootstrap and the frozen ``--neko-plugin-metadata-worker`` path, so
    plugin-spawned processes never hold this pipe and the read returns promptly
    (verified against a plugin that spawns a 30 s child at import: 0.75 s).

    That makes this read safe by an invariant maintained in two other places,
    which is a thin thing for an unbounded blocking call to rest on — and it
    sits after every timeout timer has been cancelled, so nothing would
    interrupt it. Bounded here instead: a background thread, and after the
    deadline we give up on the diagnostics rather than on the scan.
    """
    stream = process.stderr
    if stream is None:
        return ""

    collected: list[bytes] = []

    def _drain() -> None:
        try:
            collected.append(stream.read(_STDERR_TAIL_BYTES))
        except Exception:  # noqa: BLE001 - diagnostics only
            pass

    reader = threading.Thread(target=_drain, daemon=True, name="plugin-scan-stderr")
    reader.start()
    reader.join(timeout=_STDERR_READ_TIMEOUT_SECONDS)
    if reader.is_alive() or not collected:
        return ""
    return collected[0].decode("utf-8", errors="replace")


def _read_protocol_output_blocking(stream: BinaryIO) -> tuple[bytes, bool]:
    output = bytearray()
    result_prefix = _RESULT_PREFIX.encode("utf-8")
    while len(output) <= _MAX_METADATA_RESULT_BYTES:
        chunk = stream.readline(
            _MAX_METADATA_RESULT_BYTES + 1 - len(output)
        )
        if not chunk:
            break
        output.extend(chunk)
        if chunk.startswith(result_prefix):
            break
    return bytes(output), len(output) > _MAX_METADATA_RESULT_BYTES


def _read_protocol_output(
    stream: BinaryIO,
    *,
    timeout_event: threading.Event | None = None,
) -> tuple[bytes, bool]:
    """Read the worker protocol without letting an inherited fd defeat timeout.

    POSIX pipes are polled directly so a detached descendant holding the write
    end open cannot leave this process blocked in ``readline()``.  The fallback
    covers streams without a selectable descriptor (including Windows pipes)
    with a daemon reader and keeps the caller bounded by ``timeout_event``.
    """
    if timeout_event is None:
        return _read_protocol_output_blocking(stream)

    if os.name != "nt":
        try:
            fd = stream.fileno()
            selector = selectors.DefaultSelector()
            selector.register(fd, selectors.EVENT_READ)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        else:
            output = bytearray()
            result_prefix = _RESULT_PREFIX.encode("utf-8")
            line_start = 0
            try:
                while len(output) <= _MAX_METADATA_RESULT_BYTES:
                    if timeout_event.is_set():
                        raise TimeoutError("metadata protocol read timed out")
                    if not selector.select(timeout=0.05):
                        continue
                    chunk = os.read(
                        fd,
                        _MAX_METADATA_RESULT_BYTES + 1 - len(output),
                    )
                    if not chunk:
                        break
                    output.extend(chunk)
                    while True:
                        line_end = output.find(b"\n", line_start)
                        if line_end < 0:
                            break
                        line = output[line_start:line_end]
                        if line.endswith(b"\r"):
                            line = line[:-1]
                        line_start = line_end + 1
                        if line.startswith(result_prefix):
                            return bytes(output[:line_start]), False
                return bytes(output), len(output) > _MAX_METADATA_RESULT_BYTES
            finally:
                selector.close()

    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def _blocking_reader() -> None:
        try:
            result_queue.put((True, _read_protocol_output_blocking(stream)))
        except BaseException as exc:
            result_queue.put((False, exc))

    reader = threading.Thread(
        target=_blocking_reader,
        name="plugin-metadata-protocol-reader",
        daemon=True,
    )
    reader.start()
    while True:
        try:
            succeeded, result = result_queue.get(timeout=0.05)
        except queue.Empty:
            if timeout_event.is_set():
                raise TimeoutError("metadata protocol read timed out")
            continue
        if succeeded:
            return result  # type: ignore[return-value]
        raise result  # type: ignore[misc]


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
    # Imports stay inside the worker: importing an untrusted plugin module runs
    # its module-level code, which must not be able to crash, hang or leak in
    # the agent/plugin-server process.
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
    raw_read = os.read
    raw_write = os.write
    immediate_exit = os._exit
    trusted_json_dumps = json.dumps
    result_prefix = _RESULT_PREFIX
    max_result_bytes = _MAX_METADATA_RESULT_BYTES
    control_fd = raw_dup(sys.stdin.fileno())
    main_module = sys.modules.get("__main__")
    if main_module is not None:
        vars(main_module).pop("_protocol_fd", None)
    if protocol_fd is None:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
        protocol_fd = raw_dup(stdout_fd)
        devnull_fd = raw_open(os.devnull, os.O_WRONLY)
        raw_dup2(devnull_fd, stdout_fd)
        raw_dup2(devnull_fd, stderr_fd)
        raw_close(devnull_fd)
    try:
        request_obj = json.loads(sys.stdin.readline())
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
    try:
        raw_read(control_fd, 1)
    except OSError:
        pass
    raw_close(control_fd)
    immediate_exit(0)


def _scan_plugin_metadata_uncached(
    *,
    plugin_id: str,
    module_path: str,
    class_name: str,
    config_path: Path,
    conf: Mapping[str, object],
    pdata: Mapping[str, object],
    python_requirement_paths: list[Path] | tuple[Path, ...] = (),
    timeout: float = _DEFAULT_SCAN_TIMEOUT_SECONDS,
) -> IsolatedPluginMetadata:
    if timeout <= 0:
        # 总预算已经用完：连进程都不要起。调用方拿到的是和"扫描超时"同一种
        # 错误，于是插件照样出现在列表里（标成扫描失败），而不是整批中断。
        raise PluginMetadataScanError(
            "ScanBudgetExhausted",
            "Plugin metadata scan skipped: discovery time budget exhausted",
        )
    request = {
        "plugin_id": plugin_id,
        "module_path": module_path,
        "class_name": class_name,
        "config_path": str(config_path),
        "conf": _json_safe(conf),
        "pdata": _json_safe(pdata),
        "python_requirement_paths": [str(path) for path in python_requirement_paths],
    }
    project_root = Path(__file__).resolve().parents[4]

    popen_kwargs: dict[str, object] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(
            _metadata_worker_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(project_root),
            **popen_kwargs,
        )
    except OSError as exc:
        raise PluginMetadataScanError(type(exc).__name__, str(exc)) from exc

    timed_out = threading.Event()
    cleanup_lock = threading.RLock()

    def _expire_worker() -> None:
        timed_out.set()
        _terminate_worker_tree(process, cleanup_lock)

    timeout_timer = threading.Timer(timeout, _expire_worker)
    timeout_timer.daemon = True
    timeout_timer.start()
    try:
        if process.stdin is None or process.stdout is None:
            raise OSError("metadata worker pipes are unavailable")
        process.stdin.write(
            (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
        )
        process.stdin.flush()
        stdout_bytes, output_too_large = _read_protocol_output(
            process.stdout,
            timeout_event=timed_out,
        )
        timeout_timer.cancel()
        _terminate_and_reap_worker(process, cleanup_lock)
    except (OSError, TimeoutError) as exc:
        timeout_timer.cancel()
        _terminate_and_reap_worker(process, cleanup_lock)
        if timed_out.is_set():
            raise PluginMetadataScanError(
                "TimeoutExpired",
                f"Plugin metadata scan timed out after {timeout:g}s",
            ) from exc
        raise PluginMetadataScanError(type(exc).__name__, str(exc)) from exc
    finally:
        timeout_timer.cancel()
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass

    if timed_out.is_set():
        raise PluginMetadataScanError(
            "TimeoutExpired",
            f"Plugin metadata scan timed out after {timeout:g}s",
        )
    if output_too_large:
        raise PluginMetadataScanError(
            "MetadataResultTooLarge",
            "Plugin metadata worker output exceeds the "
            f"{_MAX_METADATA_RESULT_BYTES}-byte protocol limit",
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = _read_worker_stderr(process)

    payload: dict[str, object] | None = None
    for line in reversed(stdout.splitlines()):
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
        stderr = stderr.strip()
        detail = stderr[-1000:] if stderr else f"worker exited with code {process.returncode}"
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


# ── 扫描结果缓存 ────────────────────────────────────────────────────────
#
# 一次扫描是一个全新解释器（本机实测约 0.84s，其中约 0.76s 只是启动和导入扫描
# 框架），而算出缓存键只要遍历插件目录 stat 一遍——实测 17 个插件合计约 17ms，
# 差三个数量级。
#
# 键包含插件目录下所有 *.py / *.toml / *.json 的 (mtime_ns, size)，而不是只看
# plugin.toml 和入口文件：插件常把代码拆到同目录别的模块里，只盯入口会在改了
# 邻居文件之后命中脏缓存——那种 bug 很难联想到缓存。
#
# ⚠️ 目录外的依赖（共享的 vendor/、site-packages）变化抓不到。所以凡是"内容可能
# 在我们背后变了"的路径——安装、升级、卸载、以及用户手点的刷新——必须传
# force=True 绕过，不能指望键自己发现。
# 值是 (结果, 这份结果是不是 force 扫出来的)。第二项承重：一次在 force 扫描期间
# 开始的普通扫描会捕获同一个代次，所以代次比较放它过去；但两个子进程 import
# 外部依赖的先后是不确定的，普通那次完全可能读到更旧的依赖却最后落地
# （codex）。force 的结果不接受普通扫描覆盖。
_SCAN_CACHE: dict[tuple, tuple[IsolatedPluginMetadata, bool]] = {}
_SCAN_CACHE_LOCK = threading.Lock()
_SCAN_CACHE_MAX_ENTRIES = 256
# 指纹忽略的目录名。除此之外插件目录下的**所有**文件都进指纹。
#
# 原本只看 .py/.toml/.json，但插件的模块级代码常常从同目录的数据文件派生条目
# （metadata.yaml、csv、模板……），只盯代码文件会在那些文件改了之后命中脏缓存，
# 而注册的元数据和运行时行为对不上是最难查的一类不一致（codex）。
_SCAN_KEY_IGNORED_DIRS = frozenset({"__pycache__", ".git", ".mypy_cache", ".ruff_cache"})

# 刚被动过的插件不写缓存，要"安定"这么久之后才写。
#
# (mtime_ns, size) 漏掉的唯一情形是"写入后极短时间内再次等大小改写"——两次落在
# 同一个文件系统时间戳刻度里，指纹看不出变化。CodeRabbit 建议给每个文件加内容
# 摘要，但实测读+哈希全部插件文件要 359ms（810 个文件 / 20.9MB），而仅 stat 是
# 47ms；热缓存路径现在总共才 0.14s，加这一笔等于把最该快的那条路慢 3.5 倍。
#
# 换个方向从源头关掉这个窗口：只在所有被指纹的文件都已经"老"到不可能发生
# 同刻度改写时才写缓存。刚改过的插件这一轮照常扫、只是不缓存，安定之后自然
# 开始命中。代价是零额外 I/O。
#
# 2s 不是随手取的，它必须 >= 文件系统时间戳粒度 G。设写入缓存的时刻为 T、被指纹
# 文件的 mtime 为 M，安定条件是 M <= T - S。之后一次改写发生在 T' > T，它落在
# 刻度 floor(T'/G)；要让指纹看不出变化就得 floor(T'/G) == floor(M/G)，也就是
# T' < M + G <= T - S + G。与 T' > T 联立得到 G > S。所以只要 S >= G，"同刻度
# 等大小改写"这个窗口在**写入**那一刻就不存在，缓存里也就不会先躺进一条已经过时
# 的条目（CodeRabbit 追问的正是这一条）。NTFS 的 G 是 100ns，FAT/exFAT 最粗，
# 是 2s——所以下界取 2s。
_CACHE_SETTLE_NS = 2_000_000_000
_NEVER_SETTLED = -1

# 两道"这份结果还算数吗"的闸，管的是两件不同的事。
#
# 起因：普通扫描和 force 扫描算出来的键完全一样——键只看插件目录，而 force 存在
# 的意义正是目录外的变化（共享 vendor、site-packages）。于是一次开始得早、结束得
# 晚的普通扫描能把 force 刚写进去的新结果盖回旧的，之后每次普通读取都拿到那份
# 陈旧元数据，恰好废掉 force 的语义（CodeRabbit）。
#
# ① 按键的 generation，只有 force 会 +1。
#
#    先前这里用的是一个全局计数，那是错的，而且错得比原问题更糟：一次
#    refresh_registry(force=True) 会并发地强扫十几个插件，每个 worker 都把全局
#    计数 +1，于是除了最后一个之外每个 worker 在写缓存前都看到"代不一样"，把
#    自己刚读出来的新结果全部丢掉。又因为 force 不删旧条目，那些旧条目原封不动
#    留在缓存里，下一次普通刷新照样把它们端出来——整次强制刷新等于没做（codex）。
#    按键分代之后，强扫兄弟插件之间互不干扰。
#
# ② 全局 epoch，只有显式清缓存会 +1。
#
#    清缓存的语义就是"缓存里的东西现在都不可信"，让所有在途结果一起作废正是想
#    要的效果；而它是低频的单次调用，不存在①那种自相残杀。放一个全局计数还能
#    盖住"这个键我们从没见过"的在途扫描——按键的表里根本没有它的条目。
_SCAN_GENERATION: dict[tuple, int] = {}
_SCAN_EPOCH = 0
# 缓存被显式清掉过几次。只增不减，注册表那边拿它对账。
_SCAN_CACHE_CLEAR_COUNT = 0


def _bump_scan_epoch_locked() -> None:
    """Invalidate every scan currently in flight. Caller holds the cache lock."""
    global _SCAN_EPOCH

    _SCAN_EPOCH += 1


def _make_room_for_locked(key: tuple) -> None:
    """Enforce the cache cap before adding a new key. Caller holds the lock.

    容量检查必须跟"写进 _SCAN_CACHE"这件事绑在一起，而不是只挂在成功那条路上：
    墓碑也是条目，而且键里带着整份文件清单。连着几次 force 扫描失败、每次都是新的
    源指纹，缓存就会一路涨过上限（CodeRabbit）。
    """
    if key in _SCAN_CACHE or len(_SCAN_CACHE) < _SCAN_CACHE_MAX_ENTRIES:
        return
    _SCAN_CACHE.clear()
    # 清表会把别人赖以判断的那条 force 记录一起扔掉，所以在途结果一并作废。
    _bump_scan_epoch_locked()


def _begin_scan(key: tuple, *, force: bool) -> tuple[int, int]:
    """Stamp a scan about to run, and drop what ``force`` says is untrustworthy."""
    global _SCAN_EPOCH

    with _SCAN_CACHE_LOCK:
        generation = _SCAN_GENERATION.get(key, 0)
        if force:
            generation += 1
            if len(_SCAN_GENERATION) >= _SCAN_CACHE_MAX_ENTRIES:
                # 丢掉按键代次，就是丢掉在途扫描的判据：一个在清表前记下 gen=0 的
                # 陈旧扫描，清表之后再读还是 0，校验反而放它过去，恰好把这道闸自己
                # 打开（CodeRabbit）。所以清表的同时用 epoch 把在途结果一起作废——
                # 这跟显式清缓存是同一件事：按键判据不再可靠了。
                _SCAN_GENERATION.clear()
                _SCAN_EPOCH += 1
            _SCAN_GENERATION[key] = generation
            # force 的意思是"缓存里那个答案不可信"，所以现在就把它作废，而不是
            # 等着用新结果覆盖。旧条目留着的话，后面一次普通读取会把它端出来，这次
            # force 就白做了（codex）。
            #
            # 作废的方式是留一块墓碑 (None, True)，不是 pop：
            #   * 读取侧把 None 当未命中，所以普通扫描照常真扫，行为跟删掉一样；
            #   * 写入侧那条「普通扫描不覆盖 force 的结果」现成的判据，因此对这块
            #     墓碑也生效——force 万一超时或抛错、一条结果都没写成，同代次的普通
            #     扫描也不能把它读到的（可能是变更前依赖的）结果填进这个坑
            #     （codex）。
            # 不用再引一张在途表，就是复用已有的 (结果, 是不是 force) 这个形状。
            _make_room_for_locked(key)
            _SCAN_CACHE[key] = (None, True)
        return generation, _SCAN_EPOCH


def _scan_with_slot(**kwargs: Any) -> IsolatedPluginMetadata:
    """Run one uncached scan while holding a global interpreter slot.

    Waiting for a slot spends the caller's own timeout, so a saturated server
    degrades the way an over-budget discovery already does — the plugin is
    recorded as scan-failed and retried on the next refresh — instead of
    queueing more interpreters than the machine can hold.
    """
    timeout = float(kwargs.get("timeout", _DEFAULT_SCAN_TIMEOUT_SECONDS))
    if timeout <= 0:
        # 预算已经没了：不占槽位，让扫描器自己抛 ScanBudgetExhausted。
        return _scan_plugin_metadata_uncached(**kwargs)
    started = time.monotonic()
    if not _SCAN_SLOTS.acquire(timeout=timeout):
        raise PluginMetadataScanError(
            "ScanBudgetExhausted",
            "Plugin metadata scan skipped: no scan slot became free in time",
        )
    try:
        waited = time.monotonic() - started
        kwargs["timeout"] = timeout - waited
        try:
            return _scan_plugin_metadata_uncached(**kwargs)
        except PluginMetadataScanError as exc:
            if exc.error_type != "TimeoutExpired" or waited <= _SLOT_WAIT_IS_NEGLIGIBLE:
                raise
            # 等槽位吃掉了这个插件本该拥有的扫描时间，它是在被削短的窗口里超时的。
            # 报成 TimeoutExpired 的话，上游会当成"这个插件自己的导入卡住了"，把一个
            # 健康插件标成 failed 并取消它的自启动资格——而真正的原因是服务器当时
            # 忙（codex）。改报预算类失败，语义才对得上。
            raise PluginMetadataScanError(
                "ScanBudgetExhausted",
                "Plugin metadata scan timed out after waiting "
                f"{waited:.1f}s for a scan slot",
            ) from exc
    finally:
        _SCAN_SLOTS.release()


def _plugin_source_fingerprint(config_path: Path) -> tuple[tuple, int]:
    """``(指纹, 最新 mtime_ns)``，取自插件目录下的全部文件。

    自顶向下走，并且**在下降之前**就把忽略目录剪掉。``rglob("*")`` 做不到这件
    事：它会先把 ``.git`` 底下每一个后代都枚举出来、对每一个调用 ``is_file()``，
    然后才轮到那句忽略判断。开发机上的插件目录带一个大 object database 时，每一
    次"命中缓存"都要先花上几秒走一遍明确说了不看的文件，而这段时间不在 discovery
    的扫描预算里（codex）。
    """
    root = config_path.parent
    entries: list[tuple[str, int, int]] = []
    newest = 0
    unreadable = False
    # 插件根目录本身就可能是软链。scandir 会跟着它进去，里面一个软链都看不到，
    # 于是整棵树照常进缓存——而把根重新指向另一份代码，键一点都不会变
    # （CodeRabbit）。所以先问根自己。
    # 软链要进指纹本身，不能只影响"安不安定"。
    #
    # 只把 newest 设成 _NEVER_SETTLED 的话，指纹跟一棵没有软链的树**一模一样**：
    # 如果这棵树在软链被加进来之前就已经缓存过，键没变，读取侧直接命中那条旧记录
    # 就返回了，根本走不到"不可缓存"那条路，陈旧的条目可以一直留着（codex）。
    # 给每条软链放一个哨兵条目，加一条链必然让键变化，旧条目自然失效。
    symlinks: list[tuple[str, int, int]] = []
    if os.path.islink(root):
        symlinks.append(("<symlink>:.", 0, 0))
    saw_symlink = bool(symlinks)

    # 用 scandir 手写下降，而不是 os.walk + Path.stat。
    #
    # 一次 scandir 拿回来的 DirEntry 自带类型和（Windows 上）stat 信息，所以
    # is_symlink()/is_dir()/stat() 都不用再多一次 syscall。os.walk 只给名字，判断
    # 软链就得对每个文件再来一次 lstat——在这条本来就是为了变快而存在的路径上，
    # 那是凭空多出一倍的系统调用。
    stack = [str(root)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as scan:
                children = list(scan)
        except OSError:
            # 目录读不了就整棵树不可缓存，让这次扫描照常进行。
            unreadable = True
            continue
        for entry in children:
            try:
                # 软链（目录的和文件的都算）一律让这棵树不进缓存。
                #
                # 目录软链：os.walk 不跟，rglob 当年也不跟，所以软链后面那棵树从来
                # 就不在指纹里。文件软链更隐蔽：stat() 会跟过去，只记下目标的
                # mtime/size，于是把链重新指向另一个同样大小、同样时间戳的文件
                # （带时间戳拷贝的版本化文件就是这个形状）之后，键一点没变，而
                # Python 已经在 import 另一份代码了（codex）。
                #
                # 跟进去不行：软链可能指向 site-packages 那种巨树，也可能成环，而
                # 这个遍历就在热路径上。选另一头——不进缓存。这类插件每次都真扫，
                # 只是慢，不会错。
                if entry.is_symlink():
                    saw_symlink = True
                    symlinks.append(
                        ("<symlink>:" + os.path.relpath(entry.path, root), 0, 0)
                    )
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in _SCAN_KEY_IGNORED_DIRS:
                        stack.append(entry.path)
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            entries.append(
                (os.path.relpath(entry.path, root), st.st_mtime_ns, st.st_size)
            )
            newest = max(newest, st.st_mtime_ns)

    # 排序放在最后：这样指纹不依赖下降顺序，栈序换了也不会平白让缓存失效。
    entries.extend(symlinks)
    entries.sort()
    if unreadable:
        return (("<unreadable>", 0, 0),), _NEVER_SETTLED
    if saw_symlink:
        # 键照常带上看得见的那部分（不同插件仍然分得开），但永远不"安定"，
        # 也就永远不进缓存——没有条目写进去，就不可能有陈旧命中。
        return tuple(entries), _NEVER_SETTLED
    return tuple(entries), newest


def _scan_cache_key(
    *,
    plugin_id: str,
    module_path: str,
    class_name: str,
    config_path: Path,
    conf: Mapping[str, object],
    pdata: Mapping[str, object],
    python_requirement_paths: list[Path] | tuple[Path, ...],
) -> tuple:
    return (
        plugin_id,
        module_path,
        class_name,
        str(config_path),
        tuple(sorted(str(p) for p in python_requirement_paths)),
        json.dumps(_json_safe(dict(conf)), sort_keys=True, ensure_ascii=False),
        json.dumps(_json_safe(dict(pdata)), sort_keys=True, ensure_ascii=False),
        _plugin_source_fingerprint(config_path)[0],
    )


def scan_cache_clear_count() -> int:
    """How many times the cache has been declared untrustworthy.

    注册表那边用它来判断"我开始扫之后，有没有人动过盘"。清缓存的调用方恰好就是
    那几条改盘的事务（卸载、替换、换源），而它们都在插件操作锁里跑，刷新路由却不
    进那把锁——所以这是两者之间唯一现成的信号。
    """
    with _SCAN_CACHE_LOCK:
        return _SCAN_CACHE_CLEAR_COUNT


def clear_plugin_metadata_scan_cache() -> None:
    """Drop every cached scan, and invalidate the ones still in flight.

    There used to be a ``config_path`` parameter here for the single-plugin
    refresh. It never got a production caller: that refresh is expressed as
    ``force``, and a forced scan already evicts its own key in
    :func:`_begin_scan`. A scoped branch nothing takes is not a spare tyre, it
    is untested code that a guard test made look covered — and its path
    matching was unnormalized, so it would not have matched reliably anyway.
    """
    global _SCAN_CACHE_CLEAR_COUNT

    with _SCAN_CACHE_LOCK:
        # 同时作废在途的扫描：它们是在清缓存**之前**读的盘，写回去等于把刚被
        # 明确宣布过时的内容又放回缓存。
        _bump_scan_epoch_locked()
        _SCAN_CACHE.clear()
        _SCAN_CACHE_CLEAR_COUNT += 1


def scan_plugin_metadata_isolated(
    *,
    plugin_id: str,
    module_path: str,
    class_name: str,
    config_path: Path,
    conf: Mapping[str, object],
    pdata: Mapping[str, object],
    python_requirement_paths: list[Path] | tuple[Path, ...] = (),
    timeout: float = _DEFAULT_SCAN_TIMEOUT_SECONDS,
    force: bool = False,
) -> IsolatedPluginMetadata:
    """Read one plugin's metadata in a throwaway worker, memoised on content.

    ``force=True`` bypasses and refreshes the entry. Failures are deliberately
    NOT cached: a scan that timed out or blew the budget must be retried on the
    next refresh rather than sticking to the plugin until something on disk
    changes.
    """
    key = _scan_cache_key(
        plugin_id=plugin_id,
        module_path=module_path,
        class_name=class_name,
        config_path=config_path,
        conf=conf,
        pdata=pdata,
        python_requirement_paths=python_requirement_paths,
    )
    if not force:
        with _SCAN_CACHE_LOCK:
            hit = _SCAN_CACHE.get(key)
        if hit is not None and hit[0] is not None:
            result_only, _ = hit
            # 先于预算判断：这个插件没变过，答案早就在手上，不花任何时间。反过来
            # 做的话，一批改动过的插件把预算耗光之后，排在后面的健康插件会被标成
            # 扫描失败——注册表里它的元数据被覆盖成 failed，还可能因此失去自启动
            # 资格，而它其实什么都没发生（codex）。
            return result_only

    # 盖章要排在预算判断**前面**。放在后面的话，一次预算已经见底的 force 会在下面
    # 那条 return 上直接走掉，它宣布不可信的那条旧条目原封不动留在缓存里，下一次
    # 普通读取照样把它端出来——force 的意思是"缓存里那个答案不可信"，这个意思跟
    # 这一刻还有没有时间扫无关。宁可让缓存变冷，也不能留着一条已经被宣布过时的。
    generation, epoch = _begin_scan(key, force=force)

    if timeout <= 0:
        # 缓存里没有，预算也没了：这条路不写缓存——"现在没时间"描述的是此刻，
        # 不是"这个插件是什么"。
        return _scan_with_slot(
            plugin_id=plugin_id,
            module_path=module_path,
            class_name=class_name,
            config_path=config_path,
            conf=conf,
            pdata=pdata,
            python_requirement_paths=python_requirement_paths,
            timeout=timeout,
        )

    result = _scan_with_slot(
        plugin_id=plugin_id,
        module_path=module_path,
        class_name=class_name,
        config_path=config_path,
        conf=conf,
        pdata=pdata,
        python_requirement_paths=python_requirement_paths,
        timeout=timeout,
    )
    _, newest_mtime_ns = _plugin_source_fingerprint(config_path)
    settled = (
        newest_mtime_ns != _NEVER_SETTLED
        and time.time_ns() - newest_mtime_ns > _CACHE_SETTLE_NS
    )
    if settled:
        with _SCAN_CACHE_LOCK:
            if _SCAN_GENERATION.get(key, 0) != generation or _SCAN_EPOCH != epoch:
                # 这一轮开始之后，同一个键又被 force 扫过，或者缓存被整个清了。
                # 手上这份是照着旧内容读出来的，写回去就是拿陈旧结果盖掉更新的
                # 那份。
                return result
            existing = _SCAN_CACHE.get(key)
            if existing is not None and existing[1] and not force:
                # 这一格里躺着的是 force 扫出来的结果。我们是普通扫描，代次虽然对得
                # 上，但两个子进程读外部依赖的先后无法保证——不覆盖它。
                return result
            # 清表会把别人赖以判断的那条 force 记录一起扔掉：一个共享同一代次的
            # 普通扫描随后就看不到"这里躺着 force 的结果"，于是把自己读到的更旧的
            # 依赖写进来（codex）。所以 _make_room_for_locked 里连带作废在途结果。
            # 我们自己已经过了检查，不受影响。
            _make_room_for_locked(key)
            _SCAN_CACHE[key] = (result, force)
    elif force:
        with _SCAN_CACHE_LOCK:
            # 这次 force 成功了，只是文件太新、按安定窗口不该进缓存。墓碑的使命到
            # 此为止：留着它，后面每一次普通扫描都会被"不覆盖 force 的结果"挡住，
            # 这个插件就**永远**不再进缓存了——而"改完插件顺手点一下刷新"恰好是最
            # 常见的操作（codex）。
            #
            # 只清我们自己这一代的墓碑：代次或 epoch 变了说明又有别人接手了，那块
            # 墓碑不归我们处理。
            if (
                _SCAN_GENERATION.get(key, 0) == generation
                and _SCAN_EPOCH == epoch
                and _SCAN_CACHE.get(key) == (None, True)
            ):
                _SCAN_CACHE.pop(key, None)
    return result
