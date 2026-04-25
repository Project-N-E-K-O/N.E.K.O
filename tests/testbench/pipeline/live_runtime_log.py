"""Live runtime log tee — server-side crash-safe stdout/stderr mirror.

Problem this solves
-------------------

When the frontend triggers an event cascade that freezes the browser +
pegs CPU / memory (see AGENT_NOTES §4.26 #87 and §4.27 #105 for the two
events), the uvicorn process is still alive on the server side and is
emitting a FLOOD of access-log lines to stdout — 'GET /api/... 200 OK',
'POST /api/diagnostics/errors 201 Created', etc. That flood is exactly
the evidence we need to diagnose the cascade (which URL patterns? what
frequency? from which session?), but it goes nowhere durable:

* ``logger.SessionLogger`` only writes explicit ``log_sync`` calls to
  ``LOGS_DIR/<sid>-YYYYMMDD.jsonl`` — not uvicorn access logs.
* uvicorn's own access log goes to stderr via ``logging.StreamHandler``.
* The PowerShell terminal hosting the process inherits stdout/stderr
  through the 'uv run' pipe, so **once the user force-power-downs the
  machine, the flood is gone** — no post-mortem forensic capability.

This module wires a thin tee on top of ``sys.stdout`` / ``sys.stderr``
at process start (before uvicorn.run()) so **every byte** that would
appear in the terminal also lands in a durable file on disk:

    DATA_DIR/live_runtime/
        current.log     ← this boot's live tail
        previous.log    ← last boot's live tail (rotated at startup)

The file is **line-buffered** and ``fsync`` is opt-in per-line (see
``FSYNC_EACH_LINE`` below). On a cascade event the kernel's page cache
plus our explicit ``flush()`` call is enough for the file to survive a
normal crash; only a hard-power-off within 30 seconds of the last
write may lose the tail — hence the optional per-line ``fsync``.

Design choices
--------------

1. **Bytes-level tee, not ``logging`` handler**. Uvicorn installs its
   own handlers at startup, and various third-party libraries
   (LangChain, openai, httpx) write directly to stdout/stderr. A
   logging handler wouldn't catch them. A stdout/stderr wrapper does.

2. **Size cap + rotation-free**. We don't roll into numbered files
   (*.log.1, *.log.2). Just two files: ``current`` and ``previous``.
   This matches the user's mental model ('clean last run's dump on
   next boot') and avoids any risk of an unbounded log graveyard.

3. **Size cap per file**. When ``current.log`` reaches
   ``MAX_FILE_BYTES`` we keep writing but prepend a one-line rotation
   notice and truncate the head half. This is O(file size) but happens
   rarely (default cap is 64 MB; a typical test session produces 1-5
   MB). Prevents a runaway cascade from filling the whole disk.

4. **UTF-8 with newline='\\n'**. Windows PowerShell's default for
   terminals is UTF-16 on stdout handle; we force the on-disk file to
   UTF-8 regardless of console encoding so the post-mortem is
   portable. Prints to the terminal still go through the original
   encoded stream.

5. **Re-entrancy safe**. If installation is called twice, the second
   call is a no-op. Guarded by module-level ``_installed`` flag.

6. **Boot cleanup rotates**: ``rotate_for_boot()`` moves the existing
   ``current.log`` (if any) to ``previous.log`` (removing the old
   previous first), then opens a fresh ``current.log``. Called once
   from ``run_testbench.main()`` before uvicorn starts.

Usage
-----

::

    from tests.testbench.pipeline import live_runtime_log

    # At process start, before uvicorn.run() and before the banner
    # print so the banner itself lands in the file:
    live_runtime_log.rotate_for_boot()
    live_runtime_log.install()

    # ... normal server run ...

    # Shutdown hook (best-effort; Python will flush on exit anyway):
    live_runtime_log.close()
"""
from __future__ import annotations

import io
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from tests.testbench.config import DATA_DIR

# ── knobs ─────────────────────────────────────────────────────────────

#: Directory holding the live tee files. Not the regular ``LOGS_DIR`` so
#: users can tell the two apart: ``logs/`` is structured JSONL per
#: session; ``live_runtime/`` is raw stdout/stderr bytes for crash
#: forensics.
LIVE_DIR: Path = DATA_DIR / "live_runtime"

CURRENT_FILE: Path = LIVE_DIR / "current.log"
PREVIOUS_FILE: Path = LIVE_DIR / "previous.log"

#: When the current file grows past this size we drop the head half.
#: 64 MB is comfortable for a multi-hour test session even with verbose
#: uvicorn access logs.
MAX_FILE_BYTES: int = 64 * 1024 * 1024

