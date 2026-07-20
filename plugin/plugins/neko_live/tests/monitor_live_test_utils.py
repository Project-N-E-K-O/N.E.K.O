from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _run_monitor(
    tmp_path: Path,
    context: dict,
    *extra_args: str,
    use_default_backend_log: bool = False,
) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")

    root = Path(__file__).resolve().parents[1]
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    args = list(extra_args)
    if "-BackendLogPath" not in args and not use_default_backend_log:
        backend_log_path = tmp_path / "backend.log"
        backend_log_path.write_text("", encoding="utf-8")
        args.extend(["-BackendLogPath", str(backend_log_path)])

    return subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "tools" / "monitor_live.ps1"),
            "-Once",
            "-ContextJsonPath",
            str(context_path),
            *args,
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )


def _run_monitor_args(*args: str) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")

    root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "tools" / "monitor_live.ps1"),
            *args,
        ],
        cwd=root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
    )
