"""Supervises the local `8111_for_wows` telemetry service.

Two rules shape everything here:

* **A service we did not start is never stopped.** If `/healthz` already answers
  with our service id, it belongs to the user and we just use it.
* **Identity before action.** Port 8111 is also War Thunder's own telemetry port.
  Reaching *something* there proves nothing, so we neither launch nor terminate
  anything until the `serviceId` matches.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SERVICE_ID = "8111-for-wows"
SUPPORTED_API_MAJOR = 1

MODE_EXTERNAL = "external"
MODE_MANAGED = "managed"
MODE_OFFLINE = "offline"
MODE_CONFLICT = "conflict"
MODE_DISABLED = "disabled"

# Crash backoff: 1s, 2s, 4s, then hold at 4s.
_BACKOFF_SECONDS = (1.0, 2.0, 4.0)
_CRASH_WINDOW_SECONDS = 60.0
_CRASH_LIMIT = 3

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


@dataclass
class ServiceHealth:
    reachable: bool = False
    ours: bool = False
    service_id: str = ""
    api_version: str = ""
    instance_id: str = ""
    source_status: str = ""
    error: str = ""

    @property
    def usable(self) -> bool:
        return self.reachable and self.ours


@dataclass
class ServiceStatus:
    mode: str = MODE_OFFLINE
    health: ServiceHealth = field(default_factory=ServiceHealth)
    pid: int | None = None
    # instanceId observed right after we launched; a terminate only proceeds when
    # the live service still reports this exact id.
    owned_instance_id: str = ""
    paused: bool = False
    detail: str = ""
    crash_count: int = 0

    @property
    def transport_allowed(self) -> bool:
        """A reachable foreign service must never be polled as WoWS telemetry."""
        return self.mode != MODE_CONFLICT

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "reachable": self.health.reachable,
            "ours": self.health.ours,
            "service_id": self.health.service_id,
            "api_version": self.health.api_version,
            "instance_id": self.health.instance_id,
            "source_status": self.health.source_status,
            "error": self.health.error,
            "pid": self.pid,
            "paused": self.paused,
            "detail": self.detail,
            "crash_count": self.crash_count,
        }


def api_major(version: str) -> int | None:
    head = str(version or "").split(".", 1)[0].strip()
    try:
        return int(head)
    except ValueError:
        return None


def is_loopback_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    return host is not None and host.lower() in LOOPBACK_HOSTS


def is_usable_service_url(url: str) -> bool:
    """Whether the configured address can be acted on at all.

    A malformed port only raises when `.port` is read, which happens deep inside
    the launch path. A typo in the config has to surface as an offline service,
    not as an exception out of startup.
    """
    try:
        parsed = urlparse(url)
        parsed.port
    except ValueError:
        return False
    return bool(parsed.scheme and parsed.hostname)


def _explicit_port(url: str) -> int | None:
    try:
        return urlparse(url).port
    except ValueError:
        return None


def probe_health(base_url: str, timeout: float) -> ServiceHealth:
    """Ask `/healthz` who it is. Never raises."""
    url = base_url.rstrip("/") + "/healthz"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return ServiceHealth(reachable=False, error=type(exc).__name__)

    if not isinstance(payload, dict):
        return ServiceHealth(reachable=True, ours=False, error="unexpected payload")

    service_id = str(payload.get("serviceId") or "")
    version = str(payload.get("apiVersion") or "")
    major = api_major(version)
    ours = service_id == SERVICE_ID and major == SUPPORTED_API_MAJOR
    error = ""
    if service_id and service_id != SERVICE_ID:
        error = f"foreign service: {service_id}"
    elif not service_id:
        # Something answers on this port but does not identify itself. War
        # Thunder's telemetry looks like this.
        error = "unidentified service on this port"
    elif major != SUPPORTED_API_MAJOR:
        error = f"unsupported apiVersion: {version}"
    return ServiceHealth(
        reachable=True,
        ours=ours,
        service_id=service_id,
        api_version=version,
        instance_id=str(payload.get("instanceId") or ""),
        source_status=str(payload.get("sourceStatus") or ""),
        error=error,
    )


class WowsServiceManager:
    """Probes, optionally launches, and only ever stops its own child."""

    def __init__(self, cfg, *, logger=None, log_dir: Path | None = None) -> None:
        self.cfg = cfg
        self.logger = logger
        self.log_dir = log_dir
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._log_handle = None
        self._owned_instance_id = ""
        self._crash_times: list[float] = []
        self._paused = False
        self._detail = ""
        self._last_health = ServiceHealth()
        # Monotonic deadline: no relaunch attempt before it passes.
        self._retry_not_before = 0.0

    # ------------------------------------------------------------------
    def apply_config(self, cfg) -> None:
        with self._lock:
            self.cfg = cfg

    def health(self) -> ServiceHealth:
        health = probe_health(self.cfg.service_url, self.cfg.service_health_timeout_seconds)
        with self._lock:
            self._last_health = health
        return health

    def snapshot(self) -> ServiceStatus:
        with self._lock:
            process = self._process
            alive = process is not None and process.poll() is None
            health = self._last_health
            mode = MODE_OFFLINE
            if health.usable:
                mode = MODE_MANAGED if alive else MODE_EXTERNAL
            elif health.reachable:
                mode = MODE_CONFLICT
            elif not self.cfg.service_auto_start:
                mode = MODE_DISABLED
            return ServiceStatus(
                mode=mode,
                health=health,
                pid=process.pid if alive and process is not None else None,
                owned_instance_id=self._owned_instance_id,
                paused=self._paused,
                detail=self._detail,
                crash_count=len(self._crash_times),
            )

    # ------------------------------------------------------------------
    def start_if_needed(self) -> ServiceStatus:
        """Use an existing service, or launch one if we are allowed to."""
        health = self.health()
        if health.usable:
            with self._lock:
                owned = self._process is not None and self._process.poll() is None
                self._detail = "reusing running service" if not owned else "managed service healthy"
            return self.snapshot()

        if health.reachable:
            self._set_detail(
                "port busy with a different service; not starting and not stopping anything")
            return self.snapshot()

        if not self.cfg.service_auto_start:
            self._set_detail("auto-start disabled")
            return self.snapshot()
        if not self.cfg.service_source_dir:
            self._set_detail("service_source_dir is empty; nothing to launch")
            return self.snapshot()
        if not is_usable_service_url(self.cfg.service_url):
            self._set_detail("service_url is not a usable address; fix it before auto-start")
            return self.snapshot()
        if not is_loopback_url(self.cfg.service_url):
            # Auto-managing a remote host would mean starting a process on a
            # machine the user did not point us at.
            self._set_detail("refusing to auto-manage a non-loopback address")
            return self.snapshot()
        with self._lock:
            if self._paused:
                return self.snapshot()

        return self._launch()

    def supervise(self) -> ServiceStatus:
        """Recheck the service after the transport stopped receiving frames.

        `start_if_needed` only runs at startup and on an explicit reconnect, so
        without this a child that died mid-battle would stay dead. Restarting is
        rate-limited by the same crash window as a failed start, so a service
        that cannot stay up stops being relaunched instead of looping.
        """
        with self._lock:
            process = self._process
        if process is not None and process.poll() is not None:
            self._reap(process.poll())

        now = time.monotonic()
        with self._lock:
            if self._paused or now < self._retry_not_before:
                return self.snapshot()
        return self.start_if_needed()

    def _reap(self, exit_code: int | None) -> None:
        """Forget a managed child that exited on its own."""
        self._close_log()
        with self._lock:
            self._process = None
            self._owned_instance_id = ""
        self._log("warning", f"managed service exited (code={exit_code})")
        self._set_detail(f"managed service exited unexpectedly (code={exit_code})")
        # Last, so that the pause message wins when the fuse trips: which exit
        # code it was matters less than auto-management having given up.
        self.note_crash()

    def stop(self) -> ServiceStatus:
        with self._lock:
            process = self._process
            owned_instance = self._owned_instance_id
            self._process = None
            self._owned_instance_id = ""

        if process is None:
            self._set_detail("nothing to stop (service was external or absent)")
            return self.snapshot()
        if process.poll() is not None:
            self._close_log()
            self._set_detail("managed service had already exited")
            return self.snapshot()

        live = probe_health(self.cfg.service_url, self.cfg.service_health_timeout_seconds)
        if live.usable and owned_instance and live.instance_id != owned_instance:
            # Our child is alive but something else is answering the port now.
            # Terminating on that evidence alone risks killing the wrong thing.
            self._log("warning", "instanceId changed since launch; leaving the process alone")
            self._set_detail("instance mismatch; managed process left running")
            with self._lock:
                self._process = process
                self._owned_instance_id = owned_instance
            return self.snapshot()

        try:
            process.terminate()
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
        except OSError as exc:
            self._log("warning", f"failed to terminate managed service: {exc}")
        self._close_log()
        self._set_detail("managed service stopped")
        return self.snapshot()

    def resume(self) -> ServiceStatus:
        with self._lock:
            self._paused = False
            self._crash_times.clear()
            self._retry_not_before = 0.0
            self._detail = "resumed"
        return self.snapshot()

    def note_crash(self) -> ServiceStatus:
        """Record an unexpected exit and pause after too many in one window."""
        now = time.monotonic()
        with self._lock:
            self._crash_times = [t for t in self._crash_times if now - t < _CRASH_WINDOW_SECONDS]
            self._crash_times.append(now)
            # Every failure, whether a bad start or a crash after one, pushes
            # the next automatic attempt out. Only `supervise` honours this;
            # asking for a reconnect by hand still tries straight away.
            self._retry_not_before = now + self.backoff_seconds()
            if len(self._crash_times) >= _CRASH_LIMIT:
                self._paused = True
                self._detail = (
                    f"paused after {len(self._crash_times)} service failures "
                    f"within {_CRASH_WINDOW_SECONDS:.0f}s"
                )
        return self.snapshot()

    def backoff_seconds(self) -> float:
        with self._lock:
            index = min(len(self._crash_times), len(_BACKOFF_SECONDS) - 1)
            return _BACKOFF_SECONDS[index]

    # ------------------------------------------------------------------
    def _launch(self) -> ServiceStatus:
        source_dir = Path(self.cfg.service_source_dir).expanduser()
        script = source_dir / "server" / "server.py"
        if not script.is_file():
            self._set_detail(f"server.py not found under {source_dir}")
            return self.note_crash()

        command = self._build_command(source_dir, script)
        if command is None:
            self._set_detail("no usable Python interpreter for the service")
            return self.note_crash()

        try:
            handle = self._open_log()
            process = subprocess.Popen(
                command,
                cwd=str(source_dir),
                stdout=handle or subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=_no_window_flags(),
            )
        except OSError as exc:
            self._log("error", f"failed to launch telemetry service: {exc}")
            self._set_detail(f"launch failed: {exc}")
            return self.note_crash()

        with self._lock:
            self._process = process
            self._log_handle = handle

        health = self._await_health(process)
        if not health.usable:
            self._log("warning", "service did not become healthy within the startup timeout")
            self.stop()
            self._set_detail("startup timed out")
            return self.note_crash()

        with self._lock:
            self._owned_instance_id = health.instance_id
            self._detail = "managed service started"
        self._log("info", f"telemetry service started (pid={process.pid})")
        return self.snapshot()

    def _build_command(self, source_dir: Path, script: Path) -> list[str] | None:
        args = ["server/server.py"]
        port = _explicit_port(self.cfg.service_url)
        if port:
            args += ["--port", str(port)]
        if self.cfg.game_dir:
            args += ["--game-dir", self.cfg.game_dir]

        interpreter = _project_interpreter(source_dir)
        if interpreter is not None:
            return [str(interpreter), *args]
        uv = _which_uv()
        if uv is not None:
            return [uv, "run", "python", *args]
        return None

    def _await_health(self, process: subprocess.Popen[bytes]) -> ServiceHealth:
        deadline = time.monotonic() + self.cfg.service_startup_timeout_seconds
        health = ServiceHealth()
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return ServiceHealth(reachable=False, error="process exited during startup")
            health = self.health()
            if health.usable:
                return health
            time.sleep(0.2)
        return health

    # ------------------------------------------------------------------
    def _open_log(self):
        if self.log_dir is None:
            return None
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            path = self.log_dir / "telemetry_service.log"
            return path.open("ab", buffering=0)
        except OSError as exc:
            self._log("warning", f"cannot open service log: {exc}")
            return None

    def _close_log(self) -> None:
        with self._lock:
            handle = self._log_handle
            self._log_handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass

    def _set_detail(self, detail: str) -> None:
        with self._lock:
            self._detail = detail

    def _log(self, level: str, message: str) -> None:
        if self.logger is None:
            return
        method = getattr(self.logger, level, None)
        if callable(method):
            try:
                method(message)
            except Exception:
                pass


def _project_interpreter(source_dir: Path) -> Path | None:
    """Prefer the service project's own virtualenv over anything global."""
    candidates = (
        source_dir / ".venv" / "Scripts" / "python.exe",
        source_dir / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _which_uv() -> str | None:
    from shutil import which

    return which("uv")


def _no_window_flags() -> int:
    if sys.platform != "win32":
        return 0
    # Keep the console window hidden; the log file is the interface.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


__all__ = [
    "MODE_CONFLICT",
    "MODE_DISABLED",
    "MODE_EXTERNAL",
    "MODE_MANAGED",
    "MODE_OFFLINE",
    "SERVICE_ID",
    "SUPPORTED_API_MAJOR",
    "ServiceHealth",
    "ServiceStatus",
    "WowsServiceManager",
    "api_major",
    "is_loopback_url",
    "is_usable_service_url",
    "probe_health",
]
