from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _context_with_latency(latency_ms: int) -> dict:
    return {
        "state": {
            "config": {"dry_run": False},
            "live_connection": {"state": "connected", "connected": True},
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "recently_spoke",
                "reason": "recent_output",
                "last_result_status": "pushed",
                "last_result_latency_ms": latency_ms,
            },
            "recent_results": [
                {
                    "status": "pushed",
                    "reason": "",
                    "response_latency_ms": latency_ms,
                }
            ],
        }
    }


def _run_monitor(tmp_path: Path, context: dict, *extra_args: str) -> subprocess.CompletedProcess[str]:
    shell = _powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")

    root = Path(__file__).resolve().parents[1]
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")

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
            *extra_args,
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_monitor_live_script_reports_latest_response_latency(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latency(3000))

    assert completed.returncode == 0, completed.stderr
    assert "latency=3.0s" in completed.stdout
    assert "last_result=pushed" in completed.stdout


def test_monitor_live_script_classifies_slow_response_latency(tmp_path: Path) -> None:
    completed = _run_monitor(
        tmp_path,
        _context_with_latency(12500),
        "-WarnLatencyMs",
        "5000",
        "-SlowLatencyMs",
        "10000",
    )

    assert completed.returncode == 0, completed.stderr
    assert "latency=13s" in completed.stdout
    assert "latency_status=slow" in completed.stdout
