from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from config import APP_NAME
from utils.file_utils import atomic_write_text


_WINDOWS_RUN_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_WINDOWS_RUN_VALUE_NAME = APP_NAME
_MAC_LAUNCH_AGENT_LABEL = "com.project-neko.autostart"
_MAC_LAUNCH_AGENT_FILENAME = "com.project-neko.autostart.plist"
_LINUX_AUTOSTART_FILENAME = "project-neko-autostart.desktop"
_DEFAULT_POSIX_PATH = "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
_DESKTOP_ENTRY_EXEC_FIELD_CODES = frozenset("fFuUdDnNickvm")
_DESKTOP_ENTRY_EXEC_RESERVED_CHARS = frozenset((
    " ",
    "\t",
    "\n",
    '"',
    "'",
    "\\",
    ">",
    "<",
    "~",
    "|",
    "&",
    ";",
    "$",
    "*",
    "?",
    "#",
    "(",
    ")",
    "`",
))
_PASSTHROUGH_ENV_KEYS = (
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "TMPDIR",
    "UV_CACHE_DIR",
)


def _get_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _get_project_pyproject_path() -> Path:
    return _get_project_root() / "pyproject.toml"


def _get_launcher_script_path() -> Path:
    return _get_project_root() / "launcher.py"


def _get_current_executable_path() -> Path | None:
    executable = Path(sys.executable)
    if executable.exists():
        return executable
    return None


def _get_project_virtualenv_python() -> Path | None:
    project_root = _get_project_root()
    candidates: tuple[Path, ...]
    if sys.platform.startswith("win"):
        candidates = (project_root / ".venv" / "Scripts" / "python.exe",)
    else:
        candidates = (
            project_root / ".venv" / "bin" / "python",
            project_root / ".venv" / "bin" / "python3",
        )

    for candidate in candidates:
        if candidate.exists():
            # Keep the virtualenv entrypoint path instead of resolving symlinks.
            # uv-managed venvs often point `.venv/bin/python*` at a shared base
            # interpreter outside the venv. If we resolve here, launchd ends up
            # invoking the base interpreter directly, which loses the venv
            # context and breaks dependency imports during autostart.
            return candidate
    return None


def _get_uv_executable_path() -> Path | None:
    candidates: list[Path] = []

    current_executable = _get_current_executable_path()
    if current_executable is not None:
        candidates.append(
            current_executable.parent / ("uv.exe" if sys.platform.startswith("win") else "uv")
        )

    uv_from_path = shutil.which("uv")
    if uv_from_path:
        candidates.append(Path(uv_from_path))

    if sys.platform.startswith("win"):
        candidates.extend((
            Path.home() / ".local" / "bin" / "uv.exe",
            Path.home() / ".cargo" / "bin" / "uv.exe",
        ))
    else:
        candidates.extend((
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ))

    for candidate in candidates:
        expanded = candidate.expanduser()
        if expanded.exists():
            return expanded.resolve()
    return None


def _get_dev_launch_command() -> list[str]:
    launcher_script = _get_launcher_script_path()
    if not launcher_script.exists():
        return []

    project_venv_python = _get_project_virtualenv_python()
    if project_venv_python is not None:
        return [str(project_venv_python), str(launcher_script)]

    uv_executable = _get_uv_executable_path()
    if uv_executable is not None and _get_project_pyproject_path().exists():
        return [
            str(uv_executable),
            "run",
            "--project",
            str(_get_project_root()),
            "python",
            str(launcher_script),
        ]

    executable = _get_current_executable_path()
    if executable is None:
        return []

    return [str(executable), str(launcher_script)]


def _get_launch_command() -> list[str]:
    executable = _get_current_executable_path()
    if executable is None:
        return []

    if getattr(sys, "frozen", False):
        return [str(executable)]

    return _get_dev_launch_command()


def _get_working_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return _get_project_root()


