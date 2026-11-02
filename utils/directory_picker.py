"""Native directory selection shared by local application features."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from utils.storage_policy import normalize_runtime_root


class _DirectoryPickerCancelled(Exception):
    pass


class _DirectoryPickerUnavailable(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = (
            str(error_code or "directory_picker_unavailable").strip()
            or "directory_picker_unavailable"
        )
        self.message = (
            str(message or "当前环境暂不支持系统目录选择，请手动输入路径。").strip()
            or "当前环境暂不支持系统目录选择，请手动输入路径。"
        )


def _normalize_directory_picker_start_path(raw_value: str) -> str:
    candidate_text = str(raw_value or "").strip()
    if not candidate_text:
        return ""

    try:
        candidate = normalize_runtime_root(candidate_text)
    except Exception:
        candidate = Path(candidate_text).expanduser()
        if not candidate.is_absolute():
            return ""

    if candidate.exists() and candidate.is_dir():
        return str(candidate)

    current = candidate.parent
    while current != current.parent:
        if current.exists() and current.is_dir():
            return str(current)
        current = current.parent

    if current.exists() and current.is_dir():
        return str(current)
    return ""


def _resolve_executable_name(*candidates: str) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        if os.path.isabs(candidate) and os.path.exists(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return candidates[0]


def _pick_directory_via_osascript(*, start_path: str) -> str:
    command = [_resolve_executable_name("/usr/bin/osascript", "osascript")]
    if start_path:
        safe_start_path = start_path.replace("\\", "\\\\").replace('"', '\\"')
        command.extend(
            [
                "-e",
                'tell application "Finder" to activate',
                "-e",
                f'set defaultLocation to POSIX file "{safe_start_path}"',
                "-e",
                'set selectedFolder to choose folder with prompt "请选择目录" default location defaultLocation',
            ]
        )
    else:
        command.extend(
            [
                "-e",
                'tell application "Finder" to activate',
                "-e",
                'set selectedFolder to choose folder with prompt "请选择目录"',
            ]
        )
    command.extend(["-e", "POSIX path of selectedFolder"])

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前环境暂不支持系统目录选择，请手动输入路径。",
        ) from exc
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            f"打开系统目录选择器失败: {exc}",
        ) from exc

    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        if "User canceled" in stderr or "(-128)" in stderr:
            raise _DirectoryPickerCancelled()
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )

    selected_root = str(completed.stdout or "").strip()
    if not selected_root:
        raise _DirectoryPickerCancelled()
    return selected_root


def _pick_directory_via_powershell(*, start_path: str) -> str:
    powershell_executable = _resolve_executable_name(
        os.path.join(
            os.environ.get("SystemRoot", r"C:\Windows"),
            "System32",
            "WindowsPowerShell",
            "v1.0",
            "powershell.exe",
        ),
        "powershell.exe",
        "powershell",
        "pwsh.exe",
        "pwsh",
    )
    if not os.path.isabs(powershell_executable) and not shutil.which(
        powershell_executable
    ):
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未找到 PowerShell，无法打开目录选择器。",
        )

    escaped_start_path = start_path.replace("'", "''")
    script = """
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
$dialog.Description = '请选择目录'
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
""".strip().format(start_path=escaped_start_path)

    try:
        completed = subprocess.run(
            [powershell_executable, "-NoProfile", "-STA", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未找到 PowerShell，无法打开目录选择器。",
        ) from exc
    except Exception as exc:
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {exc}",
        ) from exc

    if completed.returncode == 2:
        raise _DirectoryPickerCancelled()
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        raise _DirectoryPickerUnavailable(
            "directory_picker_failed",
            f"打开系统目录选择器失败: {stderr or completed.returncode}",
        )

    selected_root = str(completed.stdout or "").strip()
    if not selected_root:
        raise _DirectoryPickerCancelled()
    return selected_root


def _pick_directory_via_linux_dialog(*, start_path: str) -> str:
    commands: list[list[str]] = []
    zenity_executable = _resolve_executable_name(
        "/usr/bin/zenity", "/bin/zenity", "zenity"
    )
    if (
        os.path.isabs(zenity_executable)
        and os.path.exists(zenity_executable)
        or shutil.which(zenity_executable)
    ):
        command = [
            zenity_executable,
            "--file-selection",
            "--directory",
            "--title=请选择目录",
        ]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)
    kdialog_executable = _resolve_executable_name(
        "/usr/bin/kdialog", "/bin/kdialog", "kdialog"
    )
    if (
        os.path.isabs(kdialog_executable)
        and os.path.exists(kdialog_executable)
        or shutil.which(kdialog_executable)
    ):
        command = [kdialog_executable, "--getexistingdirectory"]
        if start_path:
            command.append(start_path)
        commands.append(command)
    yad_executable = _resolve_executable_name("/usr/bin/yad", "/bin/yad", "yad")
    if (
        os.path.isabs(yad_executable)
        and os.path.exists(yad_executable)
        or shutil.which(yad_executable)
    ):
        command = [
            yad_executable,
            "--file-selection",
            "--directory",
            "--title=请选择目录",
        ]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)

    if not commands:
        raise _DirectoryPickerUnavailable(
            "directory_picker_unavailable",
            "当前系统未安装可用的图形目录选择器。",
        )

    last_error = None
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except Exception as exc:
            last_error = exc
            continue

        if completed.returncode == 0:
            selected_root = str(completed.stdout or "").strip()
            if selected_root:
                return selected_root
            raise _DirectoryPickerCancelled()
        if completed.returncode in (1, 252):
            raise _DirectoryPickerCancelled()
        last_error = str(completed.stderr or "").strip() or completed.returncode

    raise _DirectoryPickerUnavailable(
        "directory_picker_failed",
        f"打开系统目录选择器失败: {last_error}",
    )


def pick_directory(*, start_path: str) -> str:
    # Project policy excludes Tk/Tcl. Trust only platform-native bridges (osascript / PowerShell /
    # zenity-kdialog-yad); failure raises _DirectoryPickerUnavailable so the frontend requests manual paths,
    # never tkinter fallback, whose tk.Tk() raises SystemExit and kills the backend when Nuitka lacks tk-inter.
    # scripts/check_no_tkinter.py enforces this.
    normalized_start_path = _normalize_directory_picker_start_path(start_path)
    if sys.platform == "darwin":
        return _pick_directory_via_osascript(start_path=normalized_start_path)
    if sys.platform == "win32":
        return _pick_directory_via_powershell(start_path=normalized_start_path)
    return _pick_directory_via_linux_dialog(start_path=normalized_start_path)


class DirectoryOpenError(RuntimeError):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = (
            str(error_code or "open_storage_root_unavailable").strip()
            or "open_storage_root_unavailable"
        )
        self.message = (
            str(message or "当前环境暂不支持直接打开目录。").strip()
            or "当前环境暂不支持直接打开目录。"
        )


def open_directory(path: Path | str) -> None:
    target_path = normalize_runtime_root(path)
    if not target_path.exists() or not target_path.is_dir():
        raise DirectoryOpenError(
            "storage_root_unavailable",
            "当前数据目录不存在或不可访问。",
        )

    try:
        if sys.platform == "win32":
            os.startfile(str(target_path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target_path)])
            return

        opener = shutil.which("xdg-open") or shutil.which("gio")
        if not opener:
            raise DirectoryOpenError(
                "open_storage_root_unavailable",
                "当前系统未找到可用的文件管理器打开命令。",
            )
        if os.path.basename(opener) == "gio":
            subprocess.Popen([opener, "open", str(target_path)])
        else:
            subprocess.Popen([opener, str(target_path)])
    except DirectoryOpenError:
        raise
    except Exception as exc:
        raise DirectoryOpenError(
            "open_storage_root_failed",
            f"打开当前数据目录失败: {exc}",
        ) from exc
