"""Optional lifecycle owner for the vendored War Thunder data-layer process."""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import os
import shutil
import importlib.util
import threading
from pathlib import Path
from typing import Any, Callable, IO

from ..core.contracts import WtConfig


HealthCheck = Callable[[str, float], bool]
PopenFactory = Callable[..., Any]
SleepFn = Callable[[float], None]
DATA_LAYER_BIND_HOST = "127.0.0.1"


def check_data_layer_health(base_url: str, timeout: float) -> bool:
    url = f"{base_url.rstrip('/')}/health"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _port_from_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.port is not None:
        return str(parsed.port)
    return "443" if parsed.scheme == "https" else "80"


def _looks_like_python(executable: str | None) -> bool:
    if not executable:
        return False
    name = Path(executable).name.lower()
    return name.startswith("python") or name in {"py.exe", "py"}


def _python_command_prefixes() -> list[list[str]]:
    """Return Python command prefixes that can execute the vendored data layer."""

    candidates: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(prefix: list[str]) -> None:
        key = tuple(prefix)
        if key not in seen:
            candidates.append(prefix)
            seen.add(key)

    executable = sys.executable
    if _looks_like_python(executable):
        add([executable])

    base_executable = getattr(sys, "_base_executable", None)
    if _looks_like_python(base_executable):
        add([str(base_executable)])

    env_python = os.environ.get("PYTHON")
    if _looks_like_python(env_python):
        add([env_python])

    for name in ("python", "python3"):
        path = shutil.which(name)
        if _looks_like_python(path):
            add([path])

    py_launcher = shutil.which("py")
    if py_launcher:
        add([py_launcher, "-3"])

    return candidates


def _tail_text(path: Path, *, max_chars: int = 800) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = data.strip()
    return text[-max_chars:] if len(text) > max_chars else text


class EmbeddedDataLayerProcess:
    """Small process-like wrapper for hosts that cannot spawn Python scripts."""

    def __init__(self, *, httpd: Any, service: Any, thread: threading.Thread) -> None:
        self.pid = os.getpid()
        self.httpd = httpd
        self.service = service
        self.thread = thread
        self._terminated = False

    def poll(self):
        if self.thread.is_alive() and not self._terminated:
            return None
        return 0 if self._terminated else 1

    def terminate(self) -> None:
        self._terminated = True
        self.httpd.shutdown()
        self.service.stop()

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):
        self.thread.join(timeout=timeout)
        return 0


def _load_wt_server_module(data_process_dir: Path):
    module_name = "_neko_warthunder_embedded_wt_server"
    script = data_process_dir / "wt_server.py"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_load_data_layer_module: {script}")

    old_path = list(sys.path)
    sys.path.insert(0, str(data_process_dir))
    try:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = old_path


def _spawn_embedded_data_layer(data_process_dir: Path, *, port: int) -> EmbeddedDataLayerProcess:
    wt_server = _load_wt_server_module(data_process_dir)
    recorder = wt_server.SessionRecorder(
        root_dir=str(data_process_dir / "records"),
        interval=1.0,
        segment_bytes=int(32.0 * 1024 * 1024),
        server_version=wt_server._Handler.server_version,
    )
    client = wt_server.WarThunderClient(host="127.0.0.1", port=wt_server.WT_PORT)
    service = wt_server.TelemetryService(
        client,
        fast_interval=0.1,
        map_interval=0.5,
        event_interval=1.0,
        mapimg_interval=5.0,
        save_map=False,
        map_dir=str(data_process_dir / "maps"),
        profiles_path=None,
        player_name=None,
        recorder=recorder,
    )
    service.start()
    try:
        httpd = wt_server.ThreadingHTTPServer((DATA_LAYER_BIND_HOST, port), wt_server._Handler)
        httpd.service = service
    except Exception:
        service.stop()
        raise

    thread = threading.Thread(target=httpd.serve_forever, name="neko-warthunder-data-layer", daemon=True)
    thread.start()
    return EmbeddedDataLayerProcess(httpd=httpd, service=service, thread=thread)


