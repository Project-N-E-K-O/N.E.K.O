"""
Plugin log service.

Reads and queries plugin and server logs.
Supports live WebSocket log updates.

Historical lesson: this module inferred log paths from BUILTIN_PLUGIN_CONFIG_ROOT.parent.parent / \"log\",
crashing with AppImage's read-only squashfs plugin/ package.
Now obtain actual Documents/N.E.K.O/logs/ paths through
utils.logger_config.RobustLoggerConfig(service_name=...), never relative-path inference.
The maintainer emphatically forbids adding loguru / cwd-based fallback again.
"""
import re
import asyncio
import threading
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from plugin.logging_config import get_logger

from plugin.settings import BUILTIN_PLUGIN_CONFIG_ROOT


logger = get_logger("server.logs")

_RUNTIME_ERRORS = (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError)

# Special server-log ID.
SERVER_LOG_ID = "_server"


def _parse_log_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    candidates = (stripped, stripped.replace("T", " "))
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(stripped, fmt)
        except ValueError:
            continue
    return None


def _validate_plugin_id(plugin_id: str) -> None:
    """
    Validate safe plugin_id against path traversal.
    
    Args:
        plugin_id: Plugin ID.
    
    Raises:
        HTTPException: Unsafe plugin_id.
    """
    # Permit the special server-log ID.
    if plugin_id == SERVER_LOG_ID:
        return
    
    # Validate plugin_id contains safe characters only, preventing traversal.
    if not re.match(r'^[a-zA-Z0-9_-]+$', plugin_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_id: '{plugin_id}'. Only alphanumeric characters, underscores, and hyphens are allowed."
        )
    
    # Also reject path-traversal symbols.
    if '..' in plugin_id or '/' in plugin_id or '\\' in plugin_id:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plugin_id: '{plugin_id}'. Path traversal characters are not allowed."
        )


def _resolve_robust_log_dir(service_name: str, log_subdir: str | None = None) -> Path:
    """Get persisted log directories from the host's RobustLoggerConfig.

    AppImage puts plugin packages on read-only squashfs; never derive paths from cwd / __file__.
    RobustLoggerConfig chooses writable directories through five fallback levels (Documents -> AppData -> home
    -> temp).

    Match writer log_subdir: plugin subprocesses write logs/plugin/,
    while the main server writes logs/.
    """
    try:
        from utils.logger_config import RobustLoggerConfig
    except (ImportError, ModuleNotFoundError):
        import importlib.util

        project_root = BUILTIN_PLUGIN_CONFIG_ROOT.parent.parent
        logger_config_path = project_root / "utils" / "logger_config.py"
        spec = importlib.util.spec_from_file_location("utils.logger_config", logger_config_path)
        if spec is None or spec.loader is None:
            raise
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        RobustLoggerConfig = getattr(mod, "RobustLoggerConfig")
    config = RobustLoggerConfig(service_name=service_name, log_subdir=log_subdir)
    log_dir = Path(config.get_log_directory_path())
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_plugin_log_dir(plugin_id: str) -> Path:
    """
    Get a plugin's log directory.

    Storage layers remain symmetric with writers in plugin/core/host.py and plugin/logging_config.py:
    Directory layout:

      - Server logs: ``<docs>/N.E.K.O/logs/N.E.K.O_PluginServer_YYYYMMDD.log``.
      - Plugin logs: ``<docs>/N.E.K.O/logs/plugin/N.E.K.O_Plugin_{id}_YYYYMMDD.log``.

    Args:
        plugin_id: Plugin ID, or SERVER_LOG_ID for server logs.

    Returns:
        Log-directory path.

    Raises:
        HTTPException: Unsafe plugin_id containing traversal characters.
    """
    _validate_plugin_id(plugin_id)

    if plugin_id == SERVER_LOG_ID:
        service_name = "PluginServer"
        log_subdir: str | None = None
    else:
        service_name = f"Plugin_{plugin_id}"
        log_subdir = "plugin"
    try:
        return _resolve_robust_log_dir(service_name, log_subdir=log_subdir)
    except HTTPException:
        raise
    except (
        ImportError,  # _resolve_robust_log_dir first imports utils.logger_config;
        ModuleNotFoundError,  # these two import exceptions previously escaped _RUNTIME_ERRORS.
        *_RUNTIME_ERRORS,
    ) as exc:
        # Final fallback: temporary directory, never plugin/ package paths on read-only squashfs.
        # Include log_subdir in fallback paths too, preserving reader/writer symmetry; otherwise
        # writers use .../logs/plugin/ while this reader reads
        # .../neko/logs/, leaving the frontend empty.
        logger.warning(f"Failed to resolve robust log dir for {service_name}: {exc}; falling back to temp")
        import tempfile
        fallback_dir = Path(tempfile.gettempdir()) / "neko" / "logs"
        if log_subdir:
            fallback_dir = fallback_dir / log_subdir
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir


