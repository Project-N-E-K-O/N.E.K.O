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


def _solo_idle_context() -> dict:
    return {
        "state": {
            "config": {"dry_run": False, "live_mode": "solo_stream"},
            "live_connection": {"state": "connected", "connected": True},
            "live_status": {"summary": "ready_to_stream", "reason": "ready"},
            "live_state": {
                "state": "idle",
                "reason": "no_recent_activity",
                "idle_hosting_candidate": True,
            },
            "idle_hosting_status": {
                "eligible": True,
                "reason": "ready",
            },
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "waiting_for_activity",
                "reason": "idle_hosting_candidate",
            },
            "recent_results": [],
        }
    }


def _solo_quiet_context(*, dry_run: bool = False) -> dict:
    return {
        "state": {
            "config": {"dry_run": dry_run, "live_mode": "solo_stream"},
            "live_connection": {"state": "connected", "connected": True},
            "live_status": {"summary": "ready_to_stream", "reason": "ready"},
            "live_state": {
                "state": "quiet",
                "reason": "quiet_activity_gap",
                "idle_hosting_candidate": False,
            },
            "idle_hosting_status": {
                "eligible": False,
                "reason": "not_candidate",
            },
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "waiting_for_activity",
                "reason": "waiting_for_danmaku",
            },
            "recent_results": [],
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
        capture_output=True,
        timeout=30,
    )


def test_monitor_live_script_reports_latest_response_latency(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latency(3000))

    assert completed.returncode == 0, completed.stderr
    assert "latency=3.0s" in completed.stdout
    assert "last_result=pushed" in completed.stdout


def test_monitor_live_script_reports_solo_stream_idle_readiness(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _solo_idle_context())

    assert completed.returncode == 0, completed.stderr
    assert "mode=solo_stream" in completed.stdout
    assert "live_status=ready_to_stream" in completed.stdout
    assert "live_state=idle" in completed.stdout
    assert "idle_candidate=True" in completed.stdout
    assert "idle_ready=True" in completed.stdout
    assert "idle_reason=ready" in completed.stdout


def test_monitor_live_script_suggests_idle_hosting_when_solo_stream_is_ready(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _solo_idle_context())

    assert completed.returncode == 0, completed.stderr
    assert "solo_test_hint=expect_idle_hosting" in completed.stdout
    assert "solo_test_focus=idle_hosting" in completed.stdout


def test_monitor_live_script_focuses_danmaku_response_when_solo_stream_is_ready(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _solo_quiet_context())

    assert completed.returncode == 0, completed.stderr
    assert "solo_test_focus=danmaku_response" in completed.stdout


def test_monitor_live_script_focuses_chain_only_when_dry_run_is_enabled(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _solo_quiet_context(dry_run=True))

    assert completed.returncode == 0, completed.stderr
    assert "solo_test_focus=chain_only" in completed.stdout


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


def test_monitor_live_script_reports_context_failure_without_stack_noise(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing-context.json"

    completed = _run_monitor_args("-Once", "-ContextJsonPath", str(missing_path))

    assert completed.returncode != 0
    assert "context=failed" in completed.stdout
    assert "error=" in completed.stdout
    assert "At " not in completed.stderr
