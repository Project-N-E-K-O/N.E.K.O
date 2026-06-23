from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def test_monitor_live_script_reports_latest_response_latency(tmp_path: Path) -> None:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")

    root = Path(__file__).resolve().parents[1]
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(
            {
                "state": {
                    "config": {"dry_run": False},
                    "live_connection": {"state": "connected", "connected": True},
                    "safety": {"status": "running"},
                    "speech_explanation": {
                        "summary": "recently_spoke",
                        "reason": "recent_output",
                        "last_result_status": "pushed",
                        "last_result_latency_ms": 3000,
                    },
                    "recent_results": [
                        {
                            "status": "pushed",
                            "reason": "",
                            "response_latency_ms": 3000,
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
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
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert "latency=3.0s" in completed.stdout
    assert "last_result=pushed" in completed.stdout
