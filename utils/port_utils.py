# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
N.E.K.O. port probing and health-check utilities.

Capabilities:
- probe /health and verify the N.E.K.O fingerprint
- single-instance startup lock (delegated to ``utils.single_instance``)
- detection of Hyper-V reserved port ranges on Windows
"""

import json
import os
import socket
import sys
from typing import Optional

from utils.logger_config import get_module_logger

logger = get_module_logger(__name__)

# ---------------------------------------------------------------------------
#  N.E.K.O. 健康指纹
# ---------------------------------------------------------------------------

HEALTH_APP_SIGNATURE = "N.E.K.O"


def set_port_probe_reuse(sock: socket.socket) -> None:
    """Align bind probes with runtime server behavior as closely as practical.

    On POSIX, asyncio/uvicorn listeners enable ``SO_REUSEADDR`` by default, which
    allows rebinding while prior connections are still in ``TIME_WAIT``.
    The launcher's plain bind probes should mirror that; otherwise they can report
    a false port conflict even though the actual server can bind immediately.

    On Windows we intentionally leave the socket untouched here. The default
    ``asyncio.create_server()`` path does not enable ``SO_REUSEADDR`` there, and
    Windows' ``SO_REUSEADDR`` semantics are broad enough to allow address sharing
    in ways we do not want for local control-plane ports.
    """
    if os.name == "posix" and sys.platform != "cygwin":
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            pass


def build_health_response(
    service: str,
    *,
    instance_id: str = "",
    version: str = "",
    extra: dict | None = None,
) -> dict:
    """Build the unified /health response structure.

    All N.E.K.O HTTP services should return this format, helping the launcher
    and frontend distinguish "the real backend" from "some other occupying process".
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
        # 合并附加字段，但禁止覆盖核心签名键
        _reserved = {"app", "service", "status", "instance_id"}
        resp.update({k: v for k, v in extra.items() if k not in _reserved})
    return resp


def probe_neko_health(
    port: int,
    *,
    host: str = "127.0.0.1",
    timeout: float = 1.0,
) -> Optional[dict]:
    """Perform a ``GET /health`` against the given port.

    Returns the parsed JSON if the response is a legitimate N.E.K.O service,
    otherwise ``None``.

    Raw sockets are used here so the launcher doesn't pull in ``httpx`` /
    ``requests``, keeping it lightweight.
    """
    sock: socket.socket | None = None
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

        raw = b"".join(chunks).decode("utf-8", errors="replace")
        # 分离响应头与响应体
        if "\r\n\r\n" not in raw:
            return None
        _, body = raw.split("\r\n\r\n", 1)

        # 处理 chunked 传输编码（常见单块场景）
        body = body.strip()
        if body and "\r\n" in body:
            _size_line, rest = body.split("\r\n", 1)
            # chunk-size 可能带扩展（分号后），取纯十六进制部分
            _size_hex = _size_line.split(";", 1)[0].strip()
            try:
                int(_size_hex, 16)
                # 确认是 chunked 分块格式，去掉末尾 "0" 结束块
                body = rest.rsplit("\r\n0", 1)[0] if "\r\n0" in rest else rest
            except ValueError:
                pass  # 非 chunked，保持 body 不变

        payload = json.loads(body)
        if isinstance(payload, dict) and payload.get("app") == HEALTH_APP_SIGNATURE:
            return payload
    except Exception:
        pass
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    return None


# ---------------------------------------------------------------------------
#  Hyper-V 保留端口范围检测（仅 Windows）
# ---------------------------------------------------------------------------

def get_hyperv_excluded_ranges() -> list[tuple[int, int]]:
    """Return the list of Hyper-V / WSL reserved port ranges (start, end).

    Returns an empty list on non-Windows or query failure.
    """
    if sys.platform != "win32":
        return []
    try:
        import shutil
        import subprocess

        # 优先通过 PATH 查找 netsh，找不到则回退到 System32 绝对路径
        resolved_netsh = shutil.which("netsh")
        if resolved_netsh is None:
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            resolved_netsh = os.path.join(system_root, "System32", "netsh.exe")

        result = subprocess.run(
            [resolved_netsh, "interface", "ipv4", "show", "excludedportrange", "protocol=tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    """Check whether the port falls inside a Hyper-V reserved range."""
    if excluded is None:
        excluded = get_hyperv_excluded_ranges()
    return any(lo <= port <= hi for lo, hi in excluded)


# ---------------------------------------------------------------------------
#  启动锁
# ---------------------------------------------------------------------------

def acquire_startup_lock() -> bool:
    """Back-compat façade over :mod:`utils.single_instance`.

    The old implementation lived here as a Windows named mutex plus a POSIX
    ``flock``, and it disagreed with itself in three ways worth recording, since
    they are the reason it could not be the basis of a uniqueness *proof*:

    * Inverted failure polarity. A Windows mutex failure returned "go ahead"
      while any POSIX ``OSError`` returned "somebody else is running" — and the
      mutex lived in the ``Global\\`` namespace, which a non-elevated interactive
      user usually cannot create, so the most common desktop configuration had
      no lock at all.
    * Inconsistent scope: machine-wide on Windows, per-user ``$TMPDIR`` on
      macOS, shared ``/tmp`` on Linux (where the second user could not even open
      the first user's lock file).
    * It unlinked the lock file on release, which hands a third contender a
      fresh inode while a second one is still waiting on the old one.

    Uniqueness now lives in one place, and it publishes the winner's identity
    instead of only saying "taken". This wrapper stays because ``launcher.py``
    re-exports it and existing tests patch it by name.
    """
    from utils import single_instance

    try:
        handle = single_instance.acquire_single_instance(
            instance_id=os.environ.get("NEKO_INSTANCE_ID", ""),
        )
    except OSError:
        # Not being able to consult the lock is "unknown", and unknown must not
        # become "somebody else is running" — that would be an unclearable
        # refusal to start on a full disk or a read-only home.
        return True
    return handle is not None


def release_startup_lock() -> None:
    """Release the single-instance lock (best effort, idempotent)."""
    from utils import single_instance

    single_instance.release_single_instance()