def _is_error_log_file(name: str) -> bool:
    """Determine whether a filename belongs to RobustLoggerConfig's _error.log family.

    RobustLoggerConfig opens separate N.E.K.O_<Service>_error.log files and rotated
    _error.log.1 / _error.log.2025-04-21 variants as permanent error streams.
    Their mtime may be newer than today's ordinary log because the latest error just occurred,
    causing latest-file logic to choose error logs and leave tail/WebSocket feeds
    with only errors, losing all ordinary INFO/DEBUG entries.

    Every latest-log-file entry point must exclude error files with this helper.
    Listing get_plugin_log_files does not exclude them, allowing users to see every file.
    """
    # _error.log or rotated suffixes (_error.log.1 / _error.log.2025-04-21 ...).
    return "_error.log" in name


def _list_plugin_log_files_for_tail(log_dir: Path, plugin_id: str) -> list[Path]:
    """Return ordinary plugin logs in descending mtime order, excluding _error.log families.

    Shared by get_plugin_logs / LogFileWatcher, replacing six independent globs
    requiring separate fixes.

    End patterns with *: RotatingFileHandler creates suffixes such as
    N.E.K.O_<Service>_YYYYMMDD.log.1 / .log.2026-04-21; matching
    *.log only returns empty when only rotated files remain, falsely reporting no logs
    to tail/WebSockets. _is_error_log_file already recognizes _error.log.1 variants.
    """
    if plugin_id == SERVER_LOG_ID:
        pattern = "N.E.K.O_PluginServer_*.log*"
    else:
        pattern = f"N.E.K.O_Plugin_{plugin_id}_*.log*"

    files = [p for p in log_dir.glob(pattern) if not _is_error_log_file(p.name)]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files


def get_plugin_log_files(plugin_id: str) -> list[dict[str, object]]:
    """
    Get the plugin's log-file list.
    
    Args:
        plugin_id: Plugin ID, or SERVER_LOG_ID for server logs.
    
    Returns:
        List of log files.
    """
    log_dir = get_plugin_log_dir(plugin_id)
    
    if not log_dir.exists():
        return []
    
    log_files = []
    
    # RobustLoggerConfig controls names: N.E.K.O_{ServiceName}_YYYYMMDD.log.
    if plugin_id == SERVER_LOG_ID:
        pattern = "N.E.K.O_PluginServer_*.log*"
    else:
        pattern = f"N.E.K.O_Plugin_{plugin_id}_*.log*"
    
    for log_file in log_dir.glob(pattern):
        try:
            stat = log_file.stat()
            log_files.append({
                "filename": log_file.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
            })
        except OSError:
            continue
    
    # Sort by modification time, newest first.
    log_files.sort(key=lambda x: x["modified"], reverse=True)
    return log_files


