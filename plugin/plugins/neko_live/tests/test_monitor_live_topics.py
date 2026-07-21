from __future__ import annotations

import re
from pathlib import Path

from plugin.plugins.neko_live.tests.monitor_contexts import (
    _context_with_latest_route_and_signal,
)
from plugin.plugins.neko_live.tests.monitor_live_test_utils import _run_monitor



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


def test_monitor_live_script_redacts_recent_danmaku_topic_material(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    latest = context["state"]["recent_results"][0]
    latest["event"]["topic_source"] = "recent_danmaku"
    latest["event"]["topic_title"] = "private-viewer-topic"
    latest["event"]["topic_key"] = "danmaku:private-viewer-topic"
    latest["event"]["topic_hook"] = "Make 'private-viewer-topic' into a prompt."

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_topic_source=recent_danmaku" in completed.stdout
    assert "latest_topic_title=[redacted]" in completed.stdout
    assert "latest_topic_key=[redacted]" in completed.stdout
    assert "latest_topic_hook=[redacted]" in completed.stdout
    assert "private-viewer-topic" not in completed.stdout



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
    latest_uid = re.search(r"latest_uid=(viewer_[0-9a-f]{12})", completed.stdout)
    avatar_uid = re.search(r"avatar_repeat_uid=(viewer_[0-9a-f]{12})", completed.stdout)
    assert latest_uid is not None
    assert avatar_uid is not None
    assert latest_uid.group(1) == avatar_uid.group(1)
    assert "latest_uid=42" not in completed.stdout
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



def test_monitor_live_script_reports_recent_route_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "danmaku_response", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "live_support_events", "event": {"uid": "2", "source": "live_danmaku"}},
        {"status": "pushed", "response_module": "idle_hosting", "event": {"uid": "__neko_idle__", "source": "idle_hosting"}},
        {"status": "pushed", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
        {"status": "pushed", "response_module": "warmup_hosting", "event": {"uid": "__neko_warmup__", "source": "warmup_hosting"}},
        {"status": "skipped", "response_module": "active_engagement", "event": {"uid": "__neko_active__", "source": "active_engagement"}},
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_avatar_roast=1" in completed.stdout
    assert "recent_danmaku_response=1" in completed.stdout
    assert "recent_live_support_events=1" in completed.stdout
    assert "recent_idle_hosting=1" in completed.stdout
    assert "recent_active_engagement=2" in completed.stdout
    assert "recent_warmup_hosting=1" in completed.stdout



def test_monitor_live_script_reports_recent_actual_route_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {"status": "pushed", "response_module": "avatar_roast", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "dry_run", "response_module": "danmaku_response", "event": {"uid": "1", "source": "live_danmaku"}},
        {"status": "dry_run", "response_module": "live_support_events", "event": {"uid": "2", "source": "live_danmaku"}},
        {"status": "failed", "response_module": "live_support_events", "event": {"uid": "3", "source": "live_danmaku"}},
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
    assert "recent_live_support_events=2" in completed.stdout
    assert "recent_idle_hosting=1" in completed.stdout
    assert "recent_active_engagement=2" in completed.stdout
    assert "recent_warmup_hosting=2" in completed.stdout
    assert "recent_actual_avatar_roast=1" in completed.stdout
    assert "recent_actual_danmaku_response=0" in completed.stdout
    assert "recent_actual_live_support_events=0" in completed.stdout
    assert "recent_actual_idle_hosting=0" in completed.stdout
    assert "recent_actual_active_engagement=1" in completed.stdout
    assert "recent_actual_warmup_hosting=0" in completed.stdout



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
