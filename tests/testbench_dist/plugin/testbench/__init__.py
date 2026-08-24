"""Testbench driver plugin — Mode A single shell / Mode B embedded HTTP.

Prefers an independent WebView window when capability allows.
Does not ship platform PyInstaller binaries.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    neko_plugin,
    plugin_entry,
    ui,
)

_STATE_FILE = "driver_state.json"
_READY_FILE = "shell_ready.json"
_LOG_NAME = "driver_shell.log"
_DEFAULT_COMPATIBLE_NEKO = "*"
_STATUS_HEALTH_TIMEOUT = 0.35
_SCRIPT_PYTHON_UNSET = object()
_WEBVIEW_LAUNCHER = (
    "import sys, webview; "
    "webview.create_window('N.E.K.O. Testbench', sys.argv[1], width=1400, height=900); "
    "webview.start()"
)


def _read_plugin_config(plugin_dir: Path) -> dict[str, Any]:
    """Best-effort read of config.toml / config.example.toml (not required)."""
    for name in ("config.toml", "config.example.toml"):
        path = plugin_dir / name
        if not path.is_file():
            continue
        try:
            import tomllib
        except ModuleNotFoundError:
            try:
                import tomli as tomllib  # type: ignore
            except ModuleNotFoundError:
                return {}
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _compatible_neko_spec(plugin_dir: Path) -> str:
    env = os.environ.get("NEKO_TESTBENCH_COMPATIBLE_NEKO")
    if env:
        return env.strip() or _DEFAULT_COMPATIBLE_NEKO
    cfg = _read_plugin_config(plugin_dir)
    for key in ("compatible_neko",):
        if key in cfg and cfg[key]:
            return str(cfg[key]).strip()
    return _DEFAULT_COMPATIBLE_NEKO


def _check_compatible_neko(plugin_dir: Path) -> str | None:
    """Return error message if incompatible; None if ok / skipped."""
    spec = _compatible_neko_spec(plugin_dir)
    if spec in {"*", "", "any"}:
        return None
    host = os.environ.get("NEKO_VERSION") or os.environ.get("NEKO_HOST_VERSION")
    if not host:
        return None
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        if Version(host.lstrip("v")) not in SpecifierSet(spec):
            return f"NEKO {host} not in compatible_neko={spec!r}"
    except Exception as exc:  # noqa: BLE001
        return f"compatible_neko check failed: {exc}"
    return None


def _looks_like_python(executable: str | None) -> bool:
    if not executable:
        return False
    name = Path(executable).name.lower()
    return name.startswith("python") or name in {"py.exe", "py"}


def _python_command_prefixes() -> list[list[str]]:
    import shutil

    candidates: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(prefix: list[str]) -> None:
        key = tuple(prefix)
        if key not in seen:
            candidates.append(prefix)
            seen.add(key)

    if _looks_like_python(sys.executable) and not getattr(sys, "frozen", False):
        add([sys.executable])
    base = getattr(sys, "_base_executable", None)
    if _looks_like_python(base) and base != sys.executable:
        add([str(base)])
    for name in ("python", "python3"):
        path = shutil.which(name)
        if _looks_like_python(path):
            add([path])
    py_launcher = shutil.which("py")
    if py_launcher:
        add([py_launcher, "-3"])
    return candidates


def _probe_script_python(prefix: list[str], *, timeout: float = 8.0) -> bool:
    try:
        proc = subprocess.run(
            [*prefix, "-c", "import sys; print(sys.version_info[0])"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "3" in (proc.stdout or "")


def find_script_python() -> list[str] | None:
    """Return a argv prefix that can run .py scripts (None → Mode B)."""
    for prefix in _python_command_prefixes():
        if _probe_script_python(prefix):
            return prefix
    return None


def _pick_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _health_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 500
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _find_neko_root(plugin_dir: Path) -> Path | None:
    env = os.environ.get("NEKO_PROJECT_ROOT")
    if env:
        root = Path(env).expanduser().resolve()
        if (root / "utils").is_dir() and (root / "plugin").is_dir():
            return root
    cur = plugin_dir.resolve()
    for _ in range(12):
        if (cur / "utils").is_dir() and (cur / "plugin").is_dir():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def _resolve_neko_root_for_start(plugin_dir: Path) -> Path | None:
    """Live repo root, or bundled import root for installed .neko-plugin packages."""
    found = _find_neko_root(plugin_dir)
    if found is not None:
        return found
    bundled_tb = plugin_dir / "bundled" / "tests" / "testbench"
    if bundled_tb.is_dir():
        return (plugin_dir / "bundled").resolve()
    return None


def _resolve_code_layout(plugin_dir: Path, neko_root: Path) -> tuple[Path, Path]:
    """Return (code_dir, import_root) for tests.testbench imports."""
    if os.environ.get("NEKO_TESTBENCH_DEV_MODE", "").lower() in {"1", "true", "yes"}:
        src = neko_root / "tests" / "testbench"
        if src.is_dir():
            return src, neko_root
    bundled = plugin_dir / "bundled" / "tests" / "testbench"
    if bundled.is_dir():
        return bundled, plugin_dir / "bundled"
    src = neko_root / "tests" / "testbench"
    if src.is_dir():
        return src, neko_root
    raise FileNotFoundError(
        "Testbench code not found (bundled/tests/testbench or NEKO tests/testbench)."
    )


def _windows_taskkill(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@neko_plugin
class TestbenchDriverPlugin(NekoPluginBase):
    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self._plugin_dir = Path(__file__).resolve().parent
        self._shell_proc: subprocess.Popen[Any] | None = None
        self._embed_thread: threading.Thread | None = None
        self._embed_server: Any | None = None
        self._script_python: list[str] | None | object = _SCRIPT_PYTHON_UNSET

    def _cached_script_python(self) -> list[str] | None:
        cached = self._script_python
        if cached is _SCRIPT_PYTHON_UNSET:
            cached = find_script_python()
            self._script_python = cached
        return cached

    def _state_path(self) -> Path:
        return Path(self.data_path()) / _STATE_FILE

    def _ready_path(self) -> Path:
        return Path(self.data_path()) / _READY_FILE

    def _log_path(self) -> Path:
        return Path(self.data_path()) / "logs" / _LOG_NAME

    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _clear_state(self) -> None:
        for path in (self._state_path(), self._ready_path()):
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass

    def _runtime_alive(self, state: dict[str, Any], *, health_timeout: float = _STATUS_HEALTH_TIMEOUT) -> bool:
        url = state.get("url")
        if url and _health_ok(f"{str(url).rstrip('/')}/healthz", timeout=health_timeout):
            return True
        mode = state.get("mode")
        if mode != "B":
            pid = state.get("shell_pid") or state.get("pid")
            if pid and _pid_alive(int(pid)):
                return True
        proc = self._shell_proc
        if proc is not None and proc.poll() is None:
            return True
        if mode == "B" and self._embed_thread is not None and self._embed_thread.is_alive():
            return True
        return False

    def _reconcile_runtime_state(self) -> dict[str, Any]:
        """Drop stale driver_state when shell/server is gone (e.g. user closed WebView)."""
        state = self._load_state()
        proc = self._shell_proc
        if proc is not None and proc.poll() is not None:
            self._shell_proc = None
        if not state:
            return {}
        if self._runtime_alive(state):
            return state
        self._shell_proc = None
        self._embed_server = None
        self._embed_thread = None
        self._clear_state()
        return {}

    def _spawn_webview_window(self, url: str) -> None:
        py = self._cached_script_python()
        if py is None:
            raise RuntimeError("无法启动 WebView：未找到可用的 Python。")
        webview_cmd = [*py, "-c", _WEBVIEW_LAUNCHER, url]
        subprocess.Popen(
            webview_cmd,
            cwd=str(self._plugin_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(sys.platform != "win32"),
        )

    def _status_dict(self) -> dict[str, Any]:
        state = self._reconcile_runtime_state()
        url = state.get("url")
        port = state.get("port")
        mode = state.get("mode")
        ui_mode = state.get("ui")
        pid = state.get("shell_pid") or state.get("pid")
        running = bool(state) and self._runtime_alive(state)
        can_spawn = self._cached_script_python() is not None
        access_url = None
        if running and url:
            access_url = str(url)
        elif running and port:
            access_url = f"http://127.0.0.1:{port}"
        if not running:
            if not can_spawn:
                hint = (
                    "就绪（Mode B）：将嵌入 HTTP 服务；启动后请在浏览器访问 "
                    "http://127.0.0.1:<端口>（启动完成后见下方 URL/端口）"
                )
            else:
                hint = "就绪（Mode A）：启动后将优先打开独立 WebView；关闭窗口即停止服务"
        elif mode == "B":
            target = access_url or "见下方 URL"
            if ui_mode == "browser":
                hint = f"已在运行（Mode B）：请在浏览器打开 {target}"
            else:
                hint = (
                    f"已在运行（Mode B）：HTTP 已嵌入插件进程；若未看到窗口，"
                    f"请在浏览器打开 {target}"
                )
        else:
            hint = "已在运行"
            if ui_mode == "browser":
                hint = f"已在运行（浏览器模式）：请在浏览器打开 {access_url or '见下方 URL'}"
            elif ui_mode == "webview":
                hint = "已在运行（Mode A / WebView）；关闭窗口即停止服务，需重新启动"
        return {
            "running": running,
            "mode": mode,
            "ui": ui_mode,
            "pid": pid if running else None,
            "port": port if running else None,
            "url": url if running else None,
            "access_url": access_url if running else None,
            "data_dir": str(self.data_path()),
            "neko_root": state.get("neko_root"),
            "code_dir": state.get("code_dir"),
            "last_error": state.get("last_error"),
            "can_spawn_python": can_spawn,
            "hint": hint if not state.get("last_error") else f"{hint}；上次错误: {state.get('last_error')}",
        }

    def _start_work(
        self,
        *,
        neko_root: Path,
        code_dir: Path,
        import_root: Path,
    ) -> dict[str, Any]:
        return self._start_with_fallback(
            neko_root=neko_root,
            code_dir=code_dir,
            import_root=import_root,
        )

    def _user_data_dir(self) -> Path:
        override = os.environ.get("NEKO_TESTBENCH_DATA_DIR")
        if override:
            return Path(override).expanduser().resolve()
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
            return Path(base) / "NEKO-Testbench"
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "NEKO-Testbench"
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return Path(xdg) / "NEKO-Testbench"
        return Path.home() / ".local" / "share" / "NEKO-Testbench"

    def _clean_env(self, *, neko_root: Path, import_root: Path, vendor: Path | None) -> dict[str, str]:
        deny = {"VIRTUAL_ENV", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT"}
        env = {k: v for k, v in os.environ.items() if k not in deny}
        parts = [str(import_root), str(neko_root)]
        if vendor and vendor.is_dir():
            parts.insert(0, str(vendor))
        env["PYTHONPATH"] = os.pathsep.join(parts)
        env["NEKO_PROJECT_ROOT"] = str(neko_root)
        env["NEKO_TESTBENCH_DATA_DIR"] = str(self._user_data_dir())
        return env

    def _start_mode_a(
        self,
        *,
        python_prefix: list[str],
        neko_root: Path,
        code_dir: Path,
        import_root: Path,
    ) -> dict[str, Any]:
        port = _pick_port()
        vendor = self._plugin_dir / "vendor"
        shell = self._plugin_dir / "shell_main.py"
        ready = self._ready_path()
        if ready.is_file():
            try:
                ready.unlink()
            except OSError:
                pass
        log_path = self._log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            *python_prefix,
            str(shell),
            "--neko-root",
            str(neko_root),
            "--code-dir",
            str(code_dir),
            "--import-root",
            str(import_root),
            "--user-data",
            str(self._user_data_dir()),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--ready-file",
            str(ready),
        ]
        if vendor.is_dir():
            cmd.extend(["--vendor", str(vendor)])
        env = self._clean_env(
            neko_root=neko_root,
            import_root=import_root,
            vendor=vendor if vendor.is_dir() else None,
        )
        log_f = open(log_path, "w", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self._plugin_dir),
                env=env,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=(
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
                ),
                start_new_session=(sys.platform != "win32"),
            )
        except OSError as exc:
            log_f.close()
            raise RuntimeError(f"spawn shell failed: {exc}") from exc
        self._shell_proc = proc

        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 90.0
        ui_mode = "webview"
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                log_f.close()
                tail = ""
                try:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-800:]
                except OSError:
                    pass
                raise RuntimeError(f"shell exited early ({proc.returncode}): {tail}")
            if _health_ok(f"{url}/healthz"):
                if ready.is_file():
                    try:
                        ready_data = json.loads(ready.read_text(encoding="utf-8"))
                        ui_mode = str(ready_data.get("ui") or ui_mode)
                        url = str(ready_data.get("url") or url)
                        port = int(ready_data.get("port") or port)
                        if ui_mode in {"browser", "webview"}:
                            break
                    except (OSError, json.JSONDecodeError, ValueError):
                        pass
            time.sleep(0.3)
        else:
            proc.terminate()
            log_f.close()
            raise RuntimeError("healthz timeout")

        try:
            log_f.close()
        except Exception:  # noqa: BLE001
            pass

        return {
            "mode": "A",
            "ui": ui_mode,
            "url": url,
            "port": port,
            "shell_pid": proc.pid,
            "neko_root": str(neko_root),
            "code_dir": str(code_dir),
            "last_error": None,
        }

    def _start_mode_b(self, *, neko_root: Path, code_dir: Path, import_root: Path) -> dict[str, Any]:
        vendor = self._plugin_dir / "vendor"
        for path in (vendor if vendor.is_dir() else None, import_root, neko_root):
            if path is None:
                continue
            value = str(path.resolve())
            if value not in sys.path:
                sys.path.insert(0, value)
        sys.path[:] = [p for p in sys.path if Path(p).resolve() != code_dir.resolve()]

        from .path_bootstrap import apply_plugin_patches

        user_data = self._user_data_dir()
        apply_plugin_patches(neko_root=neko_root, code_dir=code_dir, user_data_dir=user_data)

        port = _pick_port()
        host = "127.0.0.1"
        url = f"http://{host}:{port}"

        from .embed_runtime import load_testbench_app_stack

        uvicorn, live_runtime_log, app = load_testbench_app_stack()

        live_runtime_log.rotate_for_boot()
        live_runtime_log.install()
        config = uvicorn.Config(app, host=host, port=port, log_level="info", reload=False)
        server = uvicorn.Server(config)
        self._embed_server = server

        def _run() -> None:
            try:
                server.run()
            finally:
                try:
                    live_runtime_log.close()
                except Exception:  # noqa: BLE001
                    pass

        thread = threading.Thread(target=_run, name="testbench-embed-uvicorn", daemon=True)
        thread.start()
        self._embed_thread = thread

        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            if _health_ok(f"{url}/healthz"):
                break
            if not thread.is_alive():
                raise RuntimeError("embedded uvicorn died during startup")
            time.sleep(0.25)
        else:
            server.should_exit = True  # type: ignore[attr-defined]
            raise RuntimeError("embedded healthz timeout")

        ui_mode = "browser"
        py = self._cached_script_python()
        if py is not None:
            try:
                subprocess.Popen(
                    [*py, "-c", _WEBVIEW_LAUNCHER, url],
                    cwd=str(self._plugin_dir),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=(sys.platform != "win32"),
                )
                ui_mode = "webview"
            except OSError:
                ui_mode = "browser"

        return {
            "mode": "B",
            "ui": ui_mode,
            "url": url,
            "port": port,
            "shell_pid": None,
            "neko_root": str(neko_root),
            "code_dir": str(code_dir),
            "last_error": None,
            "open_url_hint": url if ui_mode == "browser" else None,
        }

    def _start_with_fallback(
        self,
        *,
        neko_root: Path,
        code_dir: Path,
        import_root: Path,
    ) -> dict[str, Any]:
        py = self._cached_script_python()
        if py is not None:
            try:
                return self._start_mode_a(
                    python_prefix=py,
                    neko_root=neko_root,
                    code_dir=code_dir,
                    import_root=import_root,
                )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("Mode A failed (%s); falling back to Mode B", exc)
        return self._start_mode_b(
            neko_root=neko_root,
            code_dir=code_dir,
            import_root=import_root,
        )

    @lifecycle(id="startup")
    async def startup(self, **_) -> None:
        self._script_python = await asyncio.to_thread(find_script_python)
        self.logger.info("Testbench driver ready (can_spawn=%s)", self._script_python is not None)

    @lifecycle(id="shutdown")
    async def shutdown(self, **_) -> None:
        await self._stop_impl()

    @ui.context(id="dashboard")
    async def dashboard(self, **_) -> dict[str, Any]:
        return await asyncio.to_thread(self._status_dict)

    @ui.action(label="启动 Testbench", tone="primary", group="control", order=10, refresh_context=True)
    @plugin_entry(
        id="start",
        name="Start Testbench",
        description="Start Testbench (prefer WebView window)",
        timeout=120.0,
    )
    async def start(self, **_) -> Any:
        status = await asyncio.to_thread(self._status_dict)
        if status["running"]:
            return Ok({**status, "message": "Testbench 已在运行"})
        try:
            neko_root = _resolve_neko_root_for_start(self._plugin_dir)
            if neko_root is None:
                return Err(
                    SdkError(
                        "无法定位 NEKO 工程根，且插件包内缺少 bundled/tests/testbench。",
                        code="NEKO_ROOT",
                    )
                )
            compat_err = _check_compatible_neko(self._plugin_dir)
            if compat_err:
                return Err(SdkError(compat_err, code="INCOMPATIBLE_NEKO"))
            code_dir, import_root = _resolve_code_layout(self._plugin_dir, neko_root)
            state = await asyncio.to_thread(
                self._start_work,
                neko_root=neko_root,
                code_dir=code_dir,
                import_root=import_root,
            )
            self._save_state(state)
            return Ok({**await asyncio.to_thread(self._status_dict), "message": "已启动 Testbench"})
        except Exception as exc:  # noqa: BLE001
            self.logger.exception("Testbench start failed")
            fail = {"last_error": f"{type(exc).__name__}: {exc}", "running": False}
            prev = self._load_state()
            prev.update(fail)
            self._save_state(prev)
            return Err(SdkError(str(exc), code="START_FAILED"))

    @ui.action(label="停止", tone="danger", group="control", order=20, refresh_context=True)
    @plugin_entry(id="stop", name="Stop Testbench", description="Stop Testbench server / window")
    async def stop(self, **_) -> Any:
        await self._stop_impl()
        return Ok({**await asyncio.to_thread(self._status_dict), "message": "已请求停止"})

    async def _stop_impl(self) -> None:
        state = self._load_state()
        if self._embed_server is not None:
            try:
                self._embed_server.should_exit = True
            except Exception:  # noqa: BLE001
                pass
            self._embed_server = None
        if self._embed_thread is not None:
            self._embed_thread.join(timeout=8)
            self._embed_thread = None

        proc = self._shell_proc
        if proc is not None and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    await asyncio.to_thread(_windows_taskkill, proc.pid)
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._shell_proc = None

        pid = state.get("shell_pid")
        if pid and _pid_alive(int(pid)) and state.get("mode") == "A":
            try:
                if sys.platform == "win32":
                    await asyncio.to_thread(_windows_taskkill, int(pid))
                else:
                    os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
        self._clear_state()

    @plugin_entry(id="open", name="Open UI", description="Focus WebView or return URL for external open")
    async def open_ui(self, **_) -> Any:
        status = await asyncio.to_thread(self._status_dict)
        if not status["running"] or not status.get("url"):
            return Err(
                SdkError(
                    "Testbench 未在运行（窗口关闭后服务已停止），请先重新启动。",
                    code="NOT_RUNNING",
                )
            )
        url = str(status["url"])
        if status.get("ui") == "webview":
            try:
                await asyncio.to_thread(self._spawn_webview_window, url)
                return Ok({**status, "message": "已打开 WebView 窗口"})
            except Exception as exc:  # noqa: BLE001
                return Ok(
                    {
                        **status,
                        "message": f"WebView 打开失败，改用浏览器：{exc}",
                        "open_external_url": url,
                    }
                )
        return Ok(
            {
                **status,
                "message": "请在宿主打开 URL",
                "open_external_url": url,
            }
        )

    @ui.action(label="刷新状态", tone="secondary", group="control", order=30, refresh_context=True)
    @plugin_entry(id="status", name="Status", description="Query Testbench driver status")
    async def status(self, **_) -> Any:
        return Ok(await asyncio.to_thread(self._status_dict))


# Back-compat alias for older entry strings / docs.
TestbenchLauncherPlugin = TestbenchDriverPlugin
