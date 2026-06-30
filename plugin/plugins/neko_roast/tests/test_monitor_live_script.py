from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from plugin.plugins.neko_roast.tests.monitor_contexts import (
    _context_from_other_checkout,
    _context_with_latency,
    _context_with_latest_route_and_signal,
    _solo_idle_context,
    _solo_quiet_context,
)


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
    assert "log_reply_repeat" in completed.stdout
    assert "log_reply_suppressed" in completed.stdout
    assert "avatar_repeat_count" in completed.stdout
    assert "recent_total" in completed.stdout
    assert "recent_*" in completed.stdout
    assert "recent_actual_*" in completed.stdout
    assert "recent_pushed / recent_dry_run / recent_skipped / recent_failed" in completed.stdout
    assert "recent_topic_skip_*" in completed.stdout
    assert "recent_topic_source_*" in completed.stdout
    assert "recent_topic_shape_*" in completed.stdout
    assert "recent_topic_intent_*" in completed.stdout
    assert "latest_spent_output_family" in completed.stdout
    assert "recent_spent_output_family_*" in completed.stdout
    assert "spent_output_family_bias" in completed.stdout
    assert "Latest pushed NEKO output" in completed.stdout
    assert "dry_run/skipped results are ignored" in completed.stdout
    assert "Recent pushed spent-output family counts" in completed.stdout
    assert "topic_repeat / avatar_repeat" in completed.stdout
    assert "topic_filter_direct_request" in completed.stdout
    assert "topic_filter_reaction" in completed.stdout
    assert "topic_filter_runtime_feedback" in completed.stdout
    assert "topic_intent_bias" in completed.stdout
    assert "topic_source_bias" in completed.stdout
    assert "topic_shape_bias" in completed.stdout
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
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"][0]["danmaku_profile"] = "emoji_or_reaction"
    context["state"]["recent_results"][0]["danmaku_reply_shape"] = "mirror_mood_in_a_few_chars"

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_status=pushed" in completed.stdout
    assert "latest_route=danmaku_response" in completed.stdout
    assert "latest_signal=gift_signal" in completed.stdout
    assert "latest_danmaku_profile=emoji_or_reaction" in completed.stdout
    assert "latest_danmaku_reply_shape=mirror_mood_in_a_few_chars" in completed.stdout
    assert "latest_source=live_danmaku" in completed.stdout
    assert "latest_text=猫猫今天怎么这么安静" in completed.stdout
    assert "latest_reason=dispatcher_pushed" in completed.stdout
    latest_age_match = re.search(r"latest_age=(\d+\.\d)s", completed.stdout)
    assert latest_age_match is not None
    assert 12.0 <= float(latest_age_match.group(1)) <= 20.0
    assert "latest_age_status=ok" in completed.stdout
    assert "alerts=-" in completed.stdout


def test_monitor_live_script_reports_recent_event_signal_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "danmaku_response", "event_signal": "gift_signal"},
        {"status": "dry_run", "response_module": "danmaku_response", "event_signal": "super_chat_signal"},
        {"status": "pushed", "response_module": "danmaku_response", "event_signal": "danmaku_signal"},
        {"status": "skipped", "response_module": "danmaku_response", "event_signal": "gift_signal"},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_signal_gift_signal=1" in completed.stdout
    assert "recent_signal_super_chat_signal=1" in completed.stdout
    assert "recent_signal_danmaku_signal=1" in completed.stdout


def test_monitor_live_script_reports_stale_latest_result_age(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(latest_age_seconds=90))

    assert completed.returncode == 0, completed.stderr
    assert "latest_age_status=warn" in completed.stdout


def test_monitor_live_script_reports_very_stale_latest_result_age(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(latest_age_seconds=240))

    assert completed.returncode == 0, completed.stderr
    assert "latest_age_status=stale" in completed.stdout