def list_plugin_log_files_for_export(log_dir: Path, plugin_id: str) -> list[Path]:
    """Return all files belonging to this plugin for packaged export.

    Unlike _list_plugin_log_files_for_tail, exports retain _error.log
    families, often essential for diagnosis. Trailing * includes rotation suffixes.

    Notes:
    1. Sanitize plugin_id first, matching actual log-writing IDs.
    2. Match exact plugin boundaries so foo cannot include foo_bar logs.
    """
    from plugin.core.host import _sanitize_plugin_id
    import re

    # Sanitize plugin_id first, matching log writes.
    safe_id = _sanitize_plugin_id(plugin_id, max_len=64)

    if plugin_id == SERVER_LOG_ID:
        pattern = "N.E.K.O_PluginServer_*.log*"
    else:
        # Match exact plugin boundaries: underscore must precede an eight-digit YYYYMMDD date or error.log.
        # Match: N.E.K.O_Plugin_foo_20260906.log, N.E.K.O_Plugin_foo_error.log.1.
        # Reject: N.E.K.O_Plugin_foo_bar_20260906.log.
        pattern = f"N.E.K.O_Plugin_{safe_id}_*.log*"

    candidates = [p for p in log_dir.glob(pattern) if p.is_file()]

    # Secondary filtering requires a date/error after underscore, preventing prefix false matches.
    if plugin_id != SERVER_LOG_ID:
        # Match N.E.K.O_Plugin_{safe_id}_{YYYYMMDD}.log[.rotation_suffix]
        # or    N.E.K.O_Plugin_{safe_id}_error.log[.rotation_suffix].
        # RotatingFileHandler uses numeric .1, .2, ... suffixes;
        # TimedRotatingFileHandler uses .YYYY-MM-DD dates.
        expected_prefix = f"N.E.K.O_Plugin_{safe_id}_"
        # YYYYMMDD.log or YYYYMMDD.log.1 or YYYYMMDD.log.2026-04-21.
        date_pattern = re.compile(r"^\d{8}\.log(?:\.\d+|\.\d{4}-\d{2}-\d{2})?$")
        # error.log or error.log.1 or error.log.2026-04-21.
        error_pattern = re.compile(r"^error\.log(?:\.\d+|\.\d{4}-\d{2}-\d{2})?$")

        filtered = []
        for p in candidates:
            # Extract the part after prefix.
            if not p.name.startswith(expected_prefix):
                continue
            suffix = p.name[len(expected_prefix):]
            # Require YYYYMMDD.log* or error.log*.
            if date_pattern.match(suffix) or error_pattern.match(suffix):
                filtered.append(p)

        return filtered

    return candidates


def get_plugin_log_directory_path(plugin_id: str) -> str:
    """
    Get the absolute plugin-log directory.

    Args:
        plugin_id: Plugin ID, or SERVER_LOG_ID for server logs.

    Returns:
        Absolute log-directory path string.

    Raises:
        HTTPException: Unsafe plugin_id.
    """
    log_dir = get_plugin_log_dir(plugin_id)
    return str(log_dir.resolve())


