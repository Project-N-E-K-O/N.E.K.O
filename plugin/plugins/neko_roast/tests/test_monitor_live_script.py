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
            "config": {"dry_run": False, "live_mode": "solo_stream", "activity_level": "standard"},
            "live_connection": {"state": "connected", "connected": True},
            "live_status": {"summary": "ready_to_stream", "reason": "ready"},
            "live_state": {
                "state": "engaged",
                "reason": "recent_activity",
                "idle_hosting_candidate": False,
                "last_viewer_activity_age_sec": 42.0,
                "last_output_age_sec": 8.0,
                "engaged_threshold_seconds": 60.0,
                "idle_threshold_seconds": 120.0,
            },
            "idle_hosting_status": {
                "eligible": False,
                "reason": "not_candidate",
            },
            "active_engagement_status": {
                "eligible": False,
                "reason": "recent_danmaku_output",
                "min_interval_seconds": 120.0,
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
                        "topic_intent": "quick_vote",
                        "topic_reply_affordance": "viewer can answer with one side",
                        "topic_recent_skip_reason": "single_viewer_flood",
                        "host_beat_key": "idle:soft_observation:quiet-room",
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
    assert "quiet_after / idle_after" in completed.stdout
    assert "latest_uid / avatar_repeat_uid" in completed.stdout
    latest_route_help = re.search(r"latest_route\s+(.+)", completed.stdout)
    assert latest_route_help is not None
    assert "warmup_hosting" in latest_route_help.group(1)
    assert "latest_output_len" in completed.stdout
    assert "recent_long_reply_*" in completed.stdout
    assert "recent_generic_host_prompt_count" in completed.stdout
    assert "log_generic_host_prompt" in completed.stdout
    assert "avatar_repeat_count" in completed.stdout
    assert "recent_total" in completed.stdout
    assert "recent_*" in completed.stdout
    assert "recent_actual_*" in completed.stdout
    assert "recent_pushed / recent_dry_run / recent_skipped / recent_failed" in completed.stdout
    assert "recent_topic_skip_*" in completed.stdout
    assert "recent_topic_source_*" in completed.stdout
    assert "recent_topic_intent_*" in completed.stdout
    assert "topic_repeat / avatar_repeat" in completed.stdout
    assert "topic_filter_direct_request" in completed.stdout
    assert "topic_filter_reaction" in completed.stdout
    assert "topic_filter_runtime_feedback" in completed.stdout
    assert "topic_intent_bias" in completed.stdout
    assert "topic_source_bias" in completed.stdout
    assert "generic_host_prompt" in completed.stdout
    assert "host_beat_repeat" in completed.stdout
    assert "proactive_in_engaged" in completed.stdout
    assert "warmup_repeat" in completed.stdout
    assert "warmup_missing / idle_missing / active_missing" in completed.stdout
    assert "test_isolation" in completed.stdout


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
    assert "quiet_after=60.0s" in completed.stdout
    assert "idle_after=120.0s" in completed.stdout
    assert "entrance_pacing_window=45.0s" in completed.stdout
    assert "latest_topic_source=bili_trending" in completed.stdout
    assert "latest_topic_shape=either_or" in completed.stdout
    assert "latest_topic_title=猫猫今天怎么这么安静" in completed.stdout
    assert "latest_topic_key=bili:BV_TEST" in completed.stdout
    assert "latest_topic_hook=Make_the_topic_into_one_concrete_A/B_choice." in completed.stdout
    assert "latest_topic_pattern=Turn_the_title_into_two_concrete_sides." in completed.stdout
    assert "latest_topic_intent=quick_vote" in completed.stdout
    assert "latest_topic_reply_affordance=viewer_can_answer_with_one_side" in completed.stdout
    assert "latest_topic_recent_skip_reason=single_viewer_flood" in completed.stdout
    assert "latest_topic_repeat=False" in completed.stdout
    assert "latest_host_beat_key=idle:soft_observation:quiet-room" in completed.stdout
    assert "latest_host_beat_shape=soft_observation" in completed.stdout
    assert "latest_host_beat_title=\u5b89\u9759\u7684\u76f4\u64ad\u95f4\u6c14\u6c1b" in completed.stdout
    assert "latest_host_beat_hint=Say_one_soft_concrete_observation." in completed.stdout
    assert "active_min_wait=0.0s" in completed.stdout
    assert "active_min_interval=120.0s" in completed.stdout
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


def test_monitor_live_script_alerts_when_real_output_test_is_not_isolated(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "test_isolation" in alerts_match.group(1).split(",")


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
    assert "alerts=topic_repeat" in completed.stdout


def test_monitor_live_script_ignores_skipped_active_topic_key_for_repeat(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "bili:BV_TEST",
                "topic_title": "latest topic",
            },
        },
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "bili:BV_TEST",
                "topic_title": "skipped matching topic",
            },
        },
        {
            "status": "failed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "bili:BV_TEST",
                "topic_title": "failed matching topic",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_topic_key=bili:BV_TEST" in completed.stdout
    assert "latest_topic_repeat=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_repeat" not in alerts_match.group(1).split(",")


def test_monitor_live_script_ignores_skipped_latest_active_topic_for_repeat(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "bili:BV_TEST",
                "topic_title": "latest skipped topic",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "bili:BV_TEST",
                "topic_title": "previous output topic",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_topic_key=bili:BV_TEST" in completed.stdout
    assert "latest_topic_repeat=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_repeat" not in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_active_topic_intent_mix(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_intent": "quick_vote",
                "topic_key": "topic:1",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_intent": "tiny_answer",
                "topic_key": "topic:2",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_intent": "quick_vote",
                "topic_key": "topic:3",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_intent_quick_vote=2" in completed.stdout
    assert "recent_topic_intent_tiny_answer=1" in completed.stdout
    assert "recent_topic_intent_tease_back=0" in completed.stdout
    assert "recent_topic_intent_agree_or_pushback=0" in completed.stdout


def test_monitor_live_script_reports_active_topic_source_mix(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_source": "fallback",
                "topic_key": "topic:1",
            },
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_source": "bili_trending",
                "topic_key": "topic:2",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_source": "recent_danmaku",
                "topic_key": "topic:3",
            },
        },
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_source": "fallback",
                "topic_key": "topic:4",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_source_fallback=1" in completed.stdout
    assert "recent_topic_source_bili_trending=1" in completed.stdout
    assert "recent_topic_source_recent_danmaku=1" in completed.stdout


def test_monitor_live_script_alerts_when_active_topic_source_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_source": "fallback", "topic_key": "topic:1"},
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_source": "fallback", "topic_key": "topic:2"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_source": "fallback", "topic_key": "topic:3"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_source": "bili_trending", "topic_key": "topic:4"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_source_fallback=3" in completed.stdout
    assert "recent_topic_source_bili_trending=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_source_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_topic_intent_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "quick_vote", "topic_key": "topic:1"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "quick_vote", "topic_key": "topic:2"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "quick_vote", "topic_key": "topic:3"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "tiny_answer", "topic_key": "topic:4"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_intent_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_ignores_skipped_active_topic_intents_for_bias(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "quick_vote", "topic_key": "topic:1"},
        },
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "quick_vote", "topic_key": "topic:2"},
        },
        {
            "status": "failed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "quick_vote", "topic_key": "topic:3"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_intent": "tiny_answer", "topic_key": "topic:4"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_intent_quick_vote=0" in completed.stdout
    assert "recent_topic_intent_tiny_answer=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_intent_bias" not in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_repeated_avatar_roast_uid(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "avatar_roast",
            "event": {"source": "live_danmaku", "uid": "42", "danmaku_text": "第一句"},
        },
        {
            "status": "pushed",
            "response_module": "danmaku_response",
            "event": {"source": "live_danmaku", "uid": "77", "danmaku_text": "路过"},
        },
        {
            "status": "pushed",
            "response_module": "avatar_roast",
            "event": {"source": "live_danmaku", "uid": "42", "danmaku_text": "又来了"},
        },
    ]

    completed = _run_monitor(tmp_path, context, "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    assert "latest_uid=42" in completed.stdout
    assert "avatar_repeat_uid=42" in completed.stdout
    assert "avatar_repeat_count=2" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "avatar_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_ignores_skipped_avatar_roast_for_repeat(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "avatar_roast",
            "event": {"source": "live_danmaku", "uid": "42", "danmaku_text": "first output"},
        },
        {
            "status": "skipped",
            "response_module": "avatar_roast",
            "event": {"source": "live_danmaku", "uid": "42", "danmaku_text": "skipped duplicate"},
        },
        {
            "status": "failed",
            "response_module": "avatar_roast",
            "event": {"source": "live_danmaku", "uid": "42", "danmaku_text": "failed duplicate"},
        },
    ]

    completed = _run_monitor(tmp_path, context, "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    assert "avatar_repeat_uid=-" in completed.stdout
    assert "avatar_repeat_count=0" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "avatar_repeat" not in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_latest_output_is_long(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"][0]["output"] = "x" * 81
    missing_log = tmp_path / "missing-backend.log"

    completed = _run_monitor(
        tmp_path,
        context,
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(missing_log),
    )

    assert completed.returncode == 0, completed.stderr
    assert "latest_output_len=81" in completed.stdout
    assert "latest_output_length_status=warn" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "long_reply" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_recent_output_contains_long_reply(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "danmaku_response", "output": "short"},
        {"status": "pushed", "response_module": "active_engagement", "output": "x" * 82},
        {"status": "pushed", "response_module": "idle_hosting", "output": "tiny"},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_long_reply_count=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "long_reply" in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_long_reply_counts_by_route(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "danmaku_response",
            "output": "x" * 81,
            "event": {"source": "live_danmaku", "uid": "42"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "output": "y" * 90,
            "event": {"source": "active_engagement", "topic_intent": "quick_vote"},
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "output": "short",
            "event": {"source": "idle_hosting"},
        },
    ]

    completed = _run_monitor(tmp_path, context, "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    assert "recent_long_reply_count=2" in completed.stdout
    assert "recent_long_reply_avatar_roast=0" in completed.stdout
    assert "recent_long_reply_danmaku_response=1" in completed.stdout
    assert "recent_long_reply_idle_hosting=0" in completed.stdout
    assert "recent_long_reply_active_engagement=1" in completed.stdout


def test_monitor_live_script_alerts_when_active_output_looks_like_generic_host_bait(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "output": "大家快来互动吧，弹幕刷起来",
        },
        {"status": "pushed", "response_module": "danmaku_response", "output": "短短接住"},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_generic_host_prompt_count=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "generic_host_prompt" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_output_uses_english_chat_bait(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "output": "Let's get the chat moving a little.",
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_generic_host_prompt_count=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "generic_host_prompt" in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_recent_route_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "danmaku_response", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "idle_hosting", "event": {"uid": "__neko_idle__", "source": "idle_hosting"}},
        {"status": "pushed", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
        {"status": "pushed", "response_module": "warmup_hosting", "event": {"uid": "__neko_warmup__", "source": "warmup_hosting"}},
        {"status": "skipped", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_avatar_roast=1" in completed.stdout
    assert "recent_danmaku_response=1" in completed.stdout
    assert "recent_idle_hosting=1" in completed.stdout
    assert "recent_active_engagement=2" in completed.stdout
    assert "recent_warmup_hosting=1" in completed.stdout


def test_monitor_live_script_reports_recent_actual_route_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "dry_run", "response_module": "danmaku_response", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "skipped", "response_module": "idle_hosting", "event": {"uid": "__neko_idle__", "source": "idle_hosting"}},
        {"status": "failed", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
        {"status": "pushed", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
        {"status": "dry_run", "response_module": "warmup_hosting", "event": {"uid": "__neko_warmup__", "source": "warmup_hosting"}},
        {"status": "skipped", "response_module": "warmup_hosting", "event": {"uid": "__neko_warmup__", "source": "warmup_hosting"}},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_avatar_roast=1" in completed.stdout
    assert "recent_danmaku_response=1" in completed.stdout
    assert "recent_idle_hosting=1" in completed.stdout
    assert "recent_active_engagement=2" in completed.stdout
    assert "recent_warmup_hosting=2" in completed.stdout
    assert "recent_actual_avatar_roast=1" in completed.stdout
    assert "recent_actual_danmaku_response=1" in completed.stdout
    assert "recent_actual_idle_hosting=0" in completed.stdout
    assert "recent_actual_active_engagement=1" in completed.stdout
    assert "recent_actual_warmup_hosting=1" in completed.stdout


def test_monitor_live_script_alerts_when_recent_route_mix_is_avatar_biased(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "2", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "3", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "4", "source": "live_danmaku"}},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "avatar_roast_share=100%" in completed.stdout
    assert "avatar_roast_bias=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "avatar_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_ignores_skipped_routes_for_avatar_bias(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "skipped", "response_module": "avatar_roast", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "skipped", "response_module": "avatar_roast", "event": {"uid": "2", "source": "live_danmaku"}},
        {"status": "failed", "response_module": "avatar_roast", "event": {"uid": "3", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "danmaku_response", "event": {"uid": "4", "source": "live_danmaku"}},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "avatar_roast_share=0%" in completed.stdout
    assert "avatar_roast_bias=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "avatar_bias" not in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_recent_status_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "dry_run", "response_module": "danmaku_response", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "skipped", "response_module": "idle_hosting", "event": {"uid": "__neko_idle__", "source": "idle_hosting"}},
        {"status": "failed", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
        {"status": "pushed", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_total=5" in completed.stdout
    assert "recent_pushed=2" in completed.stdout
    assert "recent_dry_run=1" in completed.stdout
    assert "recent_skipped=1" in completed.stdout
    assert "recent_failed=1" in completed.stdout


def test_monitor_live_script_reports_recent_topic_skip_reason_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_recent_skip_reason": "single_viewer_flood",
                "shape_guard_reason": "recent_shape_streak",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "single_viewer_flood"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "stale_recent_danmaku"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "avatar_roast_context"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "non_output_danmaku"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "filtered_recent_danmaku"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "filtered_direct_request"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "filtered_reaction"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "filtered_runtime_feedback"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "viewer_to_viewer_mention"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_recent_skip_reason": "recent_danmaku_source_streak",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_skip_single_viewer_flood=2" in completed.stdout
    assert "recent_topic_skip_stale_recent_danmaku=1" in completed.stdout
    assert "recent_topic_skip_avatar_roast_context=1" in completed.stdout
    assert "recent_topic_skip_non_output_danmaku=1" in completed.stdout
    assert "recent_topic_skip_filtered_recent_danmaku=1" in completed.stdout
    assert "recent_topic_skip_filtered_direct_request=1" in completed.stdout
    assert "recent_topic_skip_filtered_reaction=1" in completed.stdout
    assert "recent_topic_skip_filtered_runtime_feedback=1" in completed.stdout
    assert "recent_topic_skip_viewer_to_viewer_mention=1" in completed.stdout
    assert "recent_topic_skip_recent_danmaku_source_streak=1" in completed.stdout
    assert "latest_topic_shape_guard_reason=recent_shape_streak" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    alert_parts = alerts_match.group(1).split(",")
    assert "topic_filter_direct_request" in alert_parts
    assert "topic_filter_reaction" in alert_parts
    assert "topic_filter_runtime_feedback" in alert_parts
    assert "topic_viewer_mention" in alert_parts
    assert "topic_source_streak" in alert_parts
    assert "topic_shape_guard" in alert_parts


def test_monitor_live_script_alerts_when_recent_result_failed(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "danmaku_response", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "failed", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_status=pushed" in completed.stdout
    assert "recent_failed=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "recent_failed" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_idle_is_ready_but_missing_recent_output(tmp_path: Path) -> None:
    context = _solo_idle_context()
    context["state"]["live_director_status"] = {
        "next_auto_action": "idle_hosting",
        "eligible": True,
        "reason": "solo_idle",
        "cooldown_remaining": 0.0,
    }

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "director_action=idle_hosting" in completed.stdout
    assert "director_eligible=True" in completed.stdout
    assert "recent_idle_hosting=0" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "idle_missing" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_idle_only_has_skipped_attempt(tmp_path: Path) -> None:
    context = _solo_idle_context()
    context["state"]["live_director_status"] = {
        "next_auto_action": "idle_hosting",
        "eligible": True,
        "reason": "solo_idle",
        "cooldown_remaining": 0.0,
    }
    context["state"]["recent_results"] = [
        {
            "status": "skipped",
            "response_module": "idle_hosting",
            "event": {"source": "idle_hosting", "host_beat_key": "idle:tiny-choice"},
        }
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_idle_hosting=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "idle_missing" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_warmup_is_ready_but_missing_recent_output(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["live_state"] = {
        "state": "warmup",
        "reason": "solo_stream_warmup",
        "warmup_hosting_candidate": True,
        "idle_hosting_candidate": False,
        "last_viewer_activity_age_sec": None,
        "last_output_age_sec": None,
    }
    context["state"]["live_director_status"] = {
        "next_auto_action": "warmup_hosting",
        "eligible": True,
        "reason": "solo_warmup",
        "cooldown_remaining": 0.0,
    }
    context["state"]["recent_results"] = [
        {
            "status": "skipped",
            "response_module": "warmup_hosting",
            "event": {"source": "warmup_hosting"},
        }
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "director_action=warmup_hosting" in completed.stdout
    assert "director_eligible=True" in completed.stdout
    assert "recent_warmup_hosting=1" in completed.stdout
    assert "recent_actual_warmup_hosting=0" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "warmup_missing" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_warmup_repeats(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "warmup_hosting",
            "event": {"source": "warmup_hosting"},
        },
        {
            "status": "dry_run",
            "response_module": "warmup_hosting",
            "event": {"source": "warmup_hosting"},
        },
        {
            "status": "skipped",
            "response_module": "warmup_hosting",
            "event": {"source": "warmup_hosting"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_warmup_hosting=3" in completed.stdout
    assert "recent_actual_warmup_hosting=2" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "warmup_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_latest_proactive_output_happens_in_engaged_room(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["live_state"]["state"] = "engaged"
    context["state"]["live_state"]["reason"] = "recent_activity"
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_key": "fallback:small-choice"},
        }
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "state=engaged" in completed.stdout
    assert "latest_route=active_engagement" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "proactive_in_engaged" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_is_ready_but_missing_recent_output(tmp_path: Path) -> None:
    context = _solo_quiet_context()
    context["state"]["active_engagement_status"] = {
        "eligible": True,
        "reason": "eligible",
        "minimum_interval_remaining": 0.0,
        "recent_danmaku_cooldown_remaining": 0.0,
        "idle_hosting_wait_remaining": 60.0,
    }
    context["state"]["live_director_status"] = {
        "next_auto_action": "active_engagement",
        "eligible": True,
        "reason": "solo_quiet",
        "cooldown_remaining": 0.0,
    }

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "director_action=active_engagement" in completed.stdout
    assert "director_eligible=True" in completed.stdout
    assert "recent_active_engagement=0" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "active_missing" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_only_has_skipped_attempt(tmp_path: Path) -> None:
    context = _solo_quiet_context()
    context["state"]["active_engagement_status"] = {
        "eligible": True,
        "reason": "eligible",
        "minimum_interval_remaining": 0.0,
        "recent_danmaku_cooldown_remaining": 0.0,
        "idle_hosting_wait_remaining": 60.0,
    }
    context["state"]["live_director_status"] = {
        "next_auto_action": "active_engagement",
        "eligible": True,
        "reason": "solo_quiet",
        "cooldown_remaining": 0.0,
    }
    context["state"]["recent_results"] = [
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_key": "fallback:tiny-confession"},
        }
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_active_engagement=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "active_missing" in alerts_match.group(1).split(",")


def test_monitor_live_script_focuses_active_engagement_when_director_says_active_is_ready(tmp_path: Path) -> None:
    context = _solo_quiet_context()
    context["state"]["active_engagement_status"] = {
        "eligible": True,
        "reason": "eligible",
        "minimum_interval_remaining": 0.0,
        "recent_danmaku_cooldown_remaining": 0.0,
        "idle_hosting_wait_remaining": 60.0,
    }
    context["state"]["live_director_status"] = {
        "next_auto_action": "active_engagement",
        "eligible": True,
        "reason": "solo_quiet",
        "cooldown_remaining": 0.0,
    }

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "director_action=active_engagement" in completed.stdout
    assert "solo_test_hint=expect_active_engagement" in completed.stdout
    assert "solo_test_focus=active_engagement" in completed.stdout


def test_monitor_live_script_alerts_when_idle_host_beat_repeats(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:soft_observation:quiet-room",
                "host_beat_shape": "soft_observation",
                "host_beat_title": "quiet room",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:soft_observation:quiet-room",
                "host_beat_shape": "soft_observation",
                "host_beat_title": "quiet room again",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_host_beat_key=idle:soft_observation:quiet-room" in completed.stdout
    assert "latest_host_beat_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "host_beat_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_ignores_skipped_idle_host_beat_for_repeat(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:soft_observation:quiet-room",
                "host_beat_shape": "soft_observation",
                "host_beat_title": "latest quiet room",
            },
        },
        {
            "status": "skipped",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:soft_observation:quiet-room",
                "host_beat_shape": "soft_observation",
                "host_beat_title": "skipped quiet room",
            },
        },
        {
            "status": "failed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:soft_observation:quiet-room",
                "host_beat_shape": "soft_observation",
                "host_beat_title": "failed quiet room",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_host_beat_key=idle:soft_observation:quiet-room" in completed.stdout
    assert "latest_host_beat_repeat=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "host_beat_repeat" not in alerts_match.group(1).split(",")


def test_monitor_live_script_ignores_skipped_latest_idle_host_beat_for_repeat(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "skipped",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:soft_observation:quiet-room",
                "host_beat_shape": "soft_observation",
                "host_beat_title": "latest skipped beat",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:soft_observation:quiet-room",
                "host_beat_shape": "soft_observation",
                "host_beat_title": "previous output beat",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_host_beat_key=idle:soft_observation:quiet-room" in completed.stdout
    assert "latest_host_beat_repeat=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "host_beat_repeat" not in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_backend_log_watchdog_and_contamination(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[voice] voice playback gate watchdog timeout",
                "[project-N-E-K-O-Warthunder] proactive bridge output queued",
                "[neko] send_lanlan_response len=123",
                "[neko] send_lanlan_response text=大家快来互动吧",
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
    assert "log_generic_host_prompt=True" in completed.stdout
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


def test_monitor_live_script_does_not_flag_generic_host_prompt_from_prompt_instructions(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[prompt] Do not say get the chat moving, keep the chat alive, or keep the chat going.",
                "[neko] send_lanlan_response len=18",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "log_generic_host_prompt=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "generic_host_prompt" not in alerts_match.group(1).split(",")


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
                "[neko] send_lanlan_response text=Let's get the chat moving.",
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
    assert "alerts=dry_run,test_isolation,latest_stale,playback_watchdog,long_reply,generic_host_prompt" in completed.stdout


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


def test_monitor_live_script_suggests_warmup_hosting_when_solo_stream_is_warming_up(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["live_state"] = {
        "state": "warmup",
        "reason": "solo_stream_warmup",
        "warmup_hosting_candidate": True,
        "idle_hosting_candidate": False,
        "last_viewer_activity_age_sec": None,
        "last_output_age_sec": None,
    }
    context["state"]["live_director_status"] = {
        "next_auto_action": "warmup_hosting",
        "eligible": True,
        "reason": "solo_warmup",
        "cooldown_remaining": 0.0,
    }
    context["state"]["solo_test_readiness"]["items"] = [
        {"id": "preflight", "status": "ready", "reason": "ready"},
        {"id": "test_isolation", "status": "ready", "reason": "clean"},
    ]
    context["state"]["recent_profiles"] = []

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "live_state=warmup" in completed.stdout
    assert "director_action=warmup_hosting" in completed.stdout
    assert "solo_test_hint=expect_warmup_hosting" in completed.stdout
    assert "solo_test_focus=warmup_hosting" in completed.stdout


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
