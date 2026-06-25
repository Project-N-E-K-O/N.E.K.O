from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
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


def _context_with_latest_route_and_signal(*, latest_age_seconds: int = 12) -> dict:
    return {
        "state": {
            "config": {"dry_run": False, "live_mode": "solo_stream"},
            "live_connection": {"state": "connected", "connected": True},
            "live_status": {"summary": "ready_to_stream", "reason": "ready"},
            "live_state": {
                "state": "engaged",
                "reason": "recent_activity",
                "idle_hosting_candidate": False,
                "last_viewer_activity_age_sec": 42.0,
                "last_output_age_sec": 8.0,
            },
            "idle_hosting_status": {
                "eligible": False,
                "reason": "not_candidate",
            },
            "active_engagement_status": {
                "eligible": False,
                "reason": "recent_danmaku_output",
                "minimum_interval_remaining": 0.0,
                "recent_danmaku_cooldown_remaining": 24.0,
                "idle_hosting_wait_remaining": 4.5,
            },
            "live_director_status": {
                "next_auto_action": "idle_hosting",
                "eligible": False,
                "reason": "approaching_idle_hosting",
                "cooldown_remaining": 4.5,
            },
            "safety": {"status": "running"},
            "speech_explanation": {
                "summary": "recently_spoke",
                "reason": "recent_output",
                "last_result_status": "pushed",
            },
            "recent_results": [
                {
                    "status": "pushed",
                    "reason": "dispatcher_pushed",
                    "response_module": "danmaku_response",
                    "event_signal": "gift_signal",
                    "response_latency_ms": 3200,
                    "created_at": (datetime.now(timezone.utc) - timedelta(seconds=latest_age_seconds)).isoformat(),
                    "event": {
                        "source": "live_danmaku",
                        "danmaku_text": "猫猫今天怎么这么安静",
                        "topic_source": "bili_trending",
                        "topic_shape": "either_or",
                        "topic_title": "猫猫今天怎么这么安静",
                        "topic_key": "bili:BV_TEST",
                        "topic_hook": "Make the topic into one concrete A/B choice.",
                        "topic_pattern": "Turn the title into two concrete sides.",
                        "host_beat_shape": "soft_observation",
                        "host_beat_title": "\u5b89\u9759\u7684\u76f4\u64ad\u95f4\u6c14\u6c1b",
                        "host_beat_hint": "Say one soft concrete observation.",
                    },
                }
            ],
            "recent_profiles": [{"uid": "1"}, {"uid": "2"}],
            "solo_test_readiness": {
                "summary": "ready_for_live_test",
                "profile_count": 2,
                "items": [
                    {"id": "preflight", "status": "ready", "reason": "ready"},
                    {"id": "test_isolation", "status": "warning", "reason": "viewer_profiles_present"},
                ],
            },
        }
    }


def _context_from_other_checkout() -> dict:
    context = _context_with_latest_route_and_signal()
    context["plugin"] = {
        "config_path": r"D:\Users\zheng\Documents\Code\other\N.E.K.O\plugin\plugins\neko_roast\plugin.toml"
    }
    return context


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


def test_monitor_live_script_defaults_to_plugin_host_port() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "tools" / "monitor_live.ps1"

    assert '[string]$BaseUrl = "http://127.0.0.1:48916"' in script.read_text(encoding="utf-8")


def test_monitor_live_script_prints_live_test_help() -> None:
    completed = _run_monitor_args("-Help")

    assert completed.returncode == 0, completed.stderr
    assert "NEKO Live monitor" in completed.stdout
    assert "-ExpectRealOutput" in completed.stdout
    assert "-BackendLogPath" in completed.stdout
    assert "alerts" in completed.stdout


def test_monitor_live_script_reports_checkout_mismatch(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_from_other_checkout())

    assert completed.returncode == 0, completed.stderr
    assert "checkout=mismatch" in completed.stdout


def test_monitor_live_script_reports_latest_response_latency(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latency(3000))

    assert completed.returncode == 0, completed.stderr
    assert "latency=3.0s" in completed.stdout
    assert "last_result=pushed" in completed.stdout


def test_monitor_live_script_reports_latest_route_and_signal(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal())

    assert completed.returncode == 0, completed.stderr
    assert "latest_status=pushed" in completed.stdout
    assert "latest_route=danmaku_response" in completed.stdout
    assert "latest_signal=gift_signal" in completed.stdout
    assert "latest_source=live_danmaku" in completed.stdout
    assert "latest_text=猫猫今天怎么这么安静" in completed.stdout
    assert "latest_reason=dispatcher_pushed" in completed.stdout
    latest_age_match = re.search(r"latest_age=(\d+\.\d)s", completed.stdout)
    assert latest_age_match is not None
    assert 12.0 <= float(latest_age_match.group(1)) <= 20.0
    assert "latest_age_status=ok" in completed.stdout
    assert "alerts=-" in completed.stdout


