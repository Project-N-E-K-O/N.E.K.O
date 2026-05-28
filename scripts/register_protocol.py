"""注册 neko:// URI Scheme 协议处理器。

各平台注册方式：
- Windows: 写入注册表 HKCU\\Software\\Classes\\neko
- macOS: 生成 .app bundle 的 Info.plist（需要 .app 结构）
- Linux: 创建 .desktop 文件并注册 MIME type

使用：
  python scripts/register_protocol.py          # 注册
  python scripts/register_protocol.py --remove # 移除
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
    """注册 neko:// 协议。"""
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
    """移除 neko:// 协议注册。"""
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
    """Windows: 写入 HKCU\\Software\\Classes\\neko"""
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
        cmd = (
            f"cmd.exe /d /c cd /d {_windows_cmd_quote(str(PROJECT_ROOT))} "
            f"&& set PYTHONPATH={_windows_cmd_quote(str(PROJECT_ROOT))};%PYTHONPATH% "
            f"&& {_windows_cmd_quote(PYTHON_EXE)} -m {HANDLER_MODULE} \"%1\""
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
    """Windows: 删除注册表键。"""
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
    """macOS: 创建 helper .app 并注册 URL scheme。"""
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
    """macOS: 删除 helper app。"""
    import shutil
    app_dir = Path.home() / "Applications" / "NekoProtocolHandler.app"
    if app_dir.exists():
        shutil.rmtree(app_dir)
    print("[OK] Removed neko:// protocol (macOS)")
    return True


# ─── Linux ─────────────────────────────────────────────────────────

def _register_linux() -> bool:
    """Linux: 创建 .desktop 文件并注册 MIME type。"""
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
    """Linux: 删除 .desktop 文件。"""
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
