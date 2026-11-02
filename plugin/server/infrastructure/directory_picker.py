"""Native directory selection for local, loopback-only application features.

Only platform-native bridges are used (osascript / PowerShell / zenity, kdialog
and yad). Tk/Tcl is excluded by project policy; see scripts/check_no_tkinter.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from utils.storage_policy import normalize_runtime_root

_PICKER_TIMEOUT_SECONDS = 120
_PICK_TITLE = "请选择目录"
_MANUAL_HINT = "当前环境暂不支持系统目录选择，请手动输入路径。"


class DirectoryPickerCancelled(Exception):
    pass


class DirectoryPickerUnavailable(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = (
            str(error_code or "directory_picker_unavailable").strip()
            or "directory_picker_unavailable"
        )
        self.message = str(message or _MANUAL_HINT).strip() or _MANUAL_HINT


def normalize_start_path(raw_value: str) -> str:
    """Return the nearest existing directory at or above ``raw_value``."""
    candidate_text = str(raw_value or "").strip()
    if not candidate_text:
        return ""

    try:
        candidate = normalize_runtime_root(candidate_text)
    except Exception:
        candidate = Path(candidate_text).expanduser()
        if not candidate.is_absolute():
            return ""

    if candidate.is_dir():
        return str(candidate)
    for parent in candidate.parents:
        if parent.is_dir():
            return str(parent)
    return ""


def _find_executable(*candidates: str) -> str | None:
    for candidate in candidates:
        if os.path.isabs(candidate):
            if os.path.exists(candidate):
                return candidate
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _run_picker(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=_PICKER_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise DirectoryPickerUnavailable(
            "directory_picker_unavailable", _MANUAL_HINT
        ) from exc
    except Exception as exc:
        raise DirectoryPickerUnavailable(
            "directory_picker_failed", f"打开系统目录选择器失败: {exc}"
        ) from exc


def _selected_or_cancelled(completed: subprocess.CompletedProcess[str]) -> str:
    selected = str(completed.stdout or "").strip()
    if not selected:
        raise DirectoryPickerCancelled()
    return selected


def _pick_via_osascript(*, start_path: str) -> str:
    command = [_find_executable("/usr/bin/osascript", "osascript") or "osascript"]
    command.extend(["-e", 'tell application "Finder" to activate'])
    if start_path:
        safe_start_path = start_path.replace("\\", "\\\\").replace('"', '\\"')
        command.extend(
            [
                "-e",
                f'set defaultLocation to POSIX file "{safe_start_path}"',
                "-e",
                f'set selectedFolder to choose folder with prompt "{_PICK_TITLE}" '
                "default location defaultLocation",
            ]
        )
    else:
        command.extend(
            [
                "-e",
                f'set selectedFolder to choose folder with prompt "{_PICK_TITLE}"',
            ]
        )
    command.extend(["-e", "POSIX path of selectedFolder"])

    completed = _run_picker(command)
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        if "User canceled" in stderr or "(-128)" in stderr:
            raise DirectoryPickerCancelled()
        raise DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )
    return _selected_or_cancelled(completed)


_POWERSHELL_SCRIPT = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'N.E.K.O'
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.TopMost = $true
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '{title}'
$dialog.ShowNewFolderButton = $true
if ('{start_path}') {{
    $dialog.SelectedPath = '{start_path}'
}}
$owner.Show()
$owner.Activate()
$owner.BringToFront()
[System.Windows.Forms.Application]::DoEvents()
$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {{
    Write-Output $dialog.SelectedPath
    exit 0
}}
exit 2
""".strip()


def _pick_via_powershell(*, start_path: str) -> str:
    executable = _find_executable(
        os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        ),
        "powershell.exe",
        "pwsh.exe",
    )
    if not executable:
        raise DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未找到 PowerShell，无法打开目录选择器。",
        )

    script = _POWERSHELL_SCRIPT.format(
        title=_PICK_TITLE, start_path=start_path.replace("'", "''")
    )
    completed = _run_picker([executable, "-NoProfile", "-STA", "-Command", script])
    if completed.returncode == 2:
        raise DirectoryPickerCancelled()
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        raise DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )
    return _selected_or_cancelled(completed)


def _pick_via_linux_dialog(*, start_path: str) -> str:
    commands: list[list[str]] = []
    zenity = _find_executable("/usr/bin/zenity", "/bin/zenity", "zenity")
    if zenity:
        command = [zenity, "--file-selection", "--directory", f"--title={_PICK_TITLE}"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)
    kdialog = _find_executable("/usr/bin/kdialog", "/bin/kdialog", "kdialog")
    if kdialog:
        command = [kdialog, "--getexistingdirectory"]
        if start_path:
            command.append(start_path)
        commands.append(command)
    yad = _find_executable("/usr/bin/yad", "/bin/yad", "yad")
    if yad:
        command = [yad, "--file-selection", "--directory", f"--title={_PICK_TITLE}"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)

    if not commands:
        raise DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未安装可用的图形目录选择器。",
        )

    last_error: object = None
    for command in commands:
        try:
            completed = _run_picker(command)
        except DirectoryPickerUnavailable as exc:
            last_error = exc.message
            continue
        if completed.returncode == 0:
            return _selected_or_cancelled(completed)
        if completed.returncode in (1, 252):
            raise DirectoryPickerCancelled()
        last_error = str(completed.stderr or "").strip() or completed.returncode

    raise DirectoryPickerUnavailable(
        "directory_picker_failed", f"打开系统目录选择器失败: {last_error}"
    )


def pick_directory(*, start_path: str = "") -> str:
    """Show the native folder dialog and return the selected absolute path.

    Raises ``DirectoryPickerCancelled`` when the user dismisses the dialog and
    ``DirectoryPickerUnavailable`` when no native dialog can be shown.
    """
    normalized = normalize_start_path(start_path)
    if sys.platform == "darwin":
        return _pick_via_osascript(start_path=normalized)
    if sys.platform == "win32":
        return _pick_via_powershell(start_path=normalized)
    return _pick_via_linux_dialog(start_path=normalized)