def test_monitor_live_script_reports_pacing_and_active_topic_fields(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"][0]["spent_output_family"] = "reward,audience_prompt"
    completed = _run_monitor(tmp_path, context)

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
    assert "latest_topic_fun_axis=choice" in completed.stdout
    assert "latest_topic_family=choice_vote" in completed.stdout
    assert "latest_topic_pack=micro_poll" in completed.stdout
    assert "latest_topic_reply_affordance=viewer_can_answer_with_one_side" in completed.stdout
    assert "latest_topic_recent_skip_reason=single_viewer_flood" in completed.stdout
    assert "latest_topic_repeat=False" in completed.stdout
    assert "latest_host_beat_key=idle:soft_observation:quiet-room" in completed.stdout
    assert "latest_host_beat_shape=soft_observation" in completed.stdout
    assert "latest_host_beat_fun_axis=mood" in completed.stdout
    assert "latest_host_beat_family=room_mood" in completed.stdout
    assert "latest_host_beat_title=\u5b89\u9759\u7684\u76f4\u64ad\u95f4\u6c14\u6c1b" in completed.stdout
    assert "latest_host_beat_hint=Say_one_soft_concrete_observation." in completed.stdout
    assert "latest_host_beat_idle_stage=settle" in completed.stdout
    assert "latest_host_beat_reply_affordance=viewer_can_answer_with_one_mood_word" in completed.stdout
    assert "latest_spent_output_family=reward,audience_prompt" in completed.stdout
    assert "recent_spent_output_family_reward=1" in completed.stdout
    assert "recent_spent_output_family_audience_prompt=1" in completed.stdout
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


def test_monitor_live_script_ignores_latest_dry_run_spent_output_family(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"][0]["status"] = "dry_run"
    context["state"]["recent_results"][0]["reason"] = "dispatcher.dry_run"
    context["state"]["recent_results"][0]["spent_output_family"] = "reward,audience_prompt"

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_status=dry_run" in completed.stdout
    assert "latest_spent_output_family=-" in completed.stdout
    assert "recent_spent_output_family_reward=0" in completed.stdout
    assert "recent_spent_output_family_audience_prompt=0" in completed.stdout


def test_monitor_live_script_alerts_when_real_output_test_is_not_isolated(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "test_isolation" in alerts_match.group(1).split(",")


def test_monitor_live_script_does_not_alert_test_isolation_when_profiles_are_clean(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["live_connection"] = {"state": "disconnected", "connected": False}
    context["state"]["live_status"] = {"summary": "cannot_stream", "reason": "live_ingest_disconnected"}
    context["state"]["recent_profiles"] = []
    context["state"]["solo_test_readiness"] = {
        "summary": "live_not_ready",
        "profile_count": 0,
        "items": [
            {"id": "preflight", "status": "blocked", "reason": "live_not_ready"},
            {"id": "test_isolation", "status": "blocked", "reason": "live_not_ready"},
        ],
    }

    completed = _run_monitor(tmp_path, context, "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    assert "profile_count=0" in completed.stdout
    assert "test_isolation=blocked" in completed.stdout
    assert "test_isolation_reason=live_not_ready" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    alerts = alerts_match.group(1).split(",")
    assert "live_disconnected" in alerts
    assert "live_not_ready" in alerts
    assert "test_isolation" not in alerts


def test_monitor_live_script_alerts_when_active_topic_lacks_reply_hook(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    latest = context["state"]["recent_results"][0]
    latest["response_module"] = "active_engagement"
    latest["event"]["source"] = "active_engagement"
    latest["event"].pop("topic_reply_affordance", None)

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_reply_missing" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_idle_host_beat_lacks_reply_hook(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    latest = context["state"]["recent_results"][0]
    latest["response_module"] = "idle_hosting"
    latest["event"]["source"] = "idle_hosting"
    latest["event"].pop("host_beat_fun_axis", None)
    latest["event"].pop("host_beat_reply_affordance", None)

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "host_beat_reply_missing" in alerts_match.group(1).split(",")


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


def test_monitor_live_script_reports_active_topic_axis_mix(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:1",
                "topic_fun_axis": "choice",
            },
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:2",
                "topic_fun_axis": "tease",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:3",
                "topic_fun_axis": "viewer_callback",
            },
        },
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:4",
                "topic_fun_axis": "choice",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_axis_choice=1" in completed.stdout
    assert "recent_topic_axis_tease=1" in completed.stdout
    assert "recent_topic_axis_viewer_callback=1" in completed.stdout


def test_monitor_live_script_reports_idle_host_beat_axis_mix(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:1",
                "host_beat_fun_axis": "mood",
            },
        },
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:2",
                "host_beat_fun_axis": "choice",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:3",
                "host_beat_fun_axis": "viewer_callback",
            },
        },
        {
            "status": "skipped",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:4",
                "host_beat_fun_axis": "mood",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_host_beat_axis_mood=1" in completed.stdout
    assert "recent_host_beat_axis_choice=1" in completed.stdout
    assert "recent_host_beat_axis_viewer_callback=1" in completed.stdout


def test_monitor_live_script_reports_active_topic_family_mix(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:1",
                "topic_family": "choice_vote",
            },
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:2",
                "topic_family": "tease",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:3",
                "topic_family": "short_callback",
            },
        },
        {
            "status": "skipped",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:4",
                "topic_family": "choice_vote",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_family_choice_vote=1" in completed.stdout
    assert "recent_topic_family_tease=1" in completed.stdout
    assert "recent_topic_family_short_callback=1" in completed.stdout


def test_monitor_live_script_reports_idle_host_beat_family_mix(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:1",
                "host_beat_family": "room_mood",
            },
        },
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:2",
                "host_beat_family": "choice_vote",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:3",
                "host_beat_family": "short_callback",
            },
        },
        {
            "status": "skipped",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:4",
                "host_beat_family": "room_mood",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_host_beat_family_room_mood=1" in completed.stdout
    assert "recent_host_beat_family_choice_vote=1" in completed.stdout
    assert "recent_host_beat_family_short_callback=1" in completed.stdout


def test_monitor_live_script_alerts_when_idle_host_beat_axis_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:1",
                "host_beat_fun_axis": "mood",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:2",
                "host_beat_fun_axis": "mood",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:3",
                "host_beat_fun_axis": "mood",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:4",
                "host_beat_fun_axis": "choice",
                "host_beat_reply_affordance": "viewer can pick one side",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_host_beat_axis_mood=3" in completed.stdout
    assert "recent_host_beat_axis_choice=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "host_beat_axis_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_topic_axis_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_fun_axis": "mood", "topic_key": "topic:1"},
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_fun_axis": "mood", "topic_key": "topic:2"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_fun_axis": "mood", "topic_key": "topic:3"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_fun_axis": "choice", "topic_key": "topic:4"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_axis_mood=3" in completed.stdout
    assert "recent_topic_axis_choice=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_axis_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_topic_reply_affordance_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:1",
                "topic_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:2",
                "topic_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:3",
                "topic_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {
                "source": "active_engagement",
                "topic_key": "topic:4",
                "topic_reply_affordance": "viewer can pick one side",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_reply_affordance_top=viewer_can_answer_with_one_mood_word" in completed.stdout
    assert "recent_topic_reply_affordance_bias=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_reply_affordance_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_topic_family_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_family": "short_callback", "topic_key": "topic:1"},
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_family": "short_callback", "topic_key": "topic:2"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_family": "short_callback", "topic_key": "topic:3"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_family": "choice_vote", "topic_key": "topic:4"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_family_short_callback=3" in completed.stdout
    assert "recent_topic_family_choice_vote=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_family_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_idle_host_beat_family_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:1",
                "host_beat_family": "room_mood",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:2",
                "host_beat_family": "room_mood",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:3",
                "host_beat_family": "room_mood",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:4",
                "host_beat_family": "choice_vote",
                "host_beat_reply_affordance": "viewer can pick one side",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_host_beat_family_room_mood=3" in completed.stdout
    assert "recent_host_beat_family_choice_vote=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "host_beat_family_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_idle_host_beat_reply_affordance_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:1",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:2",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:3",
                "host_beat_reply_affordance": "viewer can answer with one mood word",
            },
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "event": {
                "source": "idle_hosting",
                "host_beat_key": "idle:4",
                "host_beat_reply_affordance": "viewer can pick one side",
            },
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_host_beat_reply_affordance_top=viewer_can_answer_with_one_mood_word" in completed.stdout
    assert "recent_host_beat_reply_affordance_bias=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "host_beat_reply_affordance_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_ignores_dry_run_spent_output_family(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "danmaku_response",
            "spent_output_family": "reward,audience_prompt",
            "event": {"source": "live_danmaku", "danmaku_text": "one"},
        },
        {
            "status": "dry_run",
            "response_module": "idle_hosting",
            "spent_output_family": "reward",
            "event": {"source": "idle_hosting"},
        },
        {
            "status": "pushed",
            "response_module": "danmaku_response",
            "spent_output_family": "reward",
            "event": {"source": "live_danmaku", "danmaku_text": "two"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "spent_output_family": "choice_vote",
            "event": {"source": "active_engagement"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_spent_output_family_reward=2" in completed.stdout
    assert "recent_spent_output_family_audience_prompt=1" in completed.stdout
    assert "recent_spent_output_family_choice_vote=1" in completed.stdout
    assert "recent_spent_output_family_bias=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "spent_output_family_bias" not in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_pushed_spent_output_family_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "danmaku_response",
            "spent_output_family": "reward,audience_prompt",
            "event": {"source": "live_danmaku", "danmaku_text": "one"},
        },
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "spent_output_family": "reward",
            "event": {"source": "idle_hosting"},
        },
        {
            "status": "pushed",
            "response_module": "danmaku_response",
            "spent_output_family": "reward",
            "event": {"source": "live_danmaku", "danmaku_text": "two"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "spent_output_family": "choice_vote",
            "event": {"source": "active_engagement"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_spent_output_family_reward=3" in completed.stdout
    assert "recent_spent_output_family_audience_prompt=1" in completed.stdout
    assert "recent_spent_output_family_choice_vote=1" in completed.stdout
    assert "recent_spent_output_family_bias=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "spent_output_family_bias" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_active_topic_shape_is_one_note(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_shape": "either_or", "topic_key": "topic:1"},
        },
        {
            "status": "dry_run",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_shape": "either_or", "topic_key": "topic:2"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_shape": "either_or", "topic_key": "topic:3"},
        },
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_shape": "light_stance", "topic_key": "topic:4"},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_topic_shape_either_or=3" in completed.stdout
    assert "recent_topic_shape_light_stance=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "topic_shape_bias" in alerts_match.group(1).split(",")


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


def test_monitor_live_script_warns_idle_hosting_before_global_long_reply_limit(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "idle_hosting",
            "output": "x" * 66,
            "event": {"source": "idle_hosting"},
        },
    ]

    completed = _run_monitor(tmp_path, context, "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    assert "recent_long_reply_count=1" in completed.stdout
    assert "recent_long_reply_idle_hosting=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "long_reply" in alerts_match.group(1).split(",")


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


def test_monitor_live_script_alerts_when_active_output_uses_presence_check_bait(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "output": "直播间还有人吗，猫猫探头",
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


def test_monitor_live_script_uses_solo_readiness_profile_count_when_recent_profiles_are_empty(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_profiles"] = []
    context["state"]["solo_test_readiness"]["profile_count"] = 5

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "profile_count=5" in completed.stdout


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
        {
            "status": "pushed",
            "response_module": "active_engagement",
            "event": {"source": "active_engagement", "topic_recent_skip_reason": "similar_topic_title"},
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
    assert "recent_topic_skip_similar_topic_title=1" in completed.stdout
    assert "latest_topic_shape_guard_reason=recent_shape_streak" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    alert_parts = alerts_match.group(1).split(",")
    assert "topic_filter_direct_request" in alert_parts
    assert "topic_filter_reaction" in alert_parts
    assert "topic_filter_runtime_feedback" in alert_parts
    assert "topic_viewer_mention" in alert_parts
    assert "topic_source_streak" in alert_parts
    assert "topic_similar_title" in alert_parts
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


def test_monitor_live_script_alerts_when_active_blocks_idle_window(tmp_path: Path) -> None:
    context = _solo_quiet_context()
    context["state"]["live_state"]["state"] = "idle"
    context["state"]["live_state"]["idle_hosting_candidate"] = True
    context["state"]["idle_hosting_status"] = {
        "eligible": True,
        "reason": "ready",
    }
    context["state"]["active_engagement_status"] = {
        "eligible": True,
        "reason": "eligible",
        "minimum_interval_remaining": 0.0,
        "recent_danmaku_cooldown_remaining": 0.0,
        "idle_hosting_wait_remaining": 0.0,
    }
    context["state"]["live_director_status"] = {
        "next_auto_action": "active_engagement",
        "eligible": True,
        "reason": "solo_quiet",
        "cooldown_remaining": 0.0,
    }

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "active_idle_wait=0.0s" in completed.stdout
    assert "director_action=active_engagement" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "active_blocks_idle" in alerts_match.group(1).split(",")


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


def test_monitor_live_script_does_not_flag_neko_roast_proactive_as_contamination(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[EventBus] proactive_message enqueued callback (passive); next user turn will carry it",
                "proactive bridge forwarded: plugin=neko_roast event=proactive_message",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "log_contamination=none" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "contamination_proactive" not in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_backend_log_presence_check_as_generic_host_prompt(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response len=15",
                "[neko] send_lanlan_response text=直播间还有人吗，猫猫探头",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "log_generic_host_prompt=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "generic_host_prompt" in alerts_match.group(1).split(",")


def test_monitor_live_script_reports_backend_log_reply_shape_reason(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response len=8",
                "[neko] send_lanlan_response shape_reason=quality_fallback+dangling_choice",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_shape_reason=quality_fallback+dangling_choice" in completed.stdout
    assert "log_reply_quality_fallback_count=1" in completed.stdout
    assert "log_reply_dangling_choice_count=1" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    alerts = alerts_match.group(1).split(",")
    assert "reply_quality_fallback" in alerts
    assert "reply_dangling_choice" in alerts


def test_monitor_live_script_reports_frequent_backend_log_reply_shape_reasons(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response shape_reason=quality_fallback",
                "[neko] send_lanlan_response shape_reason=dangling_choice+quality_fallback",
                "[neko] send_lanlan_response shape_reason=quality_fallback",
                "[neko] send_lanlan_response shape_reason=dangling_choice",
                "[neko] send_lanlan_response shape_reason=dangling_choice",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_quality_fallback_count=3" in completed.stdout
    assert "log_reply_dangling_choice_count=3" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    alerts = alerts_match.group(1).split(",")
    assert "reply_quality_fallback_many" in alerts
    assert "reply_dangling_choice_many" in alerts


def test_monitor_live_script_auto_detects_default_backend_log(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    default_log = root / ".codex-backend-live-test.log"
    default_log.unlink(missing_ok=True)
    default_log.write_text("[voice] voice playback gate watchdog timeout\n", encoding="utf-8")
    try:
        completed = _run_monitor(
            tmp_path,
            _context_with_latest_route_and_signal(),
            use_default_backend_log=True,
        )
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


def test_monitor_live_script_alerts_when_backend_log_repeats_live_reply(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=猫猫先蹲一下",
                "[neko] send_lanlan_response text=猫猫先蹲一下！",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_repeats_non_adjacent_live_reply(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=cat says tiny plan",
                "[neko] send_lanlan_response text=fresh different angle",
                "[neko] send_lanlan_response text=cat says tiny plan!",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_paraphrases_live_reply(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=catpawscheckthemoon",
                "[neko] send_lanlan_response text=fresh different angle",
                "[neko] send_lanlan_response text=moonchecksthecatpaw",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_repeats_host_beat_with_changed_words(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=小鱼干奖励先记账，等弹幕接一句",
                "[neko] send_lanlan_response text=这题换个爪子答",
                "[neko] send_lanlan_response text=给你们备了鱼干小奖励，谁先发弹幕",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_repeats_audience_prompt_with_short_callback(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=觉得猫猫还能抢救一下的扣个1",
                "[neko] send_lanlan_response text=这题换个爪子答",
                "[neko] send_lanlan_response text=还在的观众吱一声，给猫猫一点反应",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_repeats_presence_check_prompt(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=直播间还有人吗，猫猫探头",
                "[neko] send_lanlan_response text=这题换个爪子答",
                "[neko] send_lanlan_response text=在不在，猫猫确认一下信号",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_repeats_reward_bit_with_low_overlap(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=小鱼干先记账",
                "[neko] send_lanlan_response text=这题换个爪子等",
                "[neko] send_lanlan_response text=奖励小本本又打开了",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_repeats_host_score_bit(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=猫猫主播力先满格三秒",
                "[neko] send_lanlan_response text=这题换个爪子等",
                "[neko] send_lanlan_response text=正经主持挑战开始，别笑",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_marks_live_reply_repeat(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "[warning] NEKO Live repeated reply detected module=idle_hosting len=12 window=4\n",
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_suppresses_live_reply_repeat(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "[warning] NEKO Live repeated reply suppressed module=idle_hosting len=12 window=4\n",
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    assert "log_reply_suppressed=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")
    assert "reply_suppressed" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_has_repeat_metadata(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "[neko] send_lanlan_response metadata neko_live_reply_repeat=true\n",
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_alerts_when_backend_log_repeats_within_wider_window(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=cat says tiny plan",
                "[neko] send_lanlan_response text=fresh angle 1",
                "[neko] send_lanlan_response text=fresh angle 2",
                "[neko] send_lanlan_response text=fresh angle 3",
                "[neko] send_lanlan_response text=fresh angle 4",
                "[neko] send_lanlan_response text=fresh angle 5",
                "[neko] send_lanlan_response text=fresh angle 6",
                "[neko] send_lanlan_response text=fresh angle 7",
                "[neko] send_lanlan_response text=fresh angle 8",
                "[neko] send_lanlan_response text=cat says tiny plan!",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=True" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" in alerts_match.group(1).split(",")


def test_monitor_live_script_does_not_flag_distinct_backend_live_replies(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "\n".join(
            [
                "[neko] send_lanlan_response text=猫猫先蹲一下",
                "[neko] send_lanlan_response text=这题换个爪子答",
            ]
        ),
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "log_reply_repeat=False" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "reply_repeat" not in alerts_match.group(1).split(",")


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


def test_monitor_live_script_alerts_when_live_plugin_is_disabled_for_real_output(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text("", encoding="utf-8")
    context = _context_with_latest_route_and_signal()
    context["state"]["live_status"] = {"summary": "cannot_stream", "reason": "live_disabled"}

    completed = _run_monitor(
        tmp_path,
        context,
        "-ExpectRealOutput",
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "live_disabled" in alerts_match.group(1).split(",")


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
