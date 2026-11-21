# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Unlinked backing files for retained results with a host-verified recipient."""

from __future__ import annotations

import io
import tempfile
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OutputScope:
    receipt: str
    retained: bool = False


OUTPUT_SCOPE: ContextVar[OutputScope | None] = ContextVar("output_scope", default=None)


class ProtectedOutput:
    """The public path is a lookup key. Its plaintext is held by this session's file handle."""

    def __init__(self, directory: Path, origin: str):
        self.origin = origin
        self._file = tempfile.TemporaryFile(dir=directory)
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._file.closed:
                raise OSError("The retained output was closed")
            self._file.seek(0, io.SEEK_END)
            self._file.write(data)

    @contextmanager
    def reader(self):
        with self._lock:
            if self._file.closed:
                raise OSError("The retained output was closed")
            self._file.seek(0)
            yield self._file

    def close(self) -> None:
        with self._lock:
            self._file.close()
