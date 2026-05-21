from __future__ import annotations

import asyncio
import logging
import queue
import re
import subprocess
import sys
import threading
from typing import Any

from .models import GalgameConfig
from ._types import MEMORY_READER_DEFAULT_ENGINE, TextractorProcessHandle
from ._win32_job_objects import _track_textractor_process, _untrack_textractor_process


_LOGGER = logging.getLogger(__name__)


def _decode_textractor_stdout_line(raw: bytes) -> str:
    payload = bytes(raw or b"").rstrip(b"\r\n")
    if not payload:
        return ""
    candidates = [payload]
    if payload.startswith(b"\x00"):
        candidates.append(payload[1:])
    if len(payload) % 2:
        candidates.append(payload[:-1])
        if payload.startswith(b"\x00"):
            candidates.append(payload[1:-1])
    if b"\x00" in payload:
        for candidate in candidates:
            if not candidate:
                continue
            try:
                text = candidate.decode("utf-16-le", errors="replace")
            except Exception:
                _LOGGER.debug("utf-16-le decode failed for candidate", exc_info=True)
                continue
            cleaned = text.replace("\x00", "").replace("\ufffd", "").strip()
            if cleaned.startswith("[") or cleaned.startswith("Usage") or "]" in cleaned:
                return cleaned
    return payload.decode("utf-8", errors="replace").replace("\x00", "").replace("\ufffd", "").strip()


def _textractor_hook_command(code: str, pid: int) -> str:
    normalized = str(code or "").strip()
    if not normalized:
        return ""
    if re.search(r"(?:^|\s)-P\d+\b", normalized):
        return normalized
    return f"{normalized} -P{int(pid)}"


def _select_hook_codes_for_engine(
    config: GalgameConfig,
    engine: str,
) -> tuple[list[str], str]:
    normalized_engine = str(engine or "").strip().lower() or MEMORY_READER_DEFAULT_ENGINE
    engine_hooks = getattr(config, "memory_reader_engine_hook_codes", {}) or {}
    configured_codes = engine_hooks.get(normalized_engine)
    if configured_codes is not None:
        return list(configured_codes), "hook_codes_sent" if configured_codes else "hook_codes_none"
    legacy_codes = list(getattr(config, "memory_reader_hook_codes", []) or [])
    if not legacy_codes:
        return [], "hook_codes_none"
    if normalized_engine == "unity":
        return legacy_codes, "hook_codes_sent"
    if normalized_engine == MEMORY_READER_DEFAULT_ENGINE:
        return [], "hook_codes_skipped_for_unknown_engine"
    return [], "hook_codes_skipped_for_engine"


class _AsyncioTextractorHandle:
    """Wraps a TextractorCLI subprocess with asyncio-safe I/O.

    Uses synchronous subprocess.Popen so that stdin/stdout are not bound
    to any particular asyncio event loop.  A dedicated reader thread drains
    stdout into a plain ``queue.Queue``; the async ``readline`` method
    pulls from that queue with a timeout via ``asyncio.to_thread``.
    """

    def __init__(self, process: subprocess.Popen, *, logger: Any | None = None) -> None:
        self._process = process
        self._logger = logger
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._start_reader()

    def _start_reader(self) -> None:
        if self._process.stdout is None:
            return

        def _reader():
            try:
                while True:
                    raw = self._process.stdout.readline()
                    if not raw:
                        break
                    line = _decode_textractor_stdout_line(raw)
                    if not line:
                        continue
                    self._queue.put(line)
            except Exception as exc:
                if self._logger is not None:
                    try:
                        self._logger.warning(
                            "memory_reader Textractor stdout reader failed: {}",
                            exc,
                        )
                    except Exception:
                        _LOGGER.warning(
                            "memory_reader Textractor stdout reader failed",
                            exc_info=True,
                        )
                else:
                    _LOGGER.warning(
                        "memory_reader Textractor stdout reader failed",
                        exc_info=True,
                    )
            finally:
                self._queue.put(None)  # sentinel for EOF

        threading.Thread(target=_reader, daemon=True).start()

    async def write(self, payload: str) -> None:
        if self._process.stdin is None:
            raise RuntimeError("textractor stdin is unavailable")
        await asyncio.to_thread(self._process.stdin.write, payload.encode("utf-8"))
        await asyncio.to_thread(self._process.stdin.flush)

    async def readline(self, timeout: float) -> str | None:
        """Read one line. Returns str on success, None on EOF or timeout."""
        try:
            return await asyncio.to_thread(self._queue.get, timeout=timeout)
        except queue.Empty:
            return None

    def poll(self) -> int | None:
        return self._process.poll()

    async def terminate(self) -> None:
        if self._process.poll() is not None:
            _untrack_textractor_process(self._process)
            return
        if self._process.stdin is not None and not self._process.stdin.closed:
            try:
                self._process.stdin.close()
            except Exception:
                pass
        if self._process.poll() is None:
            self._process.terminate()

    async def wait(self, timeout: float) -> int | None:
        try:
            await asyncio.wait_for(asyncio.to_thread(self._process.wait), timeout=timeout)
        except asyncio.TimeoutError:
            if self._process.poll() is None:
                self._process.kill()
                await asyncio.to_thread(self._process.wait)
        returncode = self._process.poll()
        if returncode is not None:
            _untrack_textractor_process(self._process)
        return returncode


async def _default_process_factory(
    path: str,
    *,
    logger: Any | None = None,
) -> TextractorProcessHandle:
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "bufsize": 0,
    }
    if sys.platform.startswith("win"):
        create_no_window = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
        if create_no_window:
            popen_kwargs["creationflags"] = create_no_window
    process = await asyncio.to_thread(
        subprocess.Popen,
        path,
        **popen_kwargs,
    )
    _track_textractor_process(process)
    return _AsyncioTextractorHandle(process, logger=logger)


def _is_event_loop_binding_error(exc: BaseException) -> bool:
    message = str(exc)
    return (
        "bound to a different event loop" in message
        or "attached to a different loop" in message
    )
