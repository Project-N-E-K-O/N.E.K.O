from __future__ import annotations

import re
from pathlib import Path

from plugin.plugins.neko_roast.tests.monitor_contexts import (
    _context_from_other_checkout,
    _context_with_latency,
    _context_with_latest_route_and_signal,
    _solo_idle_context,
    _solo_quiet_context,
)
from plugin.plugins.neko_roast.tests.monitor_live_test_utils import _run_monitor, _run_monitor_args



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
            "status": "pushed",
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


def test_monitor_live_script_does_not_count_dry_run_as_actual_warmup(tmp_path: Path) -> None:
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
            "status": "dry_run",
            "response_module": "warmup_hosting",
            "event": {"source": "warmup_hosting"},
        }
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_warmup_hosting=1" in completed.stdout
    assert "recent_actual_warmup_hosting=0" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "warmup_missing" in alerts_match.group(1).split(",")
    assert "warmup_repeat" not in alerts_match.group(1).split(",")



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