def _escape_desktop_entry_exec_percent(value: str) -> str:
    escaped: list[str] = []
    index = 0

    while index < len(value):
        char = value[index]
        if char != "%":
            escaped.append(char)
            index += 1
            continue

        if index + 1 >= len(value):
            escaped.append("%%")
            index += 1
            continue

        next_char = value[index + 1]
        if next_char == "%" or next_char in _DESKTOP_ENTRY_EXEC_FIELD_CODES:
            escaped.append("%" + next_char)
            index += 2
            continue

        escaped.append("%%")
        index += 1

    return "".join(escaped)


def _quote_desktop_entry_exec_argument(value: str) -> str:
    escaped: list[str] = []

    for char in value:
        if char == "\\":
            escaped.append("\\\\\\\\")
        elif char in {'"', "`", "$"}:
            escaped.append("\\" + char)
        else:
            escaped.append(char)

    return '"' + "".join(escaped) + '"'


def _quote_posix_command(command: list[str]) -> str:
    quoted: list[str] = []

    for part in command:
        value = _escape_desktop_entry_exec_percent(str(part))
        if value == "" or any(char in _DESKTOP_ENTRY_EXEC_RESERVED_CHARS for char in value):
            quoted.append(_quote_desktop_entry_exec_argument(value))
        else:
            quoted.append(value)

    return " ".join(quoted)


def _get_windows_command_line(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def _get_windows_status() -> dict[str, Any]:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_SUBKEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, _WINDOWS_RUN_VALUE_NAME)
    except FileNotFoundError:
        return {"enabled": False, "mechanism": "windows-run"}

    return {
        "enabled": bool(str(value).strip()),
        "mechanism": "windows-run",
        "command": str(value),
    }


def _enable_windows_autostart(command: list[str]) -> dict[str, Any]:
    import winreg

    command_line = _get_windows_command_line(command)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _WINDOWS_RUN_SUBKEY) as key:
        winreg.SetValueEx(key, _WINDOWS_RUN_VALUE_NAME, 0, winreg.REG_SZ, command_line)
    return _get_windows_status()


def _disable_windows_autostart() -> dict[str, Any]:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            _WINDOWS_RUN_SUBKEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, _WINDOWS_RUN_VALUE_NAME)
    except FileNotFoundError:
        pass
    return _get_windows_status()


def _get_macos_launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / _MAC_LAUNCH_AGENT_FILENAME


def _get_macos_launch_agent_log_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / APP_NAME / "logs"


def _get_macos_launch_agent_log_paths() -> tuple[Path, Path]:
    log_dir = _get_macos_launch_agent_log_dir()
    return (
        log_dir / f"{_MAC_LAUNCH_AGENT_LABEL}.stdout.log",
        log_dir / f"{_MAC_LAUNCH_AGENT_LABEL}.stderr.log",
    )


def _get_macos_launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def _get_macos_launchctl_service_target() -> str:
    return f"{_get_macos_launchctl_domain()}/{_MAC_LAUNCH_AGENT_LABEL}"


