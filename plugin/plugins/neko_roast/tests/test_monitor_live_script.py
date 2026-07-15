from __future__ import annotations

import re
from pathlib import Path

import pytest

from plugin.plugins.neko_roast.tests.monitor_contexts import (
    _context_from_other_checkout,
    _context_with_latency,
    _context_with_latest_route_and_signal,
    _solo_quiet_context,
)
from plugin.plugins.neko_roast.tests.monitor_live_test_utils import _run_monitor, _run_monitor_args
from plugin.plugins.neko_roast.tools.live_random_danmaku_pressure import (
    HostedClient as RandomPressureHostedClient,
    build_test_config as build_random_pressure_config,
    parse_args as parse_random_pressure_args,
    require_action_success,
    run as run_random_pressure,
    submit_one,
)
from plugin.plugins.neko_roast.tools.live_silence_pressure import (
    compact_result as compact_silence_result,
    parse_args as parse_silence_pressure_args,
    require_action_success as require_silence_action_success,
    run as run_silence_pressure,
)
from plugin.plugins.neko_roast.tools.live_silence_pressure import summarize_context as summarize_silence_context
from plugin.plugins.neko_roast.tools.pressure_guard import prepare_log_path


def test_monitor_live_script_defaults_to_plugin_host_port() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "tools" / "monitor_live.ps1"

    assert '[string]$BaseUrl = "http://127.0.0.1:48916"' in script.read_text(encoding="utf-8")


def test_silence_pressure_summary_uses_director_next_auto_action() -> None:
    context = _solo_quiet_context()
    context["state"]["live_director_status"] = {
        "next_auto_action": "idle_hosting",
        "eligible": True,
        "reason": "solo_idle",
    }

    summary = summarize_silence_context(context)

    assert summary["director"] == {"action": "idle_hosting", "reason": "solo_idle"}


def test_pressure_logs_require_an_explicit_existing_file_mode(tmp_path: Path) -> None:
    log_path = tmp_path / "pressure.jsonl"
    log_path.write_text("keep\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--append or --overwrite"):
        prepare_log_path(log_path, append=False, overwrite=False)
    assert log_path.read_text(encoding="utf-8") == "keep\n"

    prepare_log_path(log_path, append=True, overwrite=False)
    assert log_path.read_text(encoding="utf-8") == "keep\n"

    prepare_log_path(log_path, append=False, overwrite=True)
    assert log_path.read_text(encoding="utf-8") == ""


def test_pressure_log_append_rejects_directory(tmp_path: Path) -> None:
    log_path = tmp_path / "pressure"
    log_path.mkdir()

    with pytest.raises(IsADirectoryError):
        prepare_log_path(log_path, append=True, overwrite=False)


def test_random_pressure_defaults_to_dry_run() -> None:
    default_args = parse_random_pressure_args([])
    real_output_args = parse_random_pressure_args(["--real-output"])

    assert default_args.real_output is False
    assert default_args.connect is False
    assert build_random_pressure_config(default_args)["dry_run"] is True
    assert real_output_args.real_output is True
    assert build_random_pressure_config(real_output_args)["dry_run"] is False


def test_random_pressure_rejects_failed_setup_action_envelope() -> None:
    with pytest.raises(RuntimeError, match="update_config failed: denied"):
        require_action_success(
            "update_config",
            {"result": {"success": False, "error": "denied"}},
        )


def test_silence_pressure_rejects_failed_setup_action_envelope() -> None:
    with pytest.raises(RuntimeError, match="update_config failed: denied"):
        require_silence_action_success(
            "update_config",
            {"result": {"success": False, "error": "denied"}},
        )


def test_silence_pressure_stops_before_triggers_when_safe_setup_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    actions: list[str] = []
    update_calls = 0

    class FakeClient:
        def __init__(self, _base_url: str) -> None:
            pass

        def context(self) -> dict:
            return {
                "state": {
                    "config": {"dry_run": False, "live_enabled": False},
                    "live_connection": {"connected": False},
                }
            }

        def action(self, action_id: str, args: dict | None = None) -> dict:
            nonlocal update_calls
            actions.append(action_id)
            if action_id == "update_config":
                update_calls += 1
                if update_calls == 1:
                    return {"result": {"success": False, "error": "denied"}}
            return {"result": {"success": True}}

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.tools.live_silence_pressure.HostedClient",
        FakeClient,
    )
    args = parse_silence_pressure_args(
        ["--cycles", "0", "--no-connect", "--log", str(tmp_path / "silence.jsonl")]
    )

    with pytest.raises(RuntimeError, match="update_config failed: denied"):
        run_silence_pressure(args)

    assert not {"trigger_warmup_hosting", "trigger_active_engagement", "trigger_idle_hosting"}.intersection(
        actions
    )


