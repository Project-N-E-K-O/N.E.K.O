# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Session-private retained results sharing one anonymous backing file."""

from __future__ import annotations

import io
import tempfile
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OutputScope:
    receipt: str
    retained: bool = False


OUTPUT_SCOPE: ContextVar[OutputScope | None] = ContextVar("output_scope", default=None)


class OutputSpool:
    def __init__(self, directory: Path):
        self.file = tempfile.TemporaryFile(dir=directory)
        self.lock = threading.Lock()

    def close(self) -> None:
        with self.lock:
            self.file.close()


class _OutputReader(io.RawIOBase):
    def __init__(self, spool: OutputSpool, spans):
        self.spool = spool
        self.spans = iter(spans)
        self.offset = self.left = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        view = memoryview(buffer)
        written = 0
        with self.spool.lock:
            if self.spool.file.closed:
                raise OSError("The retained output was closed")
            while written < len(view):
                if not self.left:
                    self.offset, self.left = next(self.spans, (0, 0))
                if not self.left:
                    break
                self.spool.file.seek(self.offset)
                size = self.spool.file.readinto(view[written:written + self.left])
                if not size:
                    raise OSError("Incomplete retained output")
                self.offset += size
                self.left -= size
                written += size
        return written


class ProtectedOutput:
    """Only the owning session can resolve this result and its original authorization."""

    def __init__(self, spool: OutputSpool, origin: str):
        self.origin = origin
        self.spool = spool
        self.spans: list[tuple[int, int]] = []

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self.spool.lock:
            if self.spool.file.closed:
                raise OSError("The retained output was closed")
            start = self.spool.file.seek(0, io.SEEK_END)
            size = self.spool.file.write(data)
            if self.spans and sum(self.spans[-1]) == start:
                previous, length = self.spans[-1]
                self.spans[-1] = previous, length + size
            else:
                self.spans.append((start, size))

    def reader(self):
        with self.spool.lock:
            return io.BufferedReader(_OutputReader(self.spool, tuple(self.spans)))