def parse_log_line(line: str) -> dict[str, object] | None:
    """
    Parse a log line.
    
    Support several formats:
    1. Plugin: 2024-01-01 00:00:00 - [plugin.xxx] - INFO - file.py:123 - message.
    2. Server: 2024-01-01 00:00:00,123 - user_plugin_server - INFO - message.
    3. Standard: 2024-01-01 00:00:00 - INFO - message.
    4. Pipes: 2024-01-01 00:00:00 | INFO | module:function:123 | message.
    """
    line = line.strip()
    if not line:
        return None
    
    # Pattern 1, plugin: 2024-01-01 00:00:00 - [plugin.xxx] - INFO - file.py:123 - message.
    pattern1 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - \[([^\]]+)\] - (\w+) - ([^:]+):(\d+) - (.+)'
    match = re.match(pattern1, line)
    if match:
        timestamp, name, level, file, line_num, message = match.groups()
        return {
            "timestamp": timestamp,
            "level": level,
            "file": file,
            "line": int(line_num),
            "message": message
        }
    
    # Pattern 2, server: 2024-01-01 00:00:00,123 - user_plugin_server - INFO - message.
    pattern2 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) - ([^-]+) - (\w+) - (.+)'
    match = re.match(pattern2, line)
    if match:
        timestamp, name, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": name.strip(),
            "line": 0,
            "message": message.strip()
        }
    
    # Pattern 3, standard: 2024-01-01 00:00:00 - INFO - message.
    pattern3 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d+)?) - (\w+) - (.+)'
    match = re.match(pattern3, line)
    if match:
        timestamp, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": "",
            "line": 0,
            "message": message.strip()
        }

    pattern3b = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(.+)'
    match = re.match(pattern3b, line)
    if match:
        timestamp, level, message = match.groups()
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": "",
            "line": 0,
            "message": message.strip(),
        }
    
    # Pattern 4, pipes: 2024-01-01 00:00:00 | INFO | module:function:123 | message.
    # Supported shape: timestamp | level | location | message.
    # location may be module:function:line, module:function, or module.
    pattern4 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*([^|]+)\s*\|\s*(.+)'
    match = re.match(pattern4, line)
    if match:
        timestamp, level, location, message = match.groups()
        # Parse location as module:function:line, module:function, or module.
        location_parts = location.strip().split(':')
        if len(location_parts) >= 3:
            # module:function:line
            file = location_parts[0]
            line_num = int(location_parts[-1]) if location_parts[-1].isdigit() else 0
        elif len(location_parts) == 2:
            # module:function
            file = location_parts[0]
            line_num = 0
        else:
            # module
            file = location_parts[0] if location_parts else ""
            line_num = 0
        
        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": file.strip(),
            "line": line_num,
            "message": message.strip()
        }

    # Pattern 5, loguru pipes: 2024-01-01 00:00:00 | INFO | [Proc-xxx] message.
    # Supported shape: timestamp | level | message.
    pattern5 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*(.+)'
    match = re.match(pattern5, line)
    if match:
        timestamp, level, message = match.groups()
        message = message.strip()

        file = ""
        # Try extracting loguru prefixes such as [Proc-xxx] / [Plugin-xxx].
        prefix_match = re.match(r'^\[([^\]]+)\]\s*(.*)$', message)
        if prefix_match:
            file, message = prefix_match.groups()

        return {
            "timestamp": timestamp.strip(),
            "level": level.strip(),
            "file": (file or "").strip(),
            "line": 0,
            "message": (message or "").strip()
        }

    pattern6 = r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.(\d+))?\s*\|\s*(\w+)\s*\|\s*([^|]+?)\s*-\s*(.+)'
    match = re.match(pattern6, line)
    if match:
        ts, ms, level, location, message = match.groups()
        timestamp = ts.strip()
        if isinstance(ms, str) and ms:
            timestamp = f"{timestamp}.{ms}"
        location = (location or "").strip()
        message = (message or "").strip()
        file = ""
        line_num = 0
        try:
            parts = location.split(":")
            if len(parts) >= 3 and parts[-1].isdigit():
                line_num = int(parts[-1])
                file = ":".join(parts[:-2]) if len(parts) > 3 else parts[0]
            else:
                file = parts[0] if parts else location
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
            file = location
            line_num = 0
        return {
            "timestamp": timestamp,
            "level": level.strip(),
            "file": file.strip(),
            "line": line_num,
            "message": message,
        }
    
    # Unmatched formats return the original line.
    return {
        "timestamp": "",
        "level": "UNKNOWN",
        "file": "",
        "line": 0,
        "message": line
    }


def read_log_file_tail(log_file: Path, lines: int = 100) -> list[dict[str, object]]:
    """
    Read the last N lines of a log file.
    
    Args:
        log_file: Log-file path.
        lines: Number of lines to read.
    
    Returns:
        Parsed log-entry list.
    """
    if not log_file.exists():
        return []
    
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # Efficiently read the last N lines with deque.
            tail_lines = deque(maxlen=lines)
            for line in f:
                tail_lines.append(line)
            
            # Parse log lines.
            parsed_logs = []
            for line in tail_lines:
                log_entry = parse_log_line(line)
                if log_entry:
                    parsed_logs.append(log_entry)
            
            return parsed_logs
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
        logger.exception(f"Failed to read log file {log_file}")
        return []


def filter_logs(
    logs: list[dict[str, object]],
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    search: str | None = None
) -> list[dict[str, object]]:
    """
    Filter logs.
    
    Args:
        logs: Log list.
        level: Log-level filter.
        start_time: Start time, ISO format.
        end_time: End time, ISO format.
        search: Keyword search.
    
    Returns:
        Filtered log list.
    """
    filtered = logs
    
    # Filter by level.
    if level:
        level_upper = level.upper()
        filtered = [log for log in filtered if log.get("level") == level_upper]
    
    # Filter by time.
    if start_time or end_time:
        start_dt = _parse_log_time(start_time) if isinstance(start_time, str) else None
        end_dt = _parse_log_time(end_time) if isinstance(end_time, str) else None
        if start_dt is not None or end_dt is not None:
            time_filtered: list[dict[str, object]] = []
            for log in filtered:
                ts = _parse_log_time(log.get("timestamp"))
                if ts is None:
                    continue
                if start_dt is not None and ts < start_dt:
                    continue
                if end_dt is not None and ts > end_dt:
                    continue
                time_filtered.append(log)
            filtered = time_filtered
    
    # Keyword search.
    if search:
        search_lower = search.lower()
        filtered = [
            log for log in filtered
            if search_lower in log.get("message", "").lower()
        ]
    
    return filtered


