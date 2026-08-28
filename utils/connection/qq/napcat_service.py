from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


class QQNapcatService:
    """NapCat process / QR / readiness management -- a pure transport component that
    depends on no plugin object.

    Decoupled by injecting ``get_settings``/``get_qq_client``/``config_dir``/
    ``logger``/``emit_log``; the process and startup-error state is owned here
    (``_napcat_process``/``_manages_napcat_process``/``_startup_error``), so any
    plugin can create it to manage NapCat without exposing its own internals.
    """

    #: OneBot connect-timeout error -- transient: NapCat may still be starting
    #: (QR login / slow start) and connect later. It must not be treated as a hard
    #: failure that short-circuits retry, otherwise a genuinely starting NapCat
    #: would stop being polled for the late connection.
    TRANSIENT_TIMEOUT_ERROR = "NapCat 已尝试启动，但没有客户端连接到反向 WS 服务器"
    FORWARD_TRANSIENT_TIMEOUT_ERROR = "NapCat 已启动，但正向 WebSocket 连接未建立（NapCat 可能仍在登录，或未开启 WebSocket 服务器）"

    def __init__(
        self,
        *,
        get_settings: Callable[[], dict] | None = None,
        get_qq_client: Callable[[], Any] | None = None,
        config_dir: str | Path | None = None,
        logger: Any = None,
        emit_log: Any = None,
    ):
        self._get_settings = get_settings or (lambda: {})
        self._get_qq_client = get_qq_client or (lambda: None)
        self._config_dir = Path(config_dir) if config_dir else (Path(__file__).parent / "static")
        self.logger = logger
        self._emit_log = emit_log or (lambda level, msg: None)
        # Process / error state owned by the transport layer.
        self._napcat_process: asyncio.subprocess.Process | None = None
        self._manages_napcat_process: bool = False
        self._startup_error: str | None = None

    @property
    def napcat_process(self) -> asyncio.subprocess.Process | None:
        return self._napcat_process

    @property
    def manages_napcat_process(self) -> bool:
        return self._manages_napcat_process

    def _transient_timeout_errors(self) -> set[str]:
        """All transient timeout texts, across connection modes (reverse + forward).

        Deciding whether a saved timeout error is transient must be **independent**
        of the current mode: a forward-mode timeout written before switching to
        reverse would otherwise be re-classified as a hard failure and
        ``wait_for_onebot_ready`` would short-circuit instead of polling the late
        connection.
        """
        return {self.TRANSIENT_TIMEOUT_ERROR, self.FORWARD_TRANSIENT_TIMEOUT_ERROR}

    def _transient_timeout_error(self) -> str:
        """OneBot connect-timeout text, chosen by mode (the one written at set time).

        Reverse: no client connected to our reverse WS server;
        forward: our dial-out has not reached NapCat (process still starting /
        logging in, or NapCat's WS server is off). Both are transient, not hard.
        """
        mode = str((self._get_settings() or {}).get("qq_connection_mode") or "napcat").strip()
        if mode == "napcat_forward":
            return self.FORWARD_TRANSIENT_TIMEOUT_ERROR
        return self.TRANSIENT_TIMEOUT_ERROR

    def has_hard_startup_error(self) -> bool:
        """Whether the failure is a "hard failure" -- retry is pointless
        (missing dir / launcher / process won't start).

        OneBot connect timeouts (reverse/forward texts) are transient -- NapCat may
        still be starting, so they are not hard: retry keeps polling for the late
        connection instead of short-circuiting.
        """
        err = self.get_startup_error()
        return bool(err) and err not in self._transient_timeout_errors()

    def get_configured_napcat_path(self) -> str:
        return str((self._get_settings() or {}).get("napcat_directory") or "").strip()

    def get_napcat_directory(self) -> Path:
        configured = self.get_configured_napcat_path()
        if configured:
            configured_path = Path(configured)
            if configured_path.is_file():
                return configured_path.parent
            return configured_path
        return Path(__file__).parent / "NapCat.Shell"

    def get_napcat_launch_target(self) -> Path:
        configured = self.get_configured_napcat_path()
        if configured:
            return Path(configured)
        return self.get_napcat_directory()

    def get_napcat_qrcode_path(self) -> Path:
        return self.get_napcat_directory() / "cache" / "qrcode.png"

    async def sync_napcat_qrcode_into_static(self) -> bool:
        source = self.get_napcat_qrcode_path()
        target = self._config_dir / "static" / "cache" / "qrcode.png"
        if not source.is_file():
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, source, target)
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to copy NapCat QR code into static cache: {e}")
            return False

    def find_napcat_launcher(self) -> Path | None:
        launch_target = self.get_napcat_launch_target()
        if launch_target.is_file():
            return launch_target
        root = launch_target
        candidates = [
            root / "launcher-user.bat",
            root / "launcher.bat",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _build_missing_launcher_error(self) -> str:
        launch_target = self.get_napcat_launch_target()
        configured = str((self._get_settings() or {}).get("napcat_directory") or "").strip()
        if configured:
            return f"NapCat 启动器不存在: {launch_target}，需要指向 launcher-user.bat、launcher.bat 或其所在目录"
        return f"NapCat 启动器不存在: {launch_target}，请先配置 napcat_directory 或确认内置 NapCat.Shell 完整"

    def clear_startup_error(self) -> None:
        self._startup_error = None

    def get_startup_error(self) -> str:
        return str(self._startup_error or "").strip()

    def set_startup_error(self, message: str | None) -> None:
        self._startup_error = str(message or "").strip() or None

    def _set_startup_error(self, message: str) -> None:
        self.set_startup_error(message)

    def _extract_onebot_port(self) -> int | None:
        raw_url = str((self._get_settings() or {}).get("onebot_url") or "").strip()
        if not raw_url:
            qq_client = self._get_qq_client()
            raw_url = str(getattr(qq_client, "onebot_url", "") or "").strip()
        if not raw_url:
            return None
        if raw_url.startswith("ws://"):
            raw_url = raw_url[5:]
        elif raw_url.startswith("wss://"):
            raw_url = raw_url[6:]
        host_port = raw_url.split("/", 1)[0]
        if ":" not in host_port:
            return 443 if raw_url.startswith("wss://") else 80
        try:
            return int(host_port.rsplit(":", 1)[1])
        except ValueError:
            return None

    async def wait_for_onebot_ready(self, *, timeout_seconds: float = 20.0, poll_interval: float = 0.5) -> bool:
        """Wait for NapCat to connect to this server's reverse WS.

        In reverse WS mode we don't dial the external port; we poll whether an
        OneBot client has connected to our server.
        """
        qq_client = self._get_qq_client()
        if qq_client and qq_client.is_connected():
            self.clear_startup_error()
            return True
        # Hard failure (missing dir / launcher / process won't start) returns
        # immediately instead of idle-waiting the full timeout -- otherwise the
        # frontend would report a false timeout while NapCat never started. OneBot
        # connect timeout is transient (NapCat may still be starting), not hard:
        # retry keeps polling for the late connection.
        if self.has_hard_startup_error():
            return False
        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_seconds or 20.0))
        while asyncio.get_running_loop().time() < deadline:
            qq_client = self._get_qq_client()
            if qq_client and qq_client.is_connected():
                self.clear_startup_error()
                return True
            # If the launcher gets flagged hard during polling, short-circuit rather
            # than idling out the whole window.
            if self.has_hard_startup_error():
                return False
            await asyncio.sleep(max(0.1, float(poll_interval or 0.5)))
            # Sleep may cross the deadline; during it OneBot may have connected or a
            # hard error may have been written. Returning to the loop top would exit
            # because the while condition is now False, so do one final check here to
            # avoid reporting a false timeout or overwriting a real startup error.
            qq_client = self._get_qq_client()
            if qq_client and qq_client.is_connected():
                self.clear_startup_error()
                return True
            if self.has_hard_startup_error():
                return False
        self._set_startup_error(self._transient_timeout_error())
        return False

    def _napcat_log_dir(self) -> Path:
        return self.get_napcat_directory() / "logs"

    def get_webui_url(self) -> str:
        """Build the WebUI URL from NapCat config/webui.json."""
        import json as _json
        napcat_dir = self.get_napcat_directory()
        webui_json = napcat_dir / "config" / "webui.json"
        if not webui_json.exists():
            return ""
        try:
            with open(webui_json, "r", encoding="utf-8") as f:
                cfg = _json.loads(f.read())
            host = str(cfg.get("host") or "127.0.0.1").strip()
            if host in ("::", "0.0.0.0", ""):
                host = "127.0.0.1"
            port = int(cfg.get("port") or 6099)
            token = str(cfg.get("token") or "").strip()
            if token:
                return f"http://{host}:{port}/webui?token={token}"
            return f"http://{host}:{port}/webui"
        except Exception:
            return ""

    async def _read_napcat_webui_lines(self) -> list[str]:
        """Return NapCat WebUI access info."""
        url = self.get_webui_url()
        if url:
            return [f"NapCat WebUI: {url}"]
        return []

    async def ensure_napcat_started(self) -> None:
        # After a hard failure (missing dir / launcher / process won't start) don't
        # retry: retrying is pointless and only repeats the error + relaunch attempt,
        # and the frontend gets no clear failure reason.
        if self.has_hard_startup_error():
            return
        configured_path = self.get_configured_napcat_path()
        if not configured_path:
            # napcat_directory unset -> don't auto-start NapCat (user may launch it
            # manually). This is not a hard failure: wait_for_onebot_ready still polls
            # for a manually started OneBot, so don't set a hard error that would
            # prevent ensure_napcat from completing that connection.
            return
        if self._napcat_process and self._napcat_process.returncode is None:
            return
        launcher = self.find_napcat_launcher()
        if launcher is None:
            mode = str((self._get_settings() or {}).get("qq_connection_mode") or "napcat").strip()
            if mode == "napcat_forward":
                # For forward mode, local NapCat launch is **best-effort**: a missing
                # launcher only warns and doesn't set a hard error -- forward can still
                # connect to a remote / manually started NapCat, and bootstrap() should
                # not enter the failure branch (wait_for_onebot_ready polls the forward
                # dial result).
                self._emit_log("WARN", self._build_missing_launcher_error())
                return
            self._set_startup_error(self._build_missing_launcher_error())
            return
        try:
            show_window = bool((self._get_settings() or {}).get("show_napcat_window", True))
            creationflags = 0
            if show_window:
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            else:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            self._napcat_process = await asyncio.create_subprocess_exec(
                "cmd.exe", "/c", str(launcher),
                cwd=str(launcher.parent),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._manages_napcat_process = True
            self.clear_startup_error()
            pid = self._napcat_process.pid
            if self.logger:
                self.logger.info(
                    f"Started NapCat: {launcher} (pid={pid}, show_window={show_window})"
                )
            self._emit_log("INFO", f"NapCat 已启动 PID={pid}")

            async def _delayed_sync_qrcode():
                await asyncio.sleep(1.5)
                await self.sync_napcat_qrcode_into_static()

            asyncio.create_task(_delayed_sync_qrcode())
        except Exception as e:
            self._set_startup_error(f"启动 NapCat 失败: {e}")
            if self.logger:
                self.logger.warning(f"Failed to start NapCat launcher {launcher}: {e}")

    async def stop_managed_napcat(self) -> None:
        if not self._manages_napcat_process:
            return
        process = self._napcat_process
        self._napcat_process = None
        self._manages_napcat_process = False
        if not process or process.returncode is not None:
            return
        pid = process.pid
        try:
            # Use /T to kill the process tree recursively so NapCat and its cmd
            # wrapper both terminate.
            kill_proc = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await kill_proc.wait()
            self._emit_log("INFO", f"NapCat 进程树已终止 PID={pid}")
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to kill NapCat process tree (PID={pid}): {e}")
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