def _run_launchctl_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _format_process_error(result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip()
    if stderr:
        return stderr

    stdout = (result.stdout or "").strip()
    if stdout:
        return stdout

    return f"exit code {result.returncode}"


def _parse_launchctl_print_output(output: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "loaded": True,
        "running": False,
    }

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()

        if key == "state":
            status["state"] = value
            status["running"] = value == "running"
        elif key == "last exit code":
            try:
                status["last_exit_code"] = int(value)
            except ValueError:
                status["last_exit_code"] = value
        elif key == "pid":
            try:
                status["pid"] = int(value)
            except ValueError:
                continue

    return status


def _get_macos_launch_agent_runtime_status() -> dict[str, Any]:
    result = _run_launchctl_command([
        "print",
        _get_macos_launchctl_service_target(),
    ])
    if result.returncode != 0:
        return {
            "loaded": False,
            "running": False,
        }
    return _parse_launchctl_print_output(result.stdout)


def _bootout_macos_launch_agent(path: Path | None = None) -> None:
    attempts: list[subprocess.CompletedProcess[str]] = []
    domain = _get_macos_launchctl_domain()

    if path is not None and path.exists():
        attempts.append(_run_launchctl_command([
            "bootout",
            domain,
            str(path),
        ]))
        if attempts[-1].returncode == 0:
            return

    attempts.append(_run_launchctl_command([
        "bootout",
        _get_macos_launchctl_service_target(),
    ]))
    if attempts[-1].returncode == 0:
        return

    runtime_status = _get_macos_launch_agent_runtime_status()
    if runtime_status.get("loaded"):
        raise RuntimeError(_format_process_error(attempts[-1]))


def _bootstrap_macos_launch_agent(path: Path) -> None:
    _bootout_macos_launch_agent(path)

    result = _run_launchctl_command([
        "bootstrap",
        _get_macos_launchctl_domain(),
        str(path),
    ])
    if result.returncode != 0:
        raise RuntimeError(_format_process_error(result))


def _get_virtualenv_root(command: list[str]) -> Path | None:
    if not command:
        return None

    executable = Path(command[0]).expanduser()
    if not executable.is_absolute():
        return None

    for candidate in (executable, executable.resolve()):
        parent_name = candidate.parent.name.lower()
        if parent_name not in {"bin", "scripts"}:
            continue
        venv_root = candidate.parent.parent
        if (venv_root / "pyvenv.cfg").exists():
            return venv_root
    return None


def _prepend_path_entry(entry: Path, existing_path: str) -> str:
    parts = [str(entry)]
    if existing_path:
        parts.extend(part for part in existing_path.split(os.pathsep) if part and part != str(entry))
    return os.pathsep.join(parts)


def _build_autostart_environment(command: list[str]) -> dict[str, str]:
    path_value = os.environ.get("PATH")
    environment = {
        "HOME": str(Path.home()),
        "PATH": path_value or _DEFAULT_POSIX_PATH,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }

    for key in _PASSTHROUGH_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            environment[key] = value

    venv_root = _get_virtualenv_root(command)
    if venv_root is not None:
        scripts_dir = venv_root / ("Scripts" if sys.platform.startswith("win") else "bin")
        environment["VIRTUAL_ENV"] = str(venv_root)
        environment["PATH"] = _prepend_path_entry(scripts_dir, environment["PATH"])

    return environment


def _build_macos_launch_agent_payload(command: list[str]) -> dict[str, Any]:
    stdout_path, stderr_path = _get_macos_launch_agent_log_paths()
    return {
        "Label": _MAC_LAUNCH_AGENT_LABEL,
        "ProgramArguments": command,
        "RunAtLoad": True,
        "WorkingDirectory": str(_get_working_directory()),
        "EnvironmentVariables": _build_autostart_environment(command),
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }


def _get_macos_status() -> dict[str, Any]:
    path = _get_macos_launch_agent_path()
    configured = path.exists()
    runtime_status = _get_macos_launch_agent_runtime_status()

    status = {
        "enabled": configured and bool(runtime_status.get("loaded")),
        "configured": configured,
        "mechanism": "launch-agent",
        "path": str(path),
        "label": _MAC_LAUNCH_AGENT_LABEL,
        "domain": _get_macos_launchctl_domain(),
        **runtime_status,
    }

    if configured and not runtime_status.get("loaded"):
        status["reason"] = "launch_agent_not_loaded"
    elif not configured and runtime_status.get("loaded"):
        status["reason"] = "launch_agent_stale_session"

    return status


def _enable_macos_autostart(command: list[str]) -> dict[str, Any]:
    path = _get_macos_launch_agent_path()
    payload = _build_macos_launch_agent_payload(command)
    stdout_path, _stderr_path = _get_macos_launch_agent_log_paths()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, plistlib.dumps(payload).decode("utf-8"))
    path.chmod(0o644)
    _bootstrap_macos_launch_agent(path)

    status = _get_macos_status()
    if not status.get("enabled"):
        raise RuntimeError("launchctl did not report the LaunchAgent as loaded")
    return status