def get_plugin_logs(
    plugin_id: str,
    lines: int = 100,
    level: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    search: str | None = None
) -> dict[str, object]:
    """
    Get plugin or server logs.
    
    Args:
        plugin_id: Plugin ID, or SERVER_LOG_ID for server logs.
        lines: Number of returned lines.
        level: Log-level filter.
        start_time: Start time.
        end_time: End time.
        search: Keyword search.
    
    Returns:
        Log data.
    """
    log_dir = get_plugin_log_dir(plugin_id)
    
    if not log_dir.exists():
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0
        }
    
    # Find the latest ordinary log, excluding _error.log files so tail does not read only errors.
    try:
        log_files = _list_plugin_log_files_for_tail(log_dir, plugin_id)
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
        logger.exception(f"Failed to find log files in {log_dir} for plugin {plugin_id}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": "Failed to find log files"
        }

    if not log_files:
        logger.info(f"No regular log files found in {log_dir} for plugin {plugin_id}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0
        }
    
    latest_log = log_files[0]
    
    # Read logs.
    try:
        logs = read_log_file_tail(latest_log, lines)
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
        logger.exception(f"Failed to read log file {latest_log}")
        return {
            "plugin_id": plugin_id,
            "logs": [],
            "total_lines": 0,
            "returned_lines": 0,
            "error": "Failed to read log file"
        }
    
    # Filter.
    filtered_logs = filter_logs(logs, level, start_time, end_time, search)
    
    return {
        "plugin_id": plugin_id,
        "logs": filtered_logs,
        "total_lines": len(logs),
        "returned_lines": len(filtered_logs),
        "log_file": latest_log.name
    }


# ========== Live WebSocket log delivery ==========

# Global log watchers: {plugin_id: LogFileWatcher}.
_log_watchers: dict[str, "LogFileWatcher"] = {}
# Thread lock protecting _log_watchers during concurrent async access.
_log_watchers_lock = threading.Lock()


def read_log_file_incremental(log_file: Path, last_position: int) -> tuple[list[dict[str, object]], int]:
    """
    Read incremental log content from a specified position.
    
    Args:
        log_file: Log-file path.
        last_position: Previous byte offset.
    
    Returns:
        (New log-entry list, new position).
    """
    if not log_file.exists():
        return [], last_position
    
    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            # Seek to the previous read position.
            f.seek(last_position)
            
            # Read appended content.
            new_lines = f.readlines()
            new_position = f.tell()
            
            # Parse appended log lines.
            new_logs = []
            for line in new_lines:
                log_entry = parse_log_line(line)
                if log_entry:
                    new_logs.append(log_entry)
            
            return new_logs, new_position
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
        logger.exception(f"Failed to read incremental log from {log_file}")
        return [], last_position