def test_random_pressure_refuses_to_replace_existing_room_listener(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    actions: list[tuple[str, dict]] = []
    current_room = "original-room"

    class FakeClient:
        def __init__(self, _base_url: str) -> None:
            pass

        def context(self) -> dict:
            return {
                "state": {
                    "config": {
                        "live_room_ref": "original-room",
                        "live_enabled": True,
                        "dry_run": False,
                    },
                    "live_connection": {
                        "connected": True,
                        "room_ref": current_room,
                    },
                }
            }

        def action(self, action_id: str, args: dict | None = None) -> dict:
            nonlocal current_room
            payload = args or {}
            actions.append((action_id, payload))
            if action_id == "connect_live_room":
                current_room = str(payload.get("room_id") or "")
            return {"result": {"success": True}}

    monkeypatch.setattr(
        "plugin.plugins.neko_roast.tools.live_random_danmaku_pressure.HostedClient",
        FakeClient,
    )
    args = parse_random_pressure_args(
        [
            "--events",
            "0",
            "--users",
            "1",
            "--concurrency",
            "1",
            "--room",
            "test-room",
            "--connect",
            "--log",
            str(tmp_path / "random.jsonl"),
        ]
    )

    with pytest.raises(RuntimeError, match="refusing to replace a listener"):
        run_random_pressure(args)
    assert ("disconnect_live_room", {}) not in actions
    assert current_room == "original-room"


def test_random_pressure_does_not_resubmit_after_run_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    create_calls = 0

    def create_entry_run(self, entry_id, args, *, timeout=45.0):
        nonlocal create_calls
        create_calls += 1
        return {"run_id": "run-accepted"}, "run-accepted"

    def collect_entry_run(self, created, run_id, *, timeout=45.0):
        raise RuntimeError("poll failed")

    monkeypatch.setattr(RandomPressureHostedClient, "create_entry_run", create_entry_run)
    monkeypatch.setattr(RandomPressureHostedClient, "collect_entry_run", collect_entry_run)

    result = submit_one("http://127.0.0.1:48916", 1, {"uid": "u1", "danmaku_text": "hello"})

    assert create_calls == 1
    assert result["type"] == "event_error"
    assert result["accepted"] is True
    assert result["run_id"] == "run-accepted"


def test_silence_pressure_compacts_nested_action_data() -> None:
    compact = compact_silence_result(
        "trigger_idle_hosting",
        {
            "result": {
                "success": True,
                "data": {
                    "accepted": True,
                    "status": "pushed",
                    "output": "short line",
                    "event": {"source": "idle_hosting", "trace_id": "tr_1"},
                },
            }
        },
    )

    assert compact["accepted"] is True
    assert compact["status"] == "pushed"
    assert compact["output"] == "short line"
    assert compact["route"] == "idle_hosting"
    assert compact["trace_id"] == "tr_1"


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
    assert "latest_reply_length_mode" in completed.stdout
    assert "latest_reply_target" in completed.stdout
    assert "latest_anchor_hint" in completed.stdout
    assert "latest_room_theme" in completed.stdout
    assert "latest_reply_shape_reason" in completed.stdout
    assert "pipeline_latency" in completed.stdout
    assert "dispatcher_latency" in completed.stdout
    assert "spoken_latency_estimate" in completed.stdout
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
    assert "latest_trace_id" in completed.stdout
    assert "timeline_stage_*" in completed.stdout
    assert "timeline_status_*" in completed.stdout
    assert "timeline_route_*" in completed.stdout
    assert "timeline_reason_*" in completed.stdout
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
    assert "pipeline_latency=3.0s" in completed.stdout
    assert "dispatcher_latency=0.1s" in completed.stdout
    assert "spoken_latency_estimate=-" in completed.stdout
    assert "latency=3.0s" in completed.stdout
    assert "last_result=pushed" in completed.stdout


def test_monitor_live_script_estimates_spoken_latency_from_timestamped_backend_log(tmp_path: Path) -> None:
    context = _context_with_latency(3000)
    context["state"]["recent_results"][0]["created_at"] = "2026-06-20T10:00:02.000000+00:00"
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "2026-06-20T10:00:04.500000Z [Lanlan] send_lanlan_response text=hello\n",
        encoding="utf-8",
    )

    completed = _run_monitor(tmp_path, context, "-BackendLogPath", str(log_path))

    assert completed.returncode == 0, completed.stderr
    assert "spoken_latency_estimate=2.5s" in completed.stdout


