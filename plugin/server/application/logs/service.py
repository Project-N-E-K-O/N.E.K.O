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
from utils.logging_layout import is_service_log_file, plugin_log_service_name, resolve_log_directory

from .text import filter_logs, parse_log_line

SERVER_LOG_ID = "_server"


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


@dataclass(frozen=True)
class LogReadResult:
    """Read data independent of HTTP fields or WebSocket message types."""

    plugin_id: str
    logs: list[dict[str, object]]
    total_lines: int
    returned_lines: int
    log_file: str | None = None
    legacy_source: bool = False
    reset: bool = False


class LogService:
    """Public log operations; transport adapters do not select or read files."""

    def _directory(self, plugin_id: str) -> Path:
        _validate_target(plugin_id)
        return resolve_log_directory(log_subdir=None if plugin_id == SERVER_LOG_ID else "plugin")

    def _files(self, plugin_id: str) -> list[_LogFile]:
        directory = self._directory(plugin_id)
        service = "PluginServer" if plugin_id == SERVER_LOG_ID else plugin_log_service_name(plugin_id)
        sources = [(service, False)]
        # Old embedded servers used the shared Plugin identity. Preserve access
        # as explicitly labelled server history, never as an individual plugin.
        if plugin_id == SERVER_LOG_ID:
            sources.append(("Plugin", True))
        files: list[_LogFile] = []
        for service_name, legacy in sources:
            for path in directory.glob("*.log*"):
                if path.is_file() and is_service_log_file(path.name, service_name):
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
    ) -> LogReadResult:
        return self.open_session(plugin_id, lines, level, start_time, end_time, search).snapshot()

    def get_plugin_log_files(self, plugin_id: str) -> list[dict[str, object]]:
        return [{"filename": item.path.name, "size": item.path.stat().st_size,
                  "modified": item.path.stat().st_mtime, "legacy_source": item.legacy}
                 for item in self._files(plugin_id)]

    def get_plugin_log_directory(self, plugin_id: str) -> Path:
        return self._directory(plugin_id).resolve()

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

    def __init__(self, service: LogService, plugin_id: str, lines: int,
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

    def _result(self, logs: list[dict[str, object]], *, reset: bool = False) -> LogReadResult:
        return LogReadResult(
            plugin_id=self.plugin_id, logs=logs, total_lines=self.total_lines,
            returned_lines=self.returned_lines,
            log_file=self.current.path.name if self.current else None,
            legacy_source=self.current.legacy if self.current else False, reset=reset,
        )

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

    def snapshot(self, *, reset: bool = False) -> LogReadResult:
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
        return self._result(logs, reset=reset)

    def poll(self) -> LogReadResult | None:
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
        return self._result(logs) if logs else None