class LogFileWatcher:
    """Log-file watcher for live WebSocket delivery."""
    
    def __init__(self, plugin_id: str):
        self.plugin_id = plugin_id
        self.clients: set[WebSocket] = set()
        self.last_position: int = 0
        self.current_log_file: Path | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._running = False
    
    def add_client(self, websocket: WebSocket):
        """Add a WebSocket client."""
        self.clients.add(websocket)
        if not self._running:
            self._start_watching()
    
    def remove_client(self, websocket: WebSocket):
        """Remove a WebSocket client."""
        self.clients.discard(websocket)
        if not self.clients and self._running:
            self._stop_watching()
    
    def _start_watching(self):
        """Start watching log files."""
        if self._running:
            return
        
        self._running = True
        self._watch_task = asyncio.create_task(self._watch_loop())
    
    def _stop_watching(self):
        """Stop watching log files."""
        self._running = False
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None
    
    async def _watch_loop(self):
        """Watch loop: periodically inspect file changes and deliver new logs."""
        while self._running:
            try:
                # Get the latest log, excluding _error.log so streams do not contain errors only.
                log_dir = get_plugin_log_dir(self.plugin_id)
                log_files = _list_plugin_log_files_for_tail(log_dir, self.plugin_id)

                if not log_files:
                    await asyncio.sleep(1)  # No log files: wait.
                    continue
                
                latest_log = log_files[0]
                
                # Reset position when the log file changes.
                if self.current_log_file != latest_log:
                    self.current_log_file = latest_log
                    self.last_position = 0
                
                # Read incremental logs.
                new_logs, new_position = read_log_file_incremental(
                    latest_log, self.last_position
                )
                
                if new_logs:
                    self.last_position = new_position
                    # Push new logs to all clients.
                    await self._broadcast_logs(new_logs)
                
                # Wait 0.5 seconds before checking again.
                await asyncio.sleep(0.5)
                
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
                logger.exception(f"Error in log watcher loop for {self.plugin_id}")
                await asyncio.sleep(1)
    
    async def _broadcast_logs(self, logs: list[dict[str, object]]):
        """Broadcast logs to every connected client."""
        if not logs or not self.clients:
            return
        
        disconnected = []
        for client in list(self.clients):
            try:
                # Check connection state.
                if hasattr(client, 'client_state'):
                    from starlette.websockets import WebSocketState
                    if client.client_state != WebSocketState.CONNECTED:
                        disconnected.append(client)
                        continue
                
                await client.send_json({
                    "type": "append",
                    "logs": logs
                })
            except (WebSocketDisconnect, ConnectionError, RuntimeError) as e:
                # Disconnected/closed: record without raising.
                logger.debug(f"Failed to send logs to client (disconnected): {e}")
                disconnected.append(client)
            except (ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError) as e:
                # Other errors: record and mark disconnected.
                logger.debug(f"Failed to send logs to client: {e}")
                disconnected.append(client)
        
        # Remove disconnected clients.
        for client in disconnected:
            self.clients.discard(client)
    
    async def send_initial_logs(self, websocket: WebSocket, lines: int = 100):
        """Send initial logs, the last N lines."""
        try:
            result = get_plugin_logs(self.plugin_id, lines=lines)
            await websocket.send_json({
                "type": "initial",
                "logs": result.get("logs", []),
                "log_file": result.get("log_file"),
                "total_lines": result.get("total_lines", 0)
            })
            
            # Track current log file/position, excluding _error.log like _watch_loop / get_plugin_logs.
            log_dir = get_plugin_log_dir(self.plugin_id)
            log_files = _list_plugin_log_files_for_tail(log_dir, self.plugin_id)

            if log_files:
                self.current_log_file = log_files[0]
                # Start at the current file size.
                self.last_position = self.current_log_file.stat().st_size
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
            logger.exception("Failed to send initial logs")


async def log_stream_endpoint(websocket: WebSocket, plugin_id: str):
    """
    WebSocket endpoint streaming logs live.
    
    Args:
        websocket: WebSocket connection.
        plugin_id: Plugin ID, or SERVER_LOG_ID for server logs.
    """
    await websocket.accept()
    
    # Get/create a watcher under lock to avoid concurrency races.
    with _log_watchers_lock:
        if plugin_id not in _log_watchers:
            _log_watchers[plugin_id] = LogFileWatcher(plugin_id)
        watcher = _log_watchers[plugin_id]
    
    watcher.add_client(websocket)
    
    try:
        # Send initial logs.
        await watcher.send_initial_logs(websocket, lines=100)
        
        # Keep the connection open, optionally awaiting client messages.
        while True:
            try:
                # Receive client messages such as filter changes.
                _ = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Could process client messages to update filters.
                # Currently ignore them.
            except asyncio.TimeoutError:
                # Send heartbeat keepalives.
                try:
                    await websocket.send_json({"type": "ping"})
                except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError):
                    # Failed sends may mean closure; exit the loop.
                    break
            except WebSocketDisconnect:
                break
            except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError) as e:
                # Log other exceptions and exit.
                logger.debug(f"WebSocket receive error for {plugin_id}: {e}")
                break
    except WebSocketDisconnect:
        pass
    except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError) as e:
        # Record without raising so finally still runs.
        logger.debug(f"Error in log stream endpoint for {plugin_id}: {e}")
    finally:
        # Ensure client-connection cleanup.
        try:
            watcher.remove_client(websocket)
            # With no clients left, clean up the watcher under lock.
            with _log_watchers_lock:
                if not watcher.clients:
                    _log_watchers.pop(plugin_id, None)
        except (RuntimeError, ValueError, TypeError, AttributeError, KeyError, OSError, TimeoutError) as e:
            logger.debug(f"Error cleaning up watcher for {plugin_id}: {e}")