#: If True, call ``fsync()`` after each line. Very slow (forces sync
#: writes) but guarantees the tail survives a full-power-cut. Off by
#: default; enable via ``TESTBENCH_LIVE_LOG_FSYNC=1`` env var for the
#: rare "I must not lose even 1 line" debugging pass.
FSYNC_EACH_LINE: bool = os.environ.get("TESTBENCH_LIVE_LOG_FSYNC") == "1"

# ── internals ─────────────────────────────────────────────────────────

_lock = threading.Lock()
_installed = False
_original_stdout: TextIO | None = None
_original_stderr: TextIO | None = None
_file_handle: io.TextIOBase | None = None


class _TeeStream:
    """Wraps an original stdout/stderr and mirrors every write to file.

    Each incoming ``write`` goes to the original (so the terminal keeps
    working normally) then to ``_file_handle`` under ``_lock`` to
    serialize concurrent writes from different threads (uvicorn's
    access-log thread, main thread printing banners, asyncio task
    callbacks etc).
    """

    def __init__(self, original: TextIO, *, stream_name: str) -> None:
        self._original = original
        self._stream_name = stream_name  # 'stdout' or 'stderr'

    # ``sys.stdout`` / ``sys.stderr`` are expected to implement a small
    # set of TextIOBase methods. We only need to faithfully proxy the
    # ones callers touch in practice.

    def write(self, s: str) -> int:
        # Original first so terminal is never blocked by our file IO.
        written = self._original.write(s)
        try:
            self._tee_to_file(s)
        except Exception:  # noqa: BLE001 — tee must never break stdout
            # Intentionally silent: if we raise here we'd crash the
            # server. This is forensic infrastructure, not a critical
            # path.
            pass
        return written

    def _tee_to_file(self, s: str) -> None:
        fh = _file_handle
        if fh is None:
            return
        with _lock:
            # Size cap check: at rotation we drop the head half.
            try:
                # Estimating size via tell() is cheap and accurate
                # because we opened with text mode buffering.
                size = fh.tell()
            except (OSError, io.UnsupportedOperation):
                size = 0
            if size > MAX_FILE_BYTES:
                _rotate_head_half(fh)
            fh.write(s)
            # Line-flush: ``print`` typically passes a full line, so
            # this is essentially per-line. We always flush; the
            # ``FSYNC_EACH_LINE`` flag decides whether to also fsync.
            try:
                fh.flush()
                if FSYNC_EACH_LINE:
                    os.fsync(fh.fileno())
            except (OSError, io.UnsupportedOperation):
                pass

    def flush(self) -> None:
        try:
            self._original.flush()
        except Exception:  # noqa: BLE001
            pass
        with _lock:
            if _file_handle is not None:
                try:
                    _file_handle.flush()
                except Exception:  # noqa: BLE001
                    pass

    # Uvicorn / logging probe these:
    def isatty(self) -> bool:
        try:
            return self._original.isatty()
        except Exception:  # noqa: BLE001
            return False

    def fileno(self) -> int:
        # Some libs call ``fileno`` to feed OS-level redirects. We
        # intentionally return the original's fileno so those paths
        # STILL tee via our wrapper only when Python-level writes go
        # through ``sys.stdout.write``. OS-level writes (e.g. a child
        # process's stdout) won't go through our tee. Acceptable: we
        # don't spawn subprocesses in this codebase.
        return self._original.fileno()

    # Proxy attribute reads to original for full TextIOBase surface
    # compatibility without having to enumerate every method.
    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


def _rotate_head_half(fh: io.TextIOBase) -> None:
    """When the file exceeds ``MAX_FILE_BYTES``, drop the head half.

    This is cheap enough for a 64 MB cap (tens of ms) and rare (only
    one true cascade event typically triggers it). The alternative
    (multi-generational rotation) adds state + failure modes.

    Caller holds ``_lock``.
    """
    path = CURRENT_FILE
    try:
        fh.flush()
        fh.close()
    except Exception:  # noqa: BLE001
        pass
    global _file_handle  # noqa: PLW0603
    _file_handle = None
    try:
        data = path.read_bytes()
        half = len(data) // 2
        # Walk forward from the byte midpoint to the next newline; if no
        # newline is found within ~1 MB, fall back to the next valid
        # UTF-8 boundary (a continuation byte ``0b10xxxxxx`` cannot start
        # a code point, so we step past it). Without this guard we'd
        # cut multi-byte CJK chars in half and the next reader (the
        # tester's tail-the-log helper or the Errors subpage live
        # snippet) sees ``\ufffd`` mojibake at the head — confusing for
        # an audit log whose whole point is faithful playback (GH AI-
        # review issue #7).
        cut = half
        nl = data.find(b"\n", cut, cut + 1_048_576)
        if nl != -1:
            cut = nl + 1
        else:
            while cut < len(data) and (data[cut] & 0xC0) == 0x80:
                cut += 1
        notice = (
            f"\n\n[live_runtime_log] rotated at "
            f"{datetime.now().isoformat(timespec='seconds')} "
            f"(file exceeded {MAX_FILE_BYTES} bytes, dropped first {cut} bytes)\n\n"
        ).encode("utf-8", errors="replace")
        path.write_bytes(notice + data[cut:])
    except Exception:  # noqa: BLE001
        pass
    _file_handle = open(path, mode="a", encoding="utf-8", buffering=1)  # noqa: SIM115


