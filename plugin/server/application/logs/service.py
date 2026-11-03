"""File-backed log application: one ownership rule for queries, streams and exports.

Reading never configures logging or creates directories. A subscription owns its
cursor; taking another snapshot cannot change an existing subscription's progress.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
import os
import re
from typing import BinaryIO

from plugin.server.domain.errors import ServerDomainError
from plugin.logging_config import resolve_plugin_log_directory
from utils.logging_layout import is_service_log_file
from plugin.logging_layout import plugin_log_service_name

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
    # Growing files remain the same source for an existing session's cursor.
    size: int = field(compare=False)
    modified: float = field(compare=False)
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
        return resolve_plugin_log_directory(log_subdir=None if plugin_id == SERVER_LOG_ID else "plugin")

    def _files(self, plugin_id: str) -> list[_LogFile]:
        directory = self._directory(plugin_id)
        service = "PluginServer" if plugin_id == SERVER_LOG_ID else plugin_log_service_name(plugin_id)
        sources = [(service, False)]
        # Old embedded servers used the shared Plugin identity. Preserve access
        # as explicitly labelled server history, never as an individual plugin.
        if plugin_id == SERVER_LOG_ID:
            sources.append(("Plugin", True))
        files: list[tuple[int, str, _LogFile]] = []
        try:
            entries = os.scandir(directory)
        except (FileNotFoundError, NotADirectoryError):
            return []
        with entries as candidates:
            for entry in candidates:
                for service_name, legacy in sources:
                    # Filter names before any metadata I/O, including files for
                    # other plugins sharing this directory. Scan server history
                    # and canonical files together in one directory traversal.
                    if not is_service_log_file(entry.name, service_name):
                        continue
                    try:
                        if not entry.is_file():
                            break
                        info = entry.stat()
                    except FileNotFoundError:
                        # Rotation can remove an entry after scandir yields it.
                        break
                    files.append((info.st_mtime_ns, entry.name,
                                  _LogFile(Path(entry.path), info.st_size, info.st_mtime, legacy)))
                    break
        files.sort(key=lambda item: item[:2], reverse=True)
        return [item[2] for item in files]

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
        return [{"filename": item.path.name, "size": item.size,
                  "modified": item.modified, "legacy_source": item.legacy}
                 for item in self._files(plugin_id)]

    def get_plugin_log_directory(self, plugin_id: str) -> Path:
        return self._directory(plugin_id).resolve()

    def files_for_export(self, plugin_id: str) -> list[Path]:
        return [item.path for item in self._files(plugin_id)]

    def open_session(
        self, plugin_id: str, lines: int = 100, level: str | None = None,
        start_time: str | None = None, end_time: str | None = None,
        search: str | None = None, *, search_all_fields: bool = False,
    ) -> LogSession:
        _validate_target(plugin_id)
        if not 1 <= lines <= 10000:
            raise ServerDomainError(code="INVALID_LOG_QUERY", status_code=400,
                                    message="Log line count must be between 1 and 10000")
        return LogSession(self, plugin_id, lines, level, start_time, end_time, search,
                          search_all_fields=search_all_fields)


class LogSession:
    """A snapshot and its subsequent reads share one privately owned cursor."""

    def __init__(self, service: LogService, plugin_id: str, lines: int,
                 level: str | None, start_time: str | None, end_time: str | None,
                 search: str | None, *, search_all_fields: bool = False):
        self.service = service
        self.plugin_id = plugin_id
        self.lines = lines
        self.filters = (level, start_time, end_time, search)
        self.search_all_fields = search_all_fields
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

    def _seek_snapshot_start(self, stream: BinaryIO) -> None:
        """Find the last N nonblank completed lines with bounded reverse reads.

        Work follows the requested tail rather than the whole file. Keep only
        a nonblank flag across blocks, so even a huge single line is scanned
        without repeatedly concatenating it. The final unterminated line is
        left for a subsequent poll, just as in incremental reads.
        """
        cursor = stream.seek(0, os.SEEK_END)
        remaining = self.lines
        partial = True
        nonblank = False
        while cursor:
            start = max(0, cursor - 65536)
            stream.seek(start)
            block = stream.read(cursor - start)
            edge = cursor
            parts = block.split(b"\n")
            for part in reversed(parts[1:]):
                edge -= len(part)
                nonblank = nonblank or bool(part.strip())
                if not partial and nonblank:
                    remaining -= 1
                    if not remaining:
                        stream.seek(edge)
                        return
                partial = False
                nonblank = False
                edge -= 1
            nonblank = nonblank or bool(parts[0].strip())
            cursor = start
        stream.seek(0)

    def _read(self, stream: BinaryIO, *, snapshot: bool) -> list[dict[str, object]]:
        assert self.identity is not None
        tail: deque[tuple[int, bytes]] = deque(maxlen=self.lines if snapshot else None)
        if snapshot:
            self._seek_snapshot_start(stream)
        else:
            stream.seek(self.position)
        while True:
            offset = stream.tell()
            raw = stream.readline()
            if not raw or not raw.endswith(b"\n"):
                # Do not consume a half-written line, including split UTF-8.
                stream.seek(offset)
                break
            if raw.strip():
                tail.append((offset, raw))
        self.position = stream.tell()
        stream.seek(max(0, self.position - 64))
        self.anchor = stream.read(self.position - stream.tell())
        # Only retained history needs parsing. The cursor still follows the
        # completed input, so an append cannot replay rows outside the window.
        result: list[dict[str, object]] = []
        for offset, raw in tail:
            entry = parse_log_line(raw.decode("utf-8", errors="replace"))
            if entry is not None:
                entry["id"] = f"{self.identity[0]}:{self.identity[1]}:{offset}"
                result.append(entry)
        self.total_lines = len(result) if snapshot else self.total_lines + len(result)
        filtered = filter_logs(result, *self.filters, search_all_fields=self.search_all_fields)
        self.returned_lines = len(filtered) if snapshot else self.returned_lines + len(filtered)
        return filtered

    def _snapshot_from_stream(self, latest: _LogFile, stream: BinaryIO, *, reset: bool) -> LogReadResult:
        info = os.fstat(stream.fileno())
        self.current = latest
        self.identity = (info.st_dev, info.st_ino)
        return self._result(self._read(stream, snapshot=True), reset=reset)

    def snapshot(self, *, reset: bool = False) -> LogReadResult:
        self.current = None
        self.identity = None
        self.position = 0
        self.anchor = b""
        self.total_lines = self.returned_lines = 0
        # Re-select once if rotation wins the race between scanning and open.
        for _ in range(2):
            latest = self.service._latest(self.plugin_id)
            if latest is None:
                break
            try:
                stream = latest.path.open("rb")
            except FileNotFoundError:
                continue
            with stream:
                return self._snapshot_from_stream(latest, stream, reset=reset)
        return self._result([], reset=reset)

    def poll(self) -> LogReadResult | None:
        latest = self.service._latest(self.plugin_id)
        if latest is None:
            return self.snapshot(reset=True) if self.current is not None else None
        try:
            stream = latest.path.open("rb")
        except FileNotFoundError:
            # Preserve the cursor and let the next poll observe the new file.
            return None
        with stream:
            info = os.fstat(stream.fileno())
            if (latest != self.current or (info.st_dev, info.st_ino) != self.identity
                    or info.st_size < self.position):
                return self._snapshot_from_stream(latest, stream, reset=True)
            stream.seek(max(0, self.position - len(self.anchor)))
            if stream.read(len(self.anchor)) != self.anchor:
                return self._snapshot_from_stream(latest, stream, reset=True)
            logs = self._read(stream, snapshot=False)
        return self._result(logs) if logs else None