def test_monitor_live_script_reports_latest_route_and_signal(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"][0]["event"]["danmaku_text"] = "private-viewer-message"
    context["state"]["recent_results"][0]["danmaku_profile"] = "emoji_or_reaction"
    context["state"]["recent_results"][0]["danmaku_reply_shape"] = "mirror_mood_in_a_few_chars"
    context["state"]["recent_results"][0]["danmaku_reply_target"] = "current_reaction"
    context["state"]["recent_results"][0]["danmaku_anchor_hint"] = "鍝堝搱"
    context["state"]["recent_results"][0]["reply_length_mode"] = "room_bridge"
    context["state"]["recent_results"][0]["room_theme"] = "small talk"

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_status=pushed" in completed.stdout
    assert "latest_route=danmaku_response" in completed.stdout
    assert "latest_signal=gift_signal" in completed.stdout
    assert "latest_danmaku_profile=emoji_or_reaction" in completed.stdout
    assert "latest_danmaku_reply_shape=mirror_mood_in_a_few_chars" in completed.stdout
    assert "latest_reply_length_mode=room_bridge" in completed.stdout
    assert "latest_reply_target=current_reaction" in completed.stdout
    assert "latest_anchor_hint=[redacted]" in completed.stdout
    assert "鍝堝搱" not in completed.stdout
    assert "latest_room_theme=small_talk" in completed.stdout
    assert "latest_source=live_danmaku" in completed.stdout
    assert "latest_text=[redacted]" in completed.stdout
    assert "private-viewer-message" not in completed.stdout
    assert "latest_reason=dispatcher_pushed" in completed.stdout
    latest_age_match = re.search(r"latest_age=(\d+\.\d)s", completed.stdout)
    assert latest_age_match is not None
    assert 12.0 <= float(latest_age_match.group(1)) <= 20.0
    assert "latest_age_status=ok" in completed.stdout
    assert "alerts=-" in completed.stdout


def test_monitor_live_script_reports_reply_shape_reason_from_backend_log(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text(
        "[Lanlan] send_lanlan_response shape_reason=quality_fallback text_len=18\n",
        encoding="utf-8",
    )

    completed = _run_monitor(
        tmp_path,
        _context_with_latest_route_and_signal(),
        "-BackendLogPath",
        str(log_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "latest_reply_shape_reason=quality_fallback" in completed.stdout


def test_monitor_live_script_reports_runtime_timeline_without_sensitive_fields(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["live_explain"] = {
        "trace_id": "tr_monitor123",
        "timeline": [
            {
                "stage": "live_input.normalize",
                "status": "ok",
                "route": "danmaku",
                "reason": "normalized",
                "raw_payload": "must-not-print",
            },
            {
                "stage": "dispatcher.push",
                "status": "skipped",
                "route": "danmaku_response",
                "reason": "viewer said must-not-leak",
            },
        ],
    }
    context["state"]["recent_results"][0]["trace_id"] = "tr_old"
    context["state"]["recent_results"][0]["timeline"] = [
        {
            "stage": "old.stage",
            "status": "ok",
            "route": "old_route",
            "reason": "old_reason",
        }
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_trace_id=tr_monitor123" in completed.stdout
    assert "timeline_count=2" in completed.stdout
    assert "timeline_stage_1=live_input.normalize" in completed.stdout
    assert "timeline_status_1=ok" in completed.stdout
    assert "timeline_route_1=danmaku" in completed.stdout
    assert "timeline_reason_1=normalized" in completed.stdout
    assert "timeline_stage_2=dispatcher.push" in completed.stdout
    assert "timeline_status_2=skipped" in completed.stdout
    assert "timeline_route_2=danmaku_response" in completed.stdout
    assert "timeline_reason_2=[redacted]" in completed.stdout
    assert "must-not-print" not in completed.stdout
    assert "must-not-leak" not in completed.stdout
    assert "old.stage" not in completed.stdout


def test_monitor_live_script_uses_dash_for_missing_topic_and_host_fields(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = []

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_topic_pack=-" in completed.stdout
    assert "latest_host_beat_idle_stage=-" in completed.stdout


def test_monitor_live_script_reports_recent_event_signal_counts(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"] = [
        {
            "status": "pushed",
            "response_module": "danmaku_response",
            "event_signal": "gift_signal",
            "event": {"uid": "douyin:actual", "event_type": "gift", "gift_name": "rose", "gift_count": 1, "gift_value": 10},
        },
        {"status": "dry_run", "response_module": "danmaku_response", "event_signal": "super_chat_signal"},
        {"status": "pushed", "response_module": "danmaku_response", "event_signal": "danmaku_signal"},
        {
            "status": "skipped",
            "response_module": "gift_signal",
            "event_signal": "gift_signal",
            "event": {"uid": "douyin:skipped", "event_type": "gift", "gift_value": 5647},
        },
    ]

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "recent_signal_gift_signal=1" in completed.stdout
    assert "recent_signal_super_chat_signal=0" in completed.stdout
    assert "recent_signal_danmaku_signal=1" in completed.stdout
    assert "recent_observed_signal_gift_signal=2" in completed.stdout
    assert "recent_observed_signal_super_chat_signal=1" in completed.stdout
    assert "recent_observed_signal_danmaku_signal=1" in completed.stdout
    assert "recent_skipped_signal_gift_signal=1" in completed.stdout
    assert re.search(r"latest_gift_uid=viewer_[0-9a-f]{12}", completed.stdout)
    assert "douyin:actual" not in completed.stdout
    assert "latest_gift_name=rose" in completed.stdout
    assert "latest_gift_count=1" in completed.stdout
    assert "latest_gift_value=10" in completed.stdout


def test_monitor_live_script_reports_stale_latest_result_age(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(latest_age_seconds=90))

    assert completed.returncode == 0, completed.stderr
    assert "latest_age_status=warn" in completed.stdout


def test_monitor_live_script_reports_very_stale_latest_result_age(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(latest_age_seconds=240))

    assert completed.returncode == 0, completed.stderr
    assert "latest_age_status=stale" in completed.stdout


def test_monitor_live_script_alerts_when_real_output_test_is_not_isolated(tmp_path: Path) -> None:
    completed = _run_monitor(tmp_path, _context_with_latest_route_and_signal(), "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "test_isolation" in alerts_match.group(1).split(",")


def test_monitor_live_script_treats_receiving_as_connected(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["live_connection"] = {"state": "receiving", "connected": True}
    context["state"]["recent_profiles"] = []
    context["state"]["solo_test_readiness"]["profile_count"] = 0
    context["state"]["solo_test_readiness"]["items"] = [
        {"id": "preflight", "status": "ready", "reason": "ready"},
        {"id": "test_isolation", "status": "ready", "reason": "ready"},
    ]

    completed = _run_monitor(tmp_path, context, "-ExpectRealOutput")

    assert completed.returncode == 0, completed.stderr
    assert "live=receiving" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "live_disconnected" not in alerts_match.group(1).split(",")


@pytest.mark.parametrize(
    "dispatcher_ack",
    [
        "queued_to_neko(target=none, ai_behavior=respond, visibility=none, image_part_bytes=0)",
        "dry_run(target=none, ai_behavior=respond, visibility=none, image_part_bytes=0)",
        "skipped_to_neko(reason=non-deliverable request with enough detail to exceed the warning limit)",
    ],
)
def test_monitor_live_script_does_not_treat_dispatcher_ack_as_long_reply(
    tmp_path: Path,
    dispatcher_ack: str,
) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_results"][0]["output"] = dispatcher_ack

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "latest_output_length_status=ack" in completed.stdout
    assert "recent_long_reply_count=0" in completed.stdout
    alerts_match = re.search(r"\balerts=([^\s]+)", completed.stdout)
    assert alerts_match is not None
    assert "long_reply" not in alerts_match.group(1).split(",")


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


def test_monitor_live_script_uses_solo_readiness_profile_count_when_recent_profiles_are_empty(tmp_path: Path) -> None:
    context = _context_with_latest_route_and_signal()
    context["state"]["recent_profiles"] = []
    context["state"]["solo_test_readiness"]["profile_count"] = 5

    completed = _run_monitor(tmp_path, context)

    assert completed.returncode == 0, completed.stderr
    assert "profile_count=5" in completed.stdout


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
