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

"""Provide filesystem and process helpers used by the server entry point."""

import os
import sys

from utils.port_utils import set_port_probe_reuse


def _format_size(size_bytes):
    """
    Format a byte size into a human-readable string
    """
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


# 辅助函数
def get_folder_size(folder_path):
    """Get folder size (in bytes)"""
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                total_size += os.path.getsize(filepath)
            except (OSError, FileNotFoundError):
                continue
    return total_size


def find_preview_image_in_folder(folder_path):
    """Look for a preview image in the folder, checking only the 8 designated image names"""
    # 按优先级顺序查找指定的图片文件列表
    preview_image_names = [
        "preview.jpg",
        "preview.png",
        "thumbnail.jpg",
        "thumbnail.png",
        "icon.jpg",
        "icon.png",
        "header.jpg",
        "header.png",
    ]

    for image_name in preview_image_names:
        image_path = os.path.join(folder_path, image_name)
        if os.path.exists(image_path) and os.path.isfile(image_path):
            return image_path

    # 如果找不到指定的图片名称，返回None
    return None


def _get_port_owners(port: int) -> list[int]:
    """Query the PIDs of processes listening on the given port (best effort)."""
    pids: set[int] = set()
    try:
        import subprocess

        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
            needle = f":{port}"
            for raw in result.stdout.splitlines():
                line = raw.strip()
                if "LISTENING" not in line or needle not in line:
                    continue
                parts = line.split()
                if not parts:
                    continue
                pid_str = parts[-1]
                if pid_str.isdigit():
                    pids.add(int(pid_str))
        else:
            result = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
            for line in result.stdout.splitlines():
                s = line.strip()
                if s.isdigit():
                    pids.add(int(s))
    except Exception:
        pass
    return sorted(pids)


def _is_port_available(port: int) -> bool:
    """Check whether 127.0.0.1:port can be bound."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        set_port_probe_reuse(sock)
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()
