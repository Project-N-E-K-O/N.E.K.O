from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class QQNapcatService:
    def __init__(self, plugin: Any):
        self.plugin = plugin

    def get_configured_napcat_path(self) -> str:
        return str((self.plugin._qq_settings or {}).get("napcat_directory") or "").strip()

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
        target = self.plugin.config_dir / "static" / "cache" / "qrcode.png"
        if not source.is_file():
            return False
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(shutil.copy2, source, target)
            return True
        except Exception as e:
            self.plugin.logger.warning(f"Failed to copy NapCat QR code into static cache: {e}")
            return False

    def find_napcat_launcher(self) -> Path | None:
        launch_target = self.get_napcat_launch_target()
        if launch_target.is_file():
            return launch_target
        root = launch_target
        candidates = [
            root / "launcher-user.bat", root / "launcher.bat",
            root / "launcher-user.sh", root / "launcher.sh",
            root / "napcat", root / "napcat.sh",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _build_missing_launcher_error(self) -> str:
        launch_target = self.get_napcat_launch_target()
        configured = str((self.plugin._qq_settings or {}).get("napcat_directory") or "").strip()
        if configured:
            return f"NapCat 启动器不存在: {launch_target}，需要指向 launcher-user.bat、launcher.bat 或其所在目录"
        return f"NapCat 启动器不存在: {launch_target}，请先配置 napcat_directory 或确认内置 NapCat.Shell 完整"

    async def start_health_check(self, interval_seconds: float = 30.0) -> None:
        """后台守护：NapCat 进程死了自动重启。"""
        import asyncio as _asyncio
        async def _loop():
            await _asyncio.sleep(10)
            while True:
                try:
                    await _asyncio.sleep(interval_seconds)
                except _asyncio.CancelledError:
                    break
                if not self.plugin._manages_napcat_process:
                    continue
                p = self.plugin._napcat_process
                if p and p.returncode is None:
                    continue  # 进程还活着
                self.plugin._emit_log("WARN", "NapCat 进程已退出，尝试重启...")
                try:
                    await self.ensure_napcat_started()
                    ready = await self.wait_for_onebot_ready()
                    if ready:
                        await self.sync_napcat_qrcode_into_static()
                        self.plugin._emit_log("INFO", "NapCat 已自动重启成功")
                    else:
                        self.plugin._emit_log("WARN", "NapCat 已重启但 OneBot 尚未就绪")
                except Exception as e:
                    self.plugin._emit_log("ERROR", f"NapCat 自动重启失败: {e}")
        self._health_task = _asyncio.create_task(_loop())

    async def stop_health_check(self) -> None:
        task = getattr(self, "_health_task", None)
        if task and not task.done():
            task.cancel()
            try: await task
            except (Exception, asyncio.CancelledError): pass

    def clear_startup_error(self) -> None:
        self.plugin._startup_error = None

    def get_startup_error(self) -> str:
        return str(self.plugin._startup_error or "").strip()

    def _set_startup_error(self, message: str) -> None:
        self.plugin._startup_error = str(message or "").strip() or None

    def _extract_onebot_port(self) -> int | None:
        raw_url = str((self.plugin._qq_settings or {}).get("onebot_url") or "").strip()
        if not raw_url and self.plugin.qq_client:
            raw_url = str(getattr(self.plugin.qq_client, "onebot_url", "") or "").strip()
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
        """等待 Napcat 客户端连接到此服务器的反向 WS

        反向 WS 模式下，我们不再主动 TCP 连接外部端口，而是轮询是否有
        OneBot 客户端连接到了我们的服务器。
        """
        if self.plugin.qq_client and self.plugin.qq_client.is_connected():
            self.clear_startup_error()
            return True
        deadline = asyncio.get_running_loop().time() + max(1.0, float(timeout_seconds or 20.0))
        while asyncio.get_running_loop().time() < deadline:
            if self.plugin.qq_client and self.plugin.qq_client.is_connected():
                self.clear_startup_error()
                return True
            await asyncio.sleep(max(0.1, float(poll_interval or 0.5)))
        self._set_startup_error("NapCat 已尝试启动，但没有客户端连接到反向 WS 服务器")
        return False

    def _napcat_log_dir(self) -> Path:
        return self.get_napcat_directory() / "logs"

    def get_webui_url(self) -> str:
        """从 NapCat config/webui.json 构造 WebUI URL"""
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
        """返回 NapCat WebUI 访问信息"""
        url = self.get_webui_url()
        if url:
            return [f"NapCat WebUI: {url}"]
        return []

    async def ensure_napcat_started(self) -> None:
        configured_path = self.get_configured_napcat_path()
        if not configured_path:
            return
        if self.plugin._napcat_process and self.plugin._napcat_process.returncode is None:
            return
        launcher = self.find_napcat_launcher()
        if launcher is None:
            self._set_startup_error(self._build_missing_launcher_error())
            return
        try:
            import platform as _platform, os as _os
            is_windows = _platform.system() == "Windows"
            show_window = False
            if is_windows:
                show_window = bool(self.plugin._qq_settings.get("show_napcat_window", True))
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010) if show_window else getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                self.plugin._napcat_process = await asyncio.create_subprocess_exec(
                    "cmd.exe", "/c", str(launcher),
                    cwd=str(launcher.parent),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            else:
                # Linux/macOS: 直接执行，launcher 应为可执行脚本
                launcher_path = str(launcher)
                if launcher_path.endswith(".bat"):
                    launcher_path = launcher_path[:-4] + ".sh"
                if not _os.access(launcher_path, _os.X_OK):
                    try: _os.chmod(launcher_path, 0o755)
                    except Exception: pass
                self.plugin._napcat_process = await asyncio.create_subprocess_exec(
                    launcher_path,
                    cwd=str(launcher.parent),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            self.plugin._manages_napcat_process = True
            self.clear_startup_error()
            await self.start_health_check()  # NapCat 启动后自动开始健康监测
            pid = self.plugin._napcat_process.pid
            self.plugin.logger.info(
                f"Started NapCat: {launcher} (pid={pid}, show_window={show_window})"
            )
            self.plugin._emit_log("INFO", f"NapCat 已启动 PID={pid}")

            async def _delayed_sync_qrcode():
                await asyncio.sleep(1.5)
                await self.sync_napcat_qrcode_into_static()

            asyncio.create_task(_delayed_sync_qrcode())
        except Exception as e:
            self._set_startup_error(f"启动 NapCat 失败: {e}")
            self.plugin.logger.warning(f"Failed to start NapCat launcher {launcher}: {e}")

    async def stop_managed_napcat(self) -> None:
        if not self.plugin._manages_napcat_process:
            return
        process = self.plugin._napcat_process
        self.plugin._napcat_process = None
        self.plugin._manages_napcat_process = False
        if not process or process.returncode is not None:
            return
        pid = process.pid
        try:
            import platform as _platform
            if _platform.system() == "Windows":
                kill_proc = await asyncio.create_subprocess_exec(
                    "taskkill", "/PID", str(pid), "/T", "/F",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
            else:
                try: process.terminate()
                except Exception: pass
                try: await asyncio.wait_for(process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    try: process.kill()
                    except Exception: pass
            self.plugin._emit_log("INFO", f"NapCat 进程已终止 PID={pid}")
        except Exception as e:
            self.plugin.logger.warning(f"Failed to kill NapCat process tree (PID={pid}): {e}")
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