# ── public API ────────────────────────────────────────────────────────


def rotate_for_boot() -> dict[str, Any]:
    """Rotate ``current.log`` → ``previous.log`` so the new boot starts fresh.

    Called **once** by ``run_testbench.main`` before ``uvicorn.run``.
    Idempotent: missing files are skipped silently.

    Returns a small stats dict used for the boot self-check audit line.
    """
    stats: dict[str, Any] = {
        "rotated": False,
        "previous_removed": False,
        "previous_bytes": 0,
        "current_bytes": 0,
    }
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return stats

    # Drop the old ``previous`` if any (user asked: "每次开启服务自检
    # 的时候可以清理上一次留下的实时转存日志").
    if PREVIOUS_FILE.exists():
        try:
            stats["previous_bytes"] = PREVIOUS_FILE.stat().st_size
            PREVIOUS_FILE.unlink()
            stats["previous_removed"] = True
        except OSError:
            pass

    # Move the previous boot's ``current`` to ``previous`` so it's
    # still available for forensics (one generation back).
    if CURRENT_FILE.exists():
        try:
            stats["current_bytes"] = CURRENT_FILE.stat().st_size
            os.replace(CURRENT_FILE, PREVIOUS_FILE)
            stats["rotated"] = True
        except OSError:
            pass

    return stats


def install() -> None:
    """Install the stdout/stderr tee. Idempotent.

    Call **after** ``rotate_for_boot()`` and **before** ``uvicorn.run``
    so every byte the server emits — the banner, uvicorn startup
    messages, access logs, library logs, ``print`` calls — lands in
    ``current.log``.
    """
    global _installed, _original_stdout, _original_stderr, _file_handle  # noqa: PLW0603
    if _installed:
        return
    try:
        LIVE_DIR.mkdir(parents=True, exist_ok=True)
        _file_handle = open(CURRENT_FILE, mode="a", encoding="utf-8", buffering=1)  # noqa: SIM115
    except OSError as exc:
        # Can't create the file — print a warning and give up. The
        # server should still start; the user just won't have live
        # runtime forensics this boot.
        print(
            f"[live_runtime_log] WARNING: cannot open {CURRENT_FILE}: {exc}; "
            "live runtime log disabled this session.",
            file=sys.stderr,
        )
        return

    # Add a boot-banner line to the live file so the forensic reader
    # can tell where a boot begins.
    try:
        _file_handle.write(
            f"\n===== live_runtime_log boot at "
            f"{datetime.now().isoformat(timespec='seconds')} "
            f"(pid={os.getpid()}) =====\n"
        )
        _file_handle.flush()
    except OSError:
        pass

    _original_stdout = sys.stdout
    _original_stderr = sys.stderr
    sys.stdout = _TeeStream(_original_stdout, stream_name="stdout")  # type: ignore[assignment]
    sys.stderr = _TeeStream(_original_stderr, stream_name="stderr")  # type: ignore[assignment]
    _installed = True


def close() -> None:
    """Restore stdout/stderr and close the file. Safe to call even if never installed."""
    global _installed, _original_stdout, _original_stderr, _file_handle  # noqa: PLW0603
    if not _installed:
        return
    try:
        if _original_stdout is not None:
            sys.stdout = _original_stdout  # type: ignore[assignment]
        if _original_stderr is not None:
            sys.stderr = _original_stderr  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass
    with _lock:
        if _file_handle is not None:
            try:
                _file_handle.write(
                    f"===== live_runtime_log close at "
                    f"{datetime.now().isoformat(timespec='seconds')} =====\n"
                )
                _file_handle.flush()
                _file_handle.close()
            except Exception:  # noqa: BLE001
                pass
            _file_handle = None
    _installed = False
