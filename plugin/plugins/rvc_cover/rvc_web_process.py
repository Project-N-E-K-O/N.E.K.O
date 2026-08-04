"""Lifecycle owner for vendored RVC Gradio/API (infer-web.py)."""

from __future__ import annotations

import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, IO

PopenFactory = Callable[..., Any]
SleepFn = Callable[[float], None]
HealthCheck = Callable[[str, float], bool]


@dataclass
class RvcWebConfig:
    rvc_root: Path
    python_path: Path
    port: int = 7897
    server_name: str = "127.0.0.1"
    auto_start: bool = True
    startup_timeout_seconds: float = 90.0
    shutdown_timeout_seconds: float = 8.0
    health_timeout_seconds: float = 2.0
    log_dir: Path | None = None

    @property
    def base_url(self) -> str:
        host = "127.0.0.1" if self.server_name in {"0.0.0.0", "::"} else self.server_name
        return f"http://{host}:{int(self.port)}"


def check_rvc_web_health(base_url: str, timeout: float) -> bool:
    url = base_url.rstrip("/") + "/"
    try:
        req = urllib.request.Request(url, headers={"Accept": "text/html,application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 500
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return False


def _tail_text(path: Path | None, *, max_chars: int = 800) -> str:
    if path is None:
        return ""
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = data.strip()
    return text[-max_chars:] if len(text) > max_chars else text


class RvcWebProcessManager:
    """Start/stop only the infer-web process this plugin owns.

    If the configured port is already healthy, treat it as external and never kill it.
    """

    def __init__(
        self,
        config: RvcWebConfig,
        *,
        health_check: HealthCheck = check_rvc_web_health,
        popen_factory: PopenFactory = subprocess.Popen,
        sleep: SleepFn = time.sleep,
    ) -> None:
        self.config = config
        self.health_check = health_check
        self.popen_factory = popen_factory
        self.sleep = sleep
        self._process: Any | None = None
        self._started_by_plugin = False
        self._mode = "unknown"
        self._last_error: str | None = None
        self._last_health = False
        self._stdout_handle: IO[str] | None = None
        self._stderr_handle: IO[str] | None = None
        self._stdout_log_path: Path | None = None
        self._stderr_log_path: Path | None = None

    def configure(self, config: RvcWebConfig) -> None:
        self.config = config

    def start_if_needed(self) -> dict[str, Any]:
        if self._started_by_plugin and self._process is not None:
            returncode = self._process.poll()
            if returncode is None:
                healthy = self.health_check(
                    self.config.base_url,
                    self.config.health_timeout_seconds,
                )
                self._mode = "managed"
                self._last_health = healthy
                if healthy:
                    self._last_error = None
                return self.snapshot()
            self._last_error = self._format_exit_error(returncode)
            self._process = None
            self._started_by_plugin = False
            self._last_health = False
            self._close_log_handles()

        if self.health_check(self.config.base_url, self.config.health_timeout_seconds):
            self._mode = "external"
            self._started_by_plugin = False
            self._last_health = True
            self._last_error = None
            return self.snapshot()

        self._last_health = False
        if not self.config.auto_start:
            self._mode = "disabled"
            self._started_by_plugin = False
            self._last_error = None
            return self.snapshot()

        try:
            self._process = self._spawn()
            self._started_by_plugin = True
            self._mode = "starting"
            self._last_error = None
        except Exception as exc:  # noqa: BLE001
            self._process = None
            self._started_by_plugin = False
            self._mode = "failed"
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._close_log_handles()
            return self.snapshot()

        deadline = time.monotonic() + float(self.config.startup_timeout_seconds)
        while time.monotonic() < deadline:
            if self.health_check(self.config.base_url, self.config.health_timeout_seconds):
                self._mode = "managed"
                self._last_health = True
                self._last_error = None
                return self.snapshot()
            if self._process is not None and self._process.poll() is not None:
                code = self._process.poll()
                self._mode = "failed"
                self._last_error = self._format_exit_error(code)
                self._process = None
                self._started_by_plugin = False
                self._last_health = False
                self._close_log_handles()
                return self.snapshot()
            self.sleep(0.25)

        # Keep the process; Gradio/CUDA warm may finish after timeout.
        self._mode = "managed"
        self._last_health = False
        self._last_error = "health_timeout"
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        if not self._started_by_plugin or self._process is None:
            self._close_log_handles()
            if self._mode not in {"external", "disabled", "unknown"}:
                self._mode = "stopped"
            return self.snapshot()

        proc = self._process
        pid = getattr(proc, "pid", None)
        try:
            if proc.poll() is None:
                self._kill_process_tree(pid)
                try:
                    proc.wait(timeout=self.config.shutdown_timeout_seconds)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self._process = None
            self._started_by_plugin = False
            self._mode = "stopped"
            self._last_health = False
            self._close_log_handles()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        pid = getattr(self._process, "pid", None) if self._process is not None else None
        return {
            "mode": self._mode,
            "url": self.config.base_url,
            "port": int(self.config.port),
            "pid": pid,
            "started_by_plugin": self._started_by_plugin,
            "auto_start": bool(self.config.auto_start),
            "health": self._last_health,
            "last_error": self._last_error,
            "stdout_log": str(self._stdout_log_path) if self._stdout_log_path else "",
            "stderr_log": str(self._stderr_log_path) if self._stderr_log_path else "",
        }

    def _spawn(self):
        root = Path(self.config.rvc_root)
        python = Path(self.config.python_path)
        script = root / "infer-web.py"
        if not script.is_file():
            raise FileNotFoundError(f"missing infer-web.py: {script}")
        if not python.is_file():
            raise FileNotFoundError(f"missing python: {python}")

        self._prepare_log_files()
        assert self._stdout_handle is not None
        assert self._stderr_handle is not None

        cmd = [
            str(python),
            str(script),
            "--pycmd",
            str(python),
            "--port",
            str(int(self.config.port)),
            "--api",
            "--server_name",
            str(self.config.server_name),
        ]
        kwargs: dict[str, Any] = {
            "cwd": str(root),
            "stdout": self._stdout_handle,
            "stderr": self._stderr_handle,
            "stdin": subprocess.DEVNULL,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return self.popen_factory(cmd, **kwargs)

    def _prepare_log_files(self) -> None:
        self._close_log_handles()
        log_dir = self.config.log_dir
        if log_dir is None:
            log_dir = Path(os.environ.get("TEMP") or ".") / "neko_rvc_web"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log_dir = Path(os.environ.get("TEMP") or ".")
        self._stdout_log_path = log_dir / f"rvc_web_{self.config.port}_stdout.log"
        self._stderr_log_path = log_dir / f"rvc_web_{self.config.port}_stderr.log"
        self._stdout_handle = self._stdout_log_path.open("w", encoding="utf-8", errors="replace")
        self._stderr_handle = self._stderr_log_path.open("w", encoding="utf-8", errors="replace")

    def _close_log_handles(self) -> None:
        for handle in (self._stdout_handle, self._stderr_handle):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        self._stdout_handle = None
        self._stderr_handle = None

    def _format_exit_error(self, returncode: int | None) -> str:
        stderr_tail = _tail_text(self._stderr_log_path)
        if stderr_tail:
            last_line = stderr_tail.splitlines()[-1].strip()
            return f"process_exited_before_healthy(exit={returncode}; {last_line})"
        return f"process_exited_before_healthy(exit={returncode})"

    @staticmethod
    def _kill_process_tree(pid: int | None) -> None:
        if not pid:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
            except OSError:
                pass
        try:
            os.kill(pid, 15)
        except OSError:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
