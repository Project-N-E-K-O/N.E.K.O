from __future__ import annotations

import re
from pathlib import Path

from plugin.plugins.neko_live.tests.monitor_contexts import (
    _context_with_latest_route_and_signal,
)
from plugin.plugins.neko_live.tests.monitor_live_test_utils import _run_monitor



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
