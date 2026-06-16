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

"""Register the neko:// URI scheme protocol handler.

Per-platform registration:
- Windows: write to the registry HKCU\\Software\\Classes\\neko
- macOS: generate a .app bundle Info.plist (requires the .app structure)
- Linux: create a .desktop file and register the MIME type

Usage:
  python scripts/register_protocol.py          # register
  python scripts/register_protocol.py --remove # remove
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

# N.E.K.O 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Python 解释器路径
PYTHON_EXE = sys.executable
# 协议处理器模块
HANDLER_MODULE = "plugin.server.market_protocol_handler"
# 协议处理命令
HANDLER_CMD = f'"{PYTHON_EXE}" -m {HANDLER_MODULE} "%1"'


def _desktop_exec_quote(value: str) -> str:
    """Quote an argv field for freedesktop Exec= lines.

    Desktop entry Exec= values are not shell commands; quoted arguments use
    double quotes and backslash escapes.
    """

    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def _desktop_entry_value_escape(value: str) -> str:
    """Escape a desktop-entry string value without Exec-style quoting."""

    return (
        value.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def _windows_cmd_quote(value: str) -> str:
    """Quote a value for a Windows cmd.exe command line."""

    escaped = str(value).replace('"', r'\"')
    return f'"{escaped}"'


def _posix_shell_quote(value: str) -> str:
    """Quote a value for the small macOS shell launcher."""

    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def register() -> bool:
    """Register the neko:// protocol."""
    system = platform.system()
    if system == "Windows":
        return _register_windows()
    elif system == "Darwin":
        return _register_macos()
    elif system == "Linux":
        return _register_linux()
    else:
        print(f"Unsupported platform: {system}")
        return False


def remove() -> bool:
    """Remove the neko:// protocol registration."""
    system = platform.system()
    if system == "Windows":
        return _remove_windows()
    elif system == "Darwin":
        return _remove_macos()
    elif system == "Linux":
        return _remove_linux()
    else:
        print(f"Unsupported platform: {system}")
        return False


# ─── Windows ───────────────────────────────────────────────────────

def _register_windows() -> bool:
    """Windows: write HKCU\\Software\\Classes\\neko"""
    try:
        import winreg

        # 创建 neko 键
        key_path = r"Software\Classes\neko"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "N.E.K.O Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        # 创建 DefaultIcon
        icon_path = str(PROJECT_ROOT / "static" / "favicon.ico")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon_path)

        # 创建 shell\open\command
        #
        # ``set "PYTHONPATH=...;%PYTHONPATH%"`` quotes the assignment so spaces
        # in the repo path are tolerated, and cmd.exe strips those outer
        # quotes when storing the variable. The old ``set PYTHONPATH="...";%PYTHONPATH%``
        # form persisted the literal quote characters as the first entry of
        # PYTHONPATH, which broke ``python -m plugin.server.market_protocol_handler``
        # on installs whose checkout sat under a path with spaces.
        project_root_value = str(PROJECT_ROOT)
        cmd = (
            f"cmd.exe /d /c cd /d {_windows_cmd_quote(project_root_value)} "
            f'&& set "PYTHONPATH={project_root_value};%PYTHONPATH%" '
            f'&& {_windows_cmd_quote(PYTHON_EXE)} -m {HANDLER_MODULE} "%1"'
        )
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\shell\\open\\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, cmd)

        print(f"[OK] Registered neko:// protocol (Windows)")
        print(f"     Handler: {cmd}")
        return True

    except Exception as exc:
        print(f"[FAIL] Windows registration failed: {exc}")
        return False


def _remove_windows() -> bool:
    """Windows: delete the registry key."""
    try:
        import winreg

        def _delete_key_recursive(root, path):
            try:
                with winreg.OpenKey(root, path, 0, winreg.KEY_ALL_ACCESS) as key:
                    while True:
                        try:
                            subkey = winreg.EnumKey(key, 0)
                            _delete_key_recursive(root, f"{path}\\{subkey}")
                        except OSError:
                            break
                winreg.DeleteKey(root, path)
            except FileNotFoundError:
                pass

        _delete_key_recursive(winreg.HKEY_CURRENT_USER, r"Software\Classes\neko")
        print("[OK] Removed neko:// protocol (Windows)")
        return True

    except Exception as exc:
        print(f"[FAIL] Windows removal failed: {exc}")
        return False