def test_monitor_live_script_reports_stale_latest_result_age(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(latest_age_seconds=90))

    assert completed.returncode == 0, completed.stderr
    assert "latest_age_status=warn" in completed.stdout


def test_monitor_live_script_reports_very_stale_latest_result_age(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(latest_age_seconds=240))

    assert completed.returncode == 0, completed.stderr
    assert "latest_age_status=stale" in completed.stdout


def test_monitor_live_script_reports_pacing_and_active_topic_fields(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal())

    assert completed.returncode == 0, completed.stderr
    assert "viewer_age=42.0s" in completed.stdout
    assert "output_age=8.0s" in completed.stdout
    assert "latest_topic_source=bili_trending" in completed.stdout
    assert "latest_topic_shape=either_or" in completed.stdout
    assert "latest_topic_title=猫猫今天怎么这么安静" in completed.stdout
    assert "latest_topic_key=bili:BV_TEST" in completed.stdout
    assert "latest_topic_hook=Make_the_topic_into_one_concrete_A/B_choice." in completed.stdout
    assert "latest_topic_pattern=Turn_the_title_into_two_concrete_sides." in completed.stdout
    assert "latest_topic_repeat=False" in completed.stdout
    assert "latest_host_beat_shape=soft_observation" in completed.stdout
    assert "latest_host_beat_title=\u5b89\u9759\u7684\u76f4\u64ad\u95f4\u6c14\u6c1b" in completed.stdout
    assert "latest_host_beat_hint=Say_one_soft_concrete_observation." in completed.stdout
    assert "active_min_wait=0.0s" in completed.stdout
    assert "active_danmaku_wait=24.0s" in completed.stdout
    assert "active_idle_wait=4.5s" in completed.stdout
    assert "director_action=idle_hosting" in completed.stdout
    assert "director_reason=approaching_idle_hosting" in completed.stdout
    assert "director_eligible=False" in completed.stdout
    assert "director_wait=4.5s" in completed.stdout
    assert "profile_count=2" in completed.stdout
    assert "solo_readiness=ready_for_live_test" in completed.stdout
    assert "test_isolation=warning" in completed.stdout
    assert "test_isolation_reason=viewer_profiles_present" in completed.stdout
    assert "readiness_warn=test_isolation" in completed.stdout
    assert "readiness_blocked=-" in completed.stdout
    assert "solo_test_hint=clear_viewer_profiles" in completed.stdout


def test_monitor_live_script_reports_repeated_active_topic_key(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"].append(
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "bili:BV_TEST",
                "topic_title": "previous matching topic",
            },
        }
    )

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_topic_key=bili:BV_TEST" in completed.stdout
    assert "latest_topic_repeat=True" in completed.stdout


def test_monitor_live_script_reports_backend_log_watchdog_and_contamination(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[voice] voice playback gate watchdog timeout",
                "[project-N-E-K-O-Warthunder] proactive bridge output queued",
                "[neko] send_lanlan_response len=123",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "log_watchdog=True" in completed.stdout
    assert "log_contamination=warthunder" in completed.stdout
    assert "log_reply_len=123" in completed.stdout
    assert "log_reply_length_status=warn" in completed.stdout
    assert "solo_test_focus=test_isolation" in completed.stdout


def test_monitor_live_script_auto_detects_default_backend_log(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    default_log = root / ".codex-backend-live-test.log"
    default_log.unlink(missing_ok=True)
    default_log.write_text("[voice] voice playback gate watchdog timeout\n", encoding="utf-8")
    try:
        completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal())
    finally:
        default_log.unlink(missing_ok=True)

    assert completed.returncode == 0, completed.stderr
    assert "log_watchdog=True" in completed.stdout


def test_monitor_live_script_alerts_when_real_output_log_is_missing(tmp_path: Path) -> None:
    missing_log = tmp_path / "missing-backend.log"

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(missing_log),
    )

    assert completed.returncode == 0, completed.stderr
    assert "alerts=backend_log_missing" in completed.stdout


def test_monitor_live_script_aggregates_real_output_alerts(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[voice] voice playback gate watchdog timeout",
                "[neko] send_lanlan_response len=123",
            ]
        ),
        encoding="utf-8",
    )
    context = _context_with_latest_route_and_signal(latest_age_seconds=240)
    context["state"]["config"]["dry_run"] = True

    completed = _run_monitor(
        tmp_path,
        context,
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "alerts=dry_run,latest_stale,playback_watchdog,long_reply" in completed.stdout


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
