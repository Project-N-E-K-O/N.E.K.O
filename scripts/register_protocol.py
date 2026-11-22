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
import shutil
import subprocess
import sys
from pathlib import Path

# N.E.K.O project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Python interpreter path.
PYTHON_EXE = sys.executable
# Protocol-handler module.
HANDLER_MODULE = "plugin.server.market_protocol_handler"
# Protocol-handler command.
HANDLER_CMD = f'"{PYTHON_EXE}" -m {HANDLER_MODULE} "%1"'
# Linux handler desktop entry and the MIME type it claims.
LINUX_DESKTOP_FILE_NAME = "neko-protocol-handler.desktop"
LINUX_SCHEME_MIME_TYPE = "x-scheme-handler/neko"
# macOS Launch Services registration tool.
LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)


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


def _run_required(args: list[str], what: str) -> bool:
    """Run a registration command whose failure means registration failed."""
    try:
        result = subprocess.run(args, check=False)
    except OSError as exc:
        print(f"[FAIL] {what} could not be started ({args[0]}): {exc}")
        return False
    if result.returncode != 0:
        print(f"[FAIL] {what} exited with status {result.returncode}: {args[0]}")
        return False
    return True


def _run_optional(args: list[str]) -> None:
    """Run a best-effort refresh command; missing tools and failures are ignored."""
    try:
        subprocess.run(
            args,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        # The refresh is optional: a missing or broken tool only delays the
        # desktop's cache update and must not fail the registration.
        pass


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

        # Create the neko key.
        key_path = r"Software\Classes\neko"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "N.E.K.O Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

        # Create DefaultIcon.
        icon_path = str(PROJECT_ROOT / "static" / "favicon.ico")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, f"{key_path}\\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon_path)

        # Create shell\open\command.
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

    # Executable script.
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

    # Register the URL scheme.
    if not _run_required([LSREGISTER, "-R", str(app_dir)], "lsregister"):
        print("[FAIL] neko:// protocol was not registered (macOS)")
        print(f"     App bundle left at: {app_dir}")
        return False

    print(f"[OK] Registered neko:// protocol (macOS)")
    print(f"     App: {app_dir}")
    return True


def _remove_macos() -> bool:
    """macOS: unregister the helper app from Launch Services and delete it."""
    app_dir = Path.home() / "Applications" / "NekoProtocolHandler.app"
    if app_dir.exists():
        # Mirror registration: lsregister -u must run while the bundle exists.
        # On failure keep the bundle, so a rerun can still unregister it.
        if not _run_required([LSREGISTER, "-u", str(app_dir)], "lsregister"):
            print("[FAIL] neko:// protocol was not removed (macOS)")
            print(f"     App bundle left at: {app_dir}")
            return False
        shutil.rmtree(app_dir)
    print("[OK] Removed neko:// protocol (macOS)")
    return True


# ─── Linux ─────────────────────────────────────────────────────────

def _register_linux() -> bool:
    """Linux: create the .desktop file and register the MIME type."""
    desktop_dir = Path.home() / ".local" / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = desktop_dir / LINUX_DESKTOP_FILE_NAME
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

    # Update the desktop database first (optional refresh) so the cache sees
    # the new .desktop file even if setting the default handler fails.
    _run_optional(["update-desktop-database", str(desktop_dir)])
    # Register the MIME type.
    if not _run_required(
        ["xdg-mime", "default", desktop_file.name, LINUX_SCHEME_MIME_TYPE],
        "xdg-mime",
    ):
        print("[FAIL] neko:// protocol was not registered (Linux)")
        print(f"     Desktop file left at: {desktop_file}")
        return False

    print(f"[OK] Registered neko:// protocol (Linux)")
    print(f"     Desktop file: {desktop_file}")
    return True


# mimeapps.list groups that xdg-mime / desktop environments use to associate
# the scheme with a handler.
_MIMEAPPS_GROUPS = ("[Default Applications]", "[Added Associations]")


def _linux_mimeapps_paths() -> list[Path]:
    """User mimeapps.list files that may hold the neko:// association."""
    config_home = os.environ.get("XDG_CONFIG_HOME", "")
    # The XDG spec ignores relative values.
    if not config_home or not os.path.isabs(config_home):
        config_home = str(Path.home() / ".config")
    config_dir = Path(config_home)
    return [
        config_dir / "mimeapps.list",
        # Desktop-specific overrides such as gnome-mimeapps.list.
        *sorted(config_dir.glob("*-mimeapps.list")),
        # Legacy location still written by older xdg-utils.
        Path.home() / ".local" / "share" / "applications" / "mimeapps.list",
    ]


def _drop_mimeapps_association(path: Path) -> None:
    """Remove our desktop file from the neko:// entries of a mimeapps.list.

    Other handlers listed for the scheme and every other line are kept as is.
    Raises OSError / UnicodeError when the file cannot be read or written.
    """
    if not path.is_file():
        return
    # Edit the real file so a symlinked mimeapps.list (dotfile managers)
    # stays a symlink.
    path = path.resolve()
    # Work on bytes split at "\n" only, so CRLF endings and any other
    # characters are written back exactly as read.
    lines = path.read_bytes().decode("utf-8").split("\n")
    group = None
    changed = False
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            group = stripped
        key, sep, value = line.partition("=")
        if (
            sep
            and group in _MIMEAPPS_GROUPS
            and key.strip() == LINUX_SCHEME_MIME_TYPE
        ):
            body = value.rstrip("\r")
            ending = value[len(body):]
            handlers = [h.strip() for h in body.split(";") if h.strip()]
            remaining = [h for h in handlers if h != LINUX_DESKTOP_FILE_NAME]
            if remaining != handlers:
                changed = True
                if not remaining:
                    # The removed line's "\n" goes with it.
                    continue
                trailer = ";" if body.rstrip().endswith(";") else ""
                line = f"{key}={';'.join(remaining)}{trailer}{ending}"
        kept.append(line)
    if not changed:
        return
    # Write a sibling file and swap it in so a failure never truncates the
    # user's associations.
    tmp = path.with_name(path.name + ".neko-tmp")
    try:
        tmp.write_bytes("\n".join(kept).encode("utf-8"))
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _remove_linux() -> bool:
    """Linux: drop the default-handler association and the .desktop file."""
    desktop_file = Path.home() / ".local" / "share" / "applications" / LINUX_DESKTOP_FILE_NAME
    ok = True
    for mimeapps in _linux_mimeapps_paths():
        try:
            _drop_mimeapps_association(mimeapps)
        except (OSError, UnicodeError) as exc:
            print(f"[FAIL] Could not remove the neko:// association from {mimeapps}: {exc}")
            ok = False
    if desktop_file.exists():
        desktop_file.unlink()
    if desktop_file.parent.is_dir():
        # Refresh even when the .desktop file was already gone, so a stale
        # mimeinfo.cache entry is dropped too.
        _run_optional(["update-desktop-database", str(desktop_file.parent)])
    if not ok:
        print("[FAIL] neko:// protocol was only partly removed (Linux)")
        return False
    print("[OK] Removed neko:// protocol (Linux)")
    return True


# -- Entry point --

if __name__ == "__main__":
    if "--remove" in sys.argv or "--unregister" in sys.argv:
        success = remove()
    else:
        success = register()

    sys.exit(0 if success else 1)
