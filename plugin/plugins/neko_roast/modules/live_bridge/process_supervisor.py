"""Small process supervisor for bundled local bridge executables."""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class BridgeProcessState:
    ok: bool = False
    base_url: str = ""
    port: int = 0
    last_error: str = ""


class BridgeProcessSupervisor:
    """Start and stop one localhost bridge executable."""

    def __init__(
        self,
        *,
        executable_path: Path,
        args_factory: Callable[[int], list[str]],
        process_factory: Any = None,
        port_factory: Callable[[], int] | None = None,
        port_waiter: Callable[[int, float], bool] | None = None,
        stale_process_cleaner: Callable[[Path], None] | None = None,
    ) -> None:
        self._executable_path = executable_path
        self._args_factory = args_factory
        self._process_factory = process_factory or subprocess.Popen
        self._port_factory = port_factory or _find_free_port
        self._port_waiter = port_waiter or _wait_for_port
        self._stale_process_cleaner = stale_process_cleaner
        self._process: Any = None
        self._port = 0
        self._lifecycle_lock = asyncio.Lock()

    async def start(self) -> BridgeProcessState:
        async with self._lifecycle_lock:
            return await self._start_locked()

    async def _start_locked(self) -> BridgeProcessState:
        if self._is_running():
            return BridgeProcessState(ok=True, base_url=_base_url(self._port), port=self._port)
        if not self._executable_path.is_file():
            return BridgeProcessState(ok=False, last_error="bundled bridge executable is missing")
        if self._stale_process_cleaner is not None:
            with _suppress_process_errors():
                await asyncio.to_thread(self._stale_process_cleaner, self._executable_path)
        port = self._port_factory()
        try:
            self._process = self._process_factory(
                [str(self._executable_path), *self._args_factory(port)],
                cwd=str(self._executable_path.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        except Exception as exc:
            return BridgeProcessState(ok=False, last_error=f"bundled bridge start failed: {type(exc).__name__}")
        if self._stale_process_cleaner is not None:
            with _suppress_process_errors():
                _record_owned_process(self._executable_path, self._process)
        self._port = port
        try:
            if await asyncio.to_thread(self._port_waiter, port, 8.0):
                return BridgeProcessState(ok=True, base_url=_base_url(port), port=port)
        except BaseException:
            await self._stop_locked()
            raise
        await self._stop_locked()
        return BridgeProcessState(ok=False, last_error="bundled bridge did not open localhost port")

    async def stop(self) -> BridgeProcessState:
        async with self._lifecycle_lock:
            return await self._stop_locked()

    async def _stop_locked(self) -> BridgeProcessState:
        process = self._process
        self._process = None
        port = self._port
        self._port = 0
        if process is not None and _poll(process) is None:
            await asyncio.to_thread(_terminate_process, process)
        if process is not None and self._stale_process_cleaner is not None:
            with _suppress_process_errors():
                _remove_owned_process_marker(self._executable_path, _process_pid(process))
        return BridgeProcessState(ok=False, port=port)

    def _is_running(self) -> bool:
        return self._process is not None and self._port > 0 and _poll(self._process) is None


class _suppress_process_errors:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return True


def _poll(process: Any) -> Any:
    try:
        return process.poll()
    except Exception:
        return 0


def _terminate_process(process: Any) -> None:
    with _suppress_process_errors():
        process.terminate()
        process.wait(timeout=4)
    if _poll(process) is None:
        with _suppress_process_errors():
            process.kill()
            process.wait(timeout=2)


def _base_url(port: int) -> str:
    return f"ws://127.0.0.1:{int(port)}/ws" if int(port) > 0 else ""


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout))
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def cleanup_stale_windows_processes(executable_path: Path) -> None:
    if os.name != "nt":
        return
    marker_path = _ownership_marker_path(executable_path)
    process_id = _read_owned_process_id(marker_path)
    if process_id <= 0:
        return
    env = os.environ.copy()
    env["NEKO_BRIDGE_EXE"] = str(executable_path)
    env["NEKO_BRIDGE_PROCESS_ID"] = str(process_id)
    script = """
$target = [Environment]::GetEnvironmentVariable('NEKO_BRIDGE_EXE')
$pidText = [Environment]::GetEnvironmentVariable('NEKO_BRIDGE_PROCESS_ID')
$ownedPid = 0
if (-not $target -or -not [int]::TryParse($pidText, [ref]$ownedPid)) { exit 0 }
Get-CimInstance Win32_Process |
  Where-Object { $_.ProcessId -eq $ownedPid -and $_.ExecutablePath -eq $target } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
"""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
            env=env,
            creationflags=_creation_flags(),
        )
    finally:
        _remove_owned_process_marker(executable_path, process_id)


def _ownership_marker_path(executable_path: Path) -> Path:
    normalized = str(executable_path.resolve()).casefold().encode("utf-8")
    digest = hashlib.sha256(normalized).hexdigest()[:20]
    return Path(tempfile.gettempdir()) / "neko-roast-live-bridge" / f"{digest}.pid"


def _process_pid(process: Any) -> int:
    try:
        value = int(getattr(process, "pid", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _record_owned_process(executable_path: Path, process: Any) -> None:
    process_id = _process_pid(process)
    if process_id <= 0:
        return
    marker_path = _ownership_marker_path(executable_path)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(str(process_id), encoding="ascii")


def _read_owned_process_id(marker_path: Path) -> int:
    try:
        value = int(marker_path.read_text(encoding="ascii").strip())
    except (OSError, TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def _remove_owned_process_marker(executable_path: Path, process_id: int) -> None:
    marker_path = _ownership_marker_path(executable_path)
    if _read_owned_process_id(marker_path) != process_id:
        return
    marker_path.unlink(missing_ok=True)
