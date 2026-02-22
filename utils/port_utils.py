# -*- coding: utf-8 -*-
"""
N.E.K.O. 端口探测与健康校验工具。

提供以下能力：
- 通过 /health 探测并校验 N.E.K.O 指纹
- 启动锁（Windows 命名互斥体 / 跨平台文件锁）
- Windows 上 Hyper-V 保留端口范围检测
"""

import json
import logging
import os
import socket
import sys
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
#  N.E.K.O. 健康指纹
# ---------------------------------------------------------------------------

HEALTH_APP_SIGNATURE = "N.E.K.O"


def build_health_response(
    service: str,
    *,
    instance_id: str = "",
    version: str = "",
    extra: dict | None = None,
) -> dict:
    """构建统一的 /health 响应结构。

    所有 N.E.K.O HTTP 服务都应返回该格式，便于 launcher
    与前端区分“真实后端”和“其他占用进程”。
    """
    resp = {
        "app": HEALTH_APP_SIGNATURE,
        "service": service,
        "status": "ok",
        "instance_id": instance_id or os.getenv("NEKO_INSTANCE_ID", ""),
    }
    if version:
        resp["version"] = version
    if extra:
        resp.update(extra)
    return resp


def probe_neko_health(
    port: int,
    *,
    host: str = "127.0.0.1",
    timeout: float = 1.0,
) -> Optional[dict]:
    """对指定端口执行 ``GET /health``。

    若响应为合法 N.E.K.O 服务则返回解析后的 JSON，
    否则返回 ``None``。

    这里使用原生 socket，避免 launcher 引入 ``httpx`` / ``requests``，
    保持启动器轻量。
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        request_line = (
            f"GET /health HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        sock.sendall(request_line.encode("utf-8"))

        # 读取响应（兼容 chunked，直到连接关闭）
        chunks: list[bytes] = []
        while True:
            try:
                data = sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
            except socket.timeout:
                break
        sock.close()

        raw = b"".join(chunks).decode("utf-8", errors="replace")
        # 分离响应头与响应体
        if "\r\n\r\n" not in raw:
            return None
        _, body = raw.split("\r\n\r\n", 1)

        # 处理 chunked 传输编码（常见单块场景）
        body = body.strip()
        if body and body[0].isdigit() and "\r\n" in body:
            # 可能是分块格式："<十六进制长度>\r\n<数据>\r\n0\r\n"
            _size_line, rest = body.split("\r\n", 1)
            body = rest.rsplit("\r\n0", 1)[0] if "\r\n0" in rest else rest

        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("app") == HEALTH_APP_SIGNATURE:
            return payload
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
#  Hyper-V 保留端口范围检测（仅 Windows）
# ---------------------------------------------------------------------------

def get_hyperv_excluded_ranges() -> list[tuple[int, int]]:
    """返回 Hyper-V / WSL 保留端口区间列表（start, end）。

    在非 Windows 或查询失败时返回空列表。
    """
    if sys.platform != "win32":
        return []
    try:
        import subprocess

        result = subprocess.run(
            ["netsh", "interface", "ipv4", "show", "excludedportrange", "protocol=tcp"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        ranges: list[tuple[int, int]] = []
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                ranges.append((int(parts[0]), int(parts[1])))
        return ranges
    except Exception:
        return []


def is_port_in_excluded_range(port: int, excluded: list[tuple[int, int]] | None = None) -> bool:
    """检查端口是否落在 Hyper-V 保留区间内。"""
    if excluded is None:
        excluded = get_hyperv_excluded_ranges()
    return any(lo <= port <= hi for lo, hi in excluded)


# ---------------------------------------------------------------------------
#  启动锁
# ---------------------------------------------------------------------------

_LOCK_NAME = r"Global\NEKO_LAUNCHER_STARTUP_LOCK"
_lock_handle = None  # Windows mutex handle
_lock_fd = None  # POSIX file lock fd


def acquire_startup_lock() -> bool:
    """尝试获取系统级单实例启动锁。

    返回 ``True`` 表示获取成功（可继续启动）。
    返回 ``False`` 表示已有其他 launcher 持有该锁。
    """
    global _lock_handle, _lock_fd

    if sys.platform == "win32":
        return _acquire_win32_mutex()
    else:
        return _acquire_file_lock()


def release_startup_lock() -> None:
    """释放启动锁（尽力而为）。"""
    global _lock_handle, _lock_fd

    if sys.platform == "win32":
        _release_win32_mutex()
    else:
        _release_file_lock()


# -- Windows 命名互斥体 ----------------------------------------------------

def _acquire_win32_mutex() -> bool:
    global _lock_handle
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        ERROR_ALREADY_EXISTS = 183

        handle = kernel32.CreateMutexW(None, True, _LOCK_NAME)
        last_err = kernel32.GetLastError()

        if handle and last_err != ERROR_ALREADY_EXISTS:
            _lock_handle = handle
            return True
        # 说明已有其他实例持有互斥体
        if handle:
            kernel32.CloseHandle(handle)
        return False
    except Exception:
        # 无法判定时，默认允许继续（避免误阻断）
        return True


def _release_win32_mutex() -> None:
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.ReleaseMutex(_lock_handle)
        kernel32.CloseHandle(_lock_handle)
    except Exception:
        pass
    _lock_handle = None


# -- POSIX 文件锁 ---------------------------------------------------------

_LOCK_FILE = os.path.join(tempfile.gettempdir(), "neko_launcher.lock")


def _acquire_file_lock() -> bool:
    global _lock_fd
    try:
        import fcntl

        _lock_fd = open(_LOCK_FILE, "w")
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (OSError, IOError):
        return False
    except ImportError:
        # POSIX 上通常应有 fcntl，这里仅做兜底
        return True


def _release_file_lock() -> None:
    global _lock_fd
    if _lock_fd is None:
        return
    try:
        import fcntl

        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
        _lock_fd.close()
    except Exception:
        pass
    _lock_fd = None
    try:
        os.unlink(_LOCK_FILE)
    except Exception:
        pass