# ─── macOS ─────────────────────────────────────────────────────────

def _register_macos() -> bool:
    """macOS: create the helper .app and register the URL scheme."""
    app_dir = Path.home() / "Applications" / "NekoProtocolHandler.app"
    contents = app_dir / "Contents"
    macos_dir = contents / "MacOS"

    macos_dir.mkdir(parents=True, exist_ok=True)

    # Info.plist
    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>com.neko.protocol-handler</string>
    <key>CFBundleName</key>
    <string>N.E.K.O Protocol Handler</string>
    <key>CFBundleExecutable</key>
    <string>handler</string>
    <key>CFBundleURLTypes</key>
    <array>
        <dict>
            <key>CFBundleURLName</key>
            <string>N.E.K.O Protocol</string>
            <key>CFBundleURLSchemes</key>
            <array>
                <string>neko</string>
            </array>
        </dict>
    </array>
    <key>LSBackgroundOnly</key>
    <true/>
</dict>
</plist>
"""
    (contents / "Info.plist").write_text(plist_content)

    # 可执行脚本
    quoted_project_root = _posix_shell_quote(str(PROJECT_ROOT))
    quoted_python_exe = _posix_shell_quote(PYTHON_EXE)
    handler_script = f"""#!/bin/bash
cd {quoted_project_root} || exit 1
export PYTHONPATH={quoted_project_root}${{PYTHONPATH:+:$PYTHONPATH}}
exec {quoted_python_exe} -m {HANDLER_MODULE} "$@"
"""
    handler_path = macos_dir / "handler"
    handler_path.write_text(handler_script)
    handler_path.chmod(0o755)

    # 注册 URL scheme
    os.system("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
              "LaunchServices.framework/Support/lsregister "
              f"-R '{app_dir}'")

    print(f"[OK] Registered neko:// protocol (macOS)")
    print(f"     App: {app_dir}")
    return True


def _remove_macos() -> bool:
    """macOS: delete the helper app."""
    import shutil
    app_dir = Path.home() / "Applications" / "NekoProtocolHandler.app"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    print("[OK] Removed neko:// protocol (macOS)")
    return True


# ─── Linux ─────────────────────────────────────────────────────────

def _register_linux() -> bool:
    """Linux: create the .desktop file and register the MIME type."""
    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = desktop_dir / "neko-protocol-handler.desktop"
    python_exe = _desktop_exec_quote(PYTHON_EXE)
    project_root = _desktop_entry_value_escape(str(PROJECT_ROOT))
    desktop_content = f"""[Desktop Entry]
Name=N.E.K.O Protocol Handler
Exec={python_exe} -m {HANDLER_MODULE} %u
Path={project_root}
Type=Application
NoDisplay=true
MimeType=x-scheme-handler/neko;
"""
    desktop_file.write_text(desktop_content)

    # 注册 MIME type
    os.system(f"xdg-mime default neko-protocol-handler.desktop x-scheme-handler/neko")
    # 更新 desktop database
    os.system(f"update-desktop-database {desktop_dir} 2>/dev/null || true")

    print(f"[OK] Registered neko:// protocol (Linux)")
    print(f"     Desktop file: {desktop_file}")
    return True


def _remove_linux() -> bool:
    """Linux: delete the .desktop file."""
    desktop_file = Path.home() / ".local" / "share" / "applications" / "neko-protocol-handler.desktop"
    if desktop_file.exists():
        desktop_file.unlink()
        os.system(f"update-desktop-database {desktop_file.parent} 2>/dev/null || true")
    print("[OK] Removed neko:// protocol (Linux)")
    return True


# ─── 入口 ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--remove" in sys.argv or "--unregister" in sys.argv:
        success = remove()
    else:
        success = register()

    sys.exit(0 if success else 1)
