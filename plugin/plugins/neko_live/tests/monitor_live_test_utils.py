from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_BARE_SWITCH_PATTERN = re.compile(r"^-[A-Za-z][A-Za-z0-9]*$")


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _ps_argument(value: str) -> str:
    # Parameter names such as -Once / -BackendLogPath must stay unquoted so
    # PowerShell binds them as parameters instead of positional strings.
    if _BARE_SWITCH_PATTERN.match(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _run_powershell_script(script: Path, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")

    # Windows PowerShell 5.1 encodes redirected stdout with the console
    # codepage (GBK on zh-CN hosts), which breaks the UTF-8 decode below.
    # Force UTF-8 output inside the child before the script writes anything.
    command = " ".join(
        [
            "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { };",
            "$OutputEncoding = [System.Text.Encoding]::UTF8;",
            "&",
            _ps_argument(str(script)),
            *[_ps_argument(arg) for arg in args],
            "; exit $LASTEXITCODE",
        ]
    )
    return subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=30,
    )


def _run_monitor(
    tmp_path: Path,
    context: dict,
    *extra_args: str,
    use_default_backend_log: bool = False,
) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[1]
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    args = list(extra_args)
    if "-BackendLogPath" not in args and not use_default_backend_log:
        backend_log_path = tmp_path / "backend.log"
        backend_log_path.write_text("", encoding="utf-8")
        args.extend(["-BackendLogPath", str(backend_log_path)])

    return _run_powershell_script(
        root / "tools" / "monitor_live.ps1",
        ["-Once", "-ContextJsonPath", str(context_path), *args],
        cwd=root,
    )


def _run_monitor_args(*args: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[1]
    return _run_powershell_script(
        root / "tools" / "monitor_live.ps1",
        list(args),
        cwd=root,
    )
