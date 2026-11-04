"""Native directory selection and opening through platform bridges.

Only platform-native bridges are used (osascript / PowerShell / zenity, kdialog
and yad). Tk/Tcl is excluded by project policy; see scripts/check_no_tkinter.py.
Error messages are English diagnostics; callers localize by ``code``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from utils.storage_policy import normalize_runtime_root

_PICKER_TIMEOUT_SECONDS = 120
_DEFAULT_TITLE = "Choose a folder"
NATIVE_DIRECTORY_ERROR_CODES = frozenset(
    {
        "DIRECTORY_PICKER_UNAVAILABLE",
        "DIRECTORY_PICKER_FAILED",
        "DIRECTORY_UNAVAILABLE",
        "OPEN_DIRECTORY_UNAVAILABLE",
        "OPEN_DIRECTORY_FAILED",
    }
)


class NativeDirectoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        if code not in NATIVE_DIRECTORY_ERROR_CODES:
            raise ValueError(f"Undeclared native directory error code: {code}")
        super().__init__(message)
        self.code = code


class DirectoryPickerCancelled(Exception):
    pass


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


def _picker_failed(detail: object) -> NativeDirectoryError:
    return NativeDirectoryError(
        "DIRECTORY_PICKER_FAILED", f"Native folder picker failed: {detail}"
    )


def _picker_unavailable(message: str) -> NativeDirectoryError:
    return NativeDirectoryError("DIRECTORY_PICKER_UNAVAILABLE", message)


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
        raise _picker_unavailable("Native folder picker is not installed") from exc
    except Exception as exc:
        raise _picker_failed(exc) from exc


def _selected_or_cancelled(completed: subprocess.CompletedProcess[str]) -> str:
    selected = str(completed.stdout or "").strip()
    if not selected:
        raise DirectoryPickerCancelled()
    return selected


def _applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _pick_via_osascript(*, start_path: str, title: str) -> str:
    command = [_find_executable("/usr/bin/osascript", "osascript") or "osascript"]
    command.extend(["-e", 'tell application "Finder" to activate'])
    prompt = f'choose folder with prompt "{_applescript_string(title)}"'
    if start_path:
        command.extend(
            [
                "-e",
                f'set defaultLocation to POSIX file "{_applescript_string(start_path)}"',
                "-e",
                f"set selectedFolder to {prompt} default location defaultLocation",
            ]
        )
    else:
        command.extend(["-e", f"set selectedFolder to {prompt}"])
    command.extend(["-e", "POSIX path of selectedFolder"])

    completed = _run_picker(command)
    if completed.returncode != 0:
        stderr = str(completed.stderr or "").strip()
        if "User canceled" in stderr or "(-128)" in stderr:
            raise DirectoryPickerCancelled()
        raise _picker_failed(stderr or completed.returncode)
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


def _powershell_string(value: str) -> str:
    return value.replace("'", "''")


def _pick_via_powershell(*, start_path: str, title: str) -> str:
    executable = _find_executable(
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
    if not executable:
        raise _picker_unavailable("PowerShell is required for the native folder picker")

    script = _POWERSHELL_SCRIPT.format(
        title=_powershell_string(title), start_path=_powershell_string(start_path)
    )
    completed = _run_picker([executable, "-NoProfile", "-STA", "-Command", script])
    if completed.returncode == 2:
        raise DirectoryPickerCancelled()
    if completed.returncode != 0:
        raise _picker_failed(str(completed.stderr or "").strip() or completed.returncode)
    return _selected_or_cancelled(completed)


def _pick_via_linux_dialog(*, start_path: str, title: str) -> str:
    commands: list[list[str]] = []
    zenity = _find_executable("/usr/bin/zenity", "/bin/zenity", "zenity")
    if zenity:
        command = [zenity, "--file-selection", "--directory", f"--title={title}"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)
    kdialog = _find_executable("/usr/bin/kdialog", "/bin/kdialog", "kdialog")
    if kdialog:
        command = [kdialog, "--title", title, "--getexistingdirectory"]
        if start_path:
            command.append(start_path)
        commands.append(command)
    yad = _find_executable("/usr/bin/yad", "/bin/yad", "yad")
    if yad:
        command = [yad, "--file-selection", "--directory", f"--title={title}"]
        if start_path:
            command.append(f"--filename={start_path.rstrip('/')}/")
        commands.append(command)

    if not commands:
        raise _picker_unavailable("No graphical folder picker is installed")

    last_error: object = None
    for command in commands:
        try:
            completed = _run_picker(command)
        except NativeDirectoryError as exc:
            last_error = exc
            continue
        if completed.returncode == 0:
            return _selected_or_cancelled(completed)
        if completed.returncode in (1, 252):
            raise DirectoryPickerCancelled()
        last_error = str(completed.stderr or "").strip() or completed.returncode

    raise _picker_failed(last_error)


def pick_directory(*, start_path: str = "", title: str = "") -> str:
    """Show the native folder dialog and return the selected absolute path.

    Raises ``DirectoryPickerCancelled`` when the user dismisses the dialog and
    ``NativeDirectoryError`` when no native dialog can be shown.
    """
    normalized = normalize_start_path(start_path)
    dialog_title = str(title or "").strip() or _DEFAULT_TITLE
    if sys.platform == "darwin":
        return _pick_via_osascript(start_path=normalized, title=dialog_title)
    if sys.platform == "win32":
        return _pick_via_powershell(start_path=normalized, title=dialog_title)
    return _pick_via_linux_dialog(start_path=normalized, title=dialog_title)


def open_directory(path: Path | str) -> None:
    """Open an existing absolute directory in the platform file manager."""
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raise NativeDirectoryError(
            "DIRECTORY_UNAVAILABLE", "Use an absolute directory path"
        )
    target_path = normalize_runtime_root(raw)
    if not target_path.is_dir():
        raise NativeDirectoryError(
            "DIRECTORY_UNAVAILABLE", "Directory does not exist or is not accessible"
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
            raise NativeDirectoryError(
                "OPEN_DIRECTORY_UNAVAILABLE", "No file manager opener is installed"
            )
        if os.path.basename(opener) == "gio":
            subprocess.Popen([opener, "open", str(target_path)])
        else:
            subprocess.Popen([opener, str(target_path)])
    except NativeDirectoryError:
        raise
    except Exception as exc:
        raise NativeDirectoryError(
            "OPEN_DIRECTORY_FAILED", f"Could not open directory: {exc}"
        ) from exc
