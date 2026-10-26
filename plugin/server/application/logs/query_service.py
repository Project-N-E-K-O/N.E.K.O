"""File-backed log application: one ownership rule for queries, streams and exports.

Reading never configures logging or creates directories. A subscription owns its
cursor; taking another snapshot cannot change an existing subscription's progress.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
import os
import re
from typing import BinaryIO

from plugin.server.domain.errors import ServerDomainError
from plugin.utils.time_utils import now_iso
from utils.logging_layout import plugin_log_service_name, resolve_log_directory

from .text import filter_logs, parse_log_line

SERVER_LOG_ID = "_server"
_FILE_SUFFIX = re.compile(r"(?:\d{8}|error)\.log(?:\.\d+|\.\d{4}-\d{2}-\d{2})?\Z")


def _validate_target(plugin_id: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", plugin_id):
        raise ServerDomainError(
            code="INVALID_PLUGIN_ID", status_code=400,
            message="Plugin ID must contain only letters, numbers, underscores or hyphens",
            details={"plugin_id": plugin_id},
        )


@dataclass(frozen=True)
class _LogFile:
    path: Path
    legacy: bool = False


class LogQueryService:
    """Public log operations; transport adapters do not select or read files."""

    def _directory(self, plugin_id: str) -> Path:
        _validate_target(plugin_id)
        return resolve_log_directory(log_subdir=None if plugin_id == SERVER_LOG_ID else "plugin")

    def _files(self, plugin_id: str) -> list[_LogFile]:
        directory = self._directory(plugin_id)
        service = "PluginServer" if plugin_id == SERVER_LOG_ID else plugin_log_service_name(plugin_id)
        prefixes = [(f"N.E.K.O_{service}_", False)]
        # Old embedded servers used the shared Plugin identity. Preserve access
        # as explicitly labelled server history, never as an individual plugin.
        if plugin_id == SERVER_LOG_ID:
            prefixes.append(("N.E.K.O_Plugin_", True))
        files: list[_LogFile] = []
        for prefix, legacy in prefixes:
            for path in directory.glob(f"{prefix}*.log*"):
                if path.is_file() and _FILE_SUFFIX.fullmatch(path.name[len(prefix):]):
                    files.append(_LogFile(path, legacy))
        files.sort(key=lambda item: (item.path.stat().st_mtime_ns, item.path.name), reverse=True)
        return files

    def _latest(self, plugin_id: str) -> _LogFile | None:
        ordinary = [item for item in self._files(plugin_id) if "_error.log" not in item.path.name]
        # Once canonical server files exist, shared historical files must not
        # displace the active server log merely because another process writes.
        canonical = [item for item in ordinary if not item.legacy]
        choices = canonical or ordinary
        # Rotation copies may have newer mtimes. Prefer the active daily file.
        active = [item for item in choices if item.path.name.endswith(".log")]
        return next(iter(active or choices), None)

    def get_plugin_logs(
        self, *, plugin_id: str, lines: int = 100, level: str | None = None,
        start_time: str | None = None, end_time: str | None = None,
        search: str | None = None,
    ) -> dict[str, object]:
        result = self.open_session(plugin_id, lines, level, start_time, end_time, search).snapshot()
        return {key: value for key, value in result.items() if key not in ("type", "reset")}

    def get_plugin_log_files(self, plugin_id: str) -> dict[str, object]:
        files = [{"filename": item.path.name, "size": item.path.stat().st_size,
                  "modified": item.path.stat().st_mtime, "legacy_source": item.legacy}
                 for item in self._files(plugin_id)]
        return {"plugin_id": plugin_id, "log_files": files, "count": len(files), "time": now_iso()}

    def get_plugin_log_directory(self, plugin_id: str) -> dict[str, object]:
        return {"plugin_id": plugin_id, "directory": str(self._directory(plugin_id).resolve()), "time": now_iso()}

    def files_for_export(self, plugin_id: str) -> list[Path]:
        return [item.path for item in self._files(plugin_id)]

    def open_session(
        self, plugin_id: str, lines: int = 100, level: str | None = None,
        start_time: str | None = None, end_time: str | None = None,
        search: str | None = None,
    ) -> LogSession:
        _validate_target(plugin_id)
        if not 1 <= lines <= 10000:
            raise ServerDomainError(code="INVALID_LOG_QUERY", status_code=400,
                                    message="Log line count must be between 1 and 10000")
        return LogSession(self, plugin_id, lines, level, start_time, end_time, search)


class LogSession:
    """A snapshot and its subsequent reads share one privately owned cursor."""

    def __init__(self, service: LogQueryService, plugin_id: str, lines: int,
                 level: str | None, start_time: str | None, end_time: str | None,
                 search: str | None):
        self.service = service
        self.plugin_id = plugin_id
        self.lines = lines
        self.filters = (level, start_time, end_time, search)
        self.current: _LogFile | None = None
        self.identity: tuple[int, int] | None = None
        self.position = 0
        self.anchor = b""
        self.total_lines = 0
        self.returned_lines = 0

    def _metadata(self) -> dict[str, object]:
        result: dict[str, object] = {"plugin_id": self.plugin_id,
                                    "total_lines": self.total_lines,
                                    "returned_lines": self.returned_lines}
        if self.current:
            result.update(log_file=self.current.path.name, legacy_source=self.current.legacy)
        return result

    def _read(self, stream: BinaryIO, *, snapshot: bool) -> list[dict[str, object]]:
        assert self.identity is not None
        entries: deque[dict[str, object]] = deque(maxlen=self.lines if snapshot else None)
        if not snapshot:
            stream.seek(self.position)
        while True:
            offset = stream.tell()
            raw = stream.readline()
            if not raw or not raw.endswith(b"\n"):
                # Do not consume a half-written line, including split UTF-8.
                stream.seek(offset)
                break
            entry = parse_log_line(raw.decode("utf-8", errors="replace"))
            if entry is not None:
                entry["id"] = f"{self.identity[0]}:{self.identity[1]}:{offset}"
                entries.append(entry)
        self.position = stream.tell()
        stream.seek(max(0, self.position - 64))
        self.anchor = stream.read(self.position - stream.tell())
        result = list(entries)
        self.total_lines = len(result) if snapshot else self.total_lines + len(result)
        filtered = filter_logs(result, *self.filters)
        self.returned_lines = len(filtered) if snapshot else self.returned_lines + len(filtered)
        return filtered

    def snapshot(self, *, reset: bool = False) -> dict[str, object]:
        self.current = self.service._latest(self.plugin_id)
        self.identity = None
        self.position = 0
        self.anchor = b""
        self.total_lines = self.returned_lines = 0
        logs: list[dict[str, object]] = []
        if self.current:
            with self.current.path.open("rb") as stream:
                info = os.fstat(stream.fileno())
                self.identity = (info.st_dev, info.st_ino)
                logs = self._read(stream, snapshot=True)
        packet = {"type": "initial", "logs": logs, **self._metadata()}
        if reset:
            packet["reset"] = True
        return packet

    def poll(self) -> dict[str, object] | None:
        latest = self.service._latest(self.plugin_id)
        if latest != self.current:
            return self.snapshot(reset=True)
        if latest is None:
            return None
        with latest.path.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if (info.st_dev, info.st_ino) != self.identity or info.st_size < self.position:
                return self.snapshot(reset=True)
            stream.seek(max(0, self.position - len(self.anchor)))
            if stream.read(len(self.anchor)) != self.anchor:
                return self.snapshot(reset=True)
            logs = self._read(stream, snapshot=False)
        return {"type": "append", "logs": logs, **self._metadata()} if logs else None