def _disable_macos_autostart() -> dict[str, Any]:
    path = _get_macos_launch_agent_path()
    _bootout_macos_launch_agent(path if path.exists() else None)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return _get_macos_status()


def _get_linux_autostart_path() -> Path:
    return Path.home() / ".config" / "autostart" / _LINUX_AUTOSTART_FILENAME


def _get_linux_status() -> dict[str, Any]:
    path = _get_linux_autostart_path()
    return {
        "enabled": path.exists(),
        "mechanism": "xdg-autostart",
        "path": str(path),
    }


def _enable_linux_autostart(command: list[str]) -> dict[str, Any]:
    path = _get_linux_autostart_path()
    working_directory = _get_working_directory()
    content = "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        f"Exec={_quote_posix_command(command)}",
        f"Path={working_directory}",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ))
    atomic_write_text(path, content)
    path.chmod(0o644)
    return _get_linux_status()


def _disable_linux_autostart() -> dict[str, Any]:
    path = _get_linux_autostart_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    return _get_linux_status()


def _get_platform_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unsupported"


def get_autostart_status() -> dict[str, Any]:
    platform_name = _get_platform_name()
    command = _get_launch_command()
    manageable = bool(command)

    if platform_name == "windows":
        status = _get_windows_status()
    elif platform_name == "macos":
        status = _get_macos_status()
    elif platform_name == "linux":
        status = _get_linux_status()
    else:
        return {
            "ok": True,
            "supported": False,
            "enabled": False,
            "platform": platform_name,
            "reason": "unsupported_platform",
        }

    status.update({
        "ok": True,
        "supported": manageable,
        "manageable": manageable,
        "platform": platform_name,
    })

    if not manageable:
        status.setdefault("reason", "launch_command_unavailable")
    return status


def enable_autostart() -> dict[str, Any]:
    platform_name = _get_platform_name()
    command = _get_launch_command()

    if platform_name == "unsupported":
        return {
            "ok": False,
            "supported": False,
            "enabled": False,
            "platform": platform_name,
            "error_code": "unsupported_platform",
            "error": "Autostart is not supported on this platform",
        }

    if not command:
        return {
            "ok": False,
            "supported": False,
            "enabled": False,
            "platform": platform_name,
            "error_code": "launch_command_unavailable",
            "error": "Autostart launch command is unavailable",
        }

    try:
        if platform_name == "windows":
            status = _enable_windows_autostart(command)
        elif platform_name == "macos":
            status = _enable_macos_autostart(command)
        else:
            status = _enable_linux_autostart(command)
    except Exception as exc:
        return {
            "ok": False,
            "supported": True,
            "enabled": False,
            "platform": platform_name,
            "error_code": "enable_failed",
            "error": str(exc),
        }

    status.update({
        "ok": True,
        "supported": True,
        "platform": platform_name,
    })
    return status


def disable_autostart() -> dict[str, Any]:
    platform_name = _get_platform_name()
    command = _get_launch_command()

    if platform_name == "unsupported":
        return {
            "ok": False,
            "supported": False,
            "enabled": False,
            "platform": platform_name,
            "error_code": "unsupported_platform",
            "error": "Autostart is not supported on this platform",
        }

    try:
        if platform_name == "windows":
            status = _disable_windows_autostart()
        elif platform_name == "macos":
            status = _disable_macos_autostart()
        else:
            status = _disable_linux_autostart()
    except Exception as exc:
        return {
            "ok": False,
            "supported": bool(command),
            "enabled": False,
            "platform": platform_name,
            "error_code": "disable_failed",
            "error": str(exc),
        }

    status.update({
        "ok": True,
        "supported": bool(command),
        "platform": platform_name,
    })
    if not command:
        status["reason"] = "launch_command_unavailable"
    return status