class DataLayerProcessManager:
    """Start and stop only the data-layer process this plugin owns.

    If :8112 is already healthy, it is treated as external and never killed.
    """

    def __init__(
        self,
        config: WtConfig,
        *,
        plugin_root: Path,
        health_check: HealthCheck = check_data_layer_health,
        popen_factory: PopenFactory = subprocess.Popen,
        sleep: SleepFn = time.sleep,
    ) -> None:
        self.config = config
        self.plugin_root = Path(plugin_root)
        self.health_check = health_check
        self.popen_factory = popen_factory
        self.sleep = sleep
        self._process: Any | None = None
        self._mode = "unknown"
        self._started_by_plugin = False
        self._last_error: str | None = None
        self._last_health = False
        self._stdout_handle: IO[str] | None = None
        self._stderr_handle: IO[str] | None = None
        self._stdout_log_path: Path | None = None
        self._stderr_log_path: Path | None = None
        self._python_cmd: list[str] = []

    def configure(self, config: WtConfig) -> None:
        self.config = config

    def start_if_needed(self) -> dict[str, Any]:
        if self.health_check(self.config.data_layer_url, self.config.http_timeout_seconds):
            self._mode = "external"
            self._started_by_plugin = False
            self._last_health = True
            self._last_error = None
            return self.snapshot()

        self._last_health = False
        if not self.config.data_layer_auto_start:
            self._mode = "missing"
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
            return self.snapshot()

        deadline = time.monotonic() + self.config.data_layer_startup_timeout_seconds
        while time.monotonic() < deadline:
            if self.health_check(self.config.data_layer_url, self.config.http_timeout_seconds):
                self._mode = "managed"
                self._last_health = True
                return self.snapshot()
            if self._process is not None and self._process.poll() is not None:
                self._mode = "failed"
                returncode = self._process.poll()
                self._close_log_handles()
                self._last_error = self._format_exit_error(returncode)
                return self.snapshot()
            self.sleep(0.1)

        self._mode = "managed"
        self._last_health = False
        self._last_error = "health_timeout"
        return self.snapshot()

    def stop(self) -> dict[str, Any]:
        if not self._started_by_plugin or self._process is None:
            return self.snapshot()

        proc = self._process
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=self.config.data_layer_shutdown_timeout_seconds)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
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
            "url": self.config.data_layer_url,
            "pid": pid,
            "started_by_plugin": self._started_by_plugin,
            "auto_start": self.config.data_layer_auto_start,
            "health": self._last_health,
            "last_error": self._last_error,
            "python_cmd": " ".join(self._python_cmd),
            "stdout_log": str(self._stdout_log_path) if self._stdout_log_path else "",
            "stderr_log": str(self._stderr_log_path) if self._stderr_log_path else "",
        }

    def _spawn(self):
        data_process_dir = self.plugin_root / "data_layer" / "data process"
        script = data_process_dir / "wt_server.py"
        if not script.exists():
            raise FileNotFoundError(str(script))

        self._prepare_log_files()
        assert self._stdout_handle is not None
        assert self._stderr_handle is not None

        python_prefixes = _python_command_prefixes()
        if not python_prefixes:
            self._python_cmd = ["embedded"]
            return _spawn_embedded_data_layer(
                data_process_dir,
                port=int(_port_from_url(self.config.data_layer_url)),
            )

        self._python_cmd = python_prefixes[0]
        cmd = [
            *self._python_cmd,
            "wt_server.py",
            "--host",
            DATA_LAYER_BIND_HOST,
            "--port",
            _port_from_url(self.config.data_layer_url),
        ]
        kwargs: dict[str, Any] = {
            "cwd": str(data_process_dir),
            "stdout": self._stdout_handle,
            "stderr": self._stderr_handle,
            "stdin": subprocess.DEVNULL,
        }
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        return self.popen_factory(cmd, **kwargs)

    def _prepare_log_files(self) -> None:
        self._close_log_handles()
        log_dir = self.plugin_root / "local_test_logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            log_dir = Path(os.environ.get("TEMP") or ".")

        self._stdout_log_path = log_dir / "warthunder_data_layer_8112_stdout.log"
        self._stderr_log_path = log_dir / "warthunder_data_layer_8112_stderr.log"
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
        stderr_tail = _tail_text(self._stderr_log_path) if self._stderr_log_path else ""
        if stderr_tail:
            first_line = stderr_tail.splitlines()[-1].strip()
            return f"process_exited_before_healthy(exit={returncode}; {first_line})"
        return f"process_exited_before_healthy(exit={returncode})"
