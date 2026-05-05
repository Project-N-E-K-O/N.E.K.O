from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest

from plugin.plugins.galgame_plugin import service as galgame_service
from plugin.plugins.galgame_plugin.models import (
    DATA_SOURCE_BRIDGE_SDK,
    DATA_SOURCE_MEMORY_READER,
    DATA_SOURCE_OCR_READER,
    OCR_TRIGGER_MODE_AFTER_ADVANCE,
    OCR_TRIGGER_MODE_INTERVAL,
    SessionCandidate,
)
from plugin.plugins.galgame_plugin.service import (
    build_explain_context,
    build_summarize_context,
    choose_candidate,
    filter_ocr_reader_candidates,
)


pytestmark = pytest.mark.plugin_unit


def _local_state() -> dict[str, object]:
    return {
        "active_game_id": "game.demo",
        "active_session_id": "session-demo",
        "active_data_source": DATA_SOURCE_OCR_READER,
        "latest_snapshot": {
            "speaker": "",
            "text": "OCR 目标窗口：等待截图",
            "line_id": "",
            "scene_id": "ocr:game:scene-0001",
            "route_id": "ocr",
            "choices": [],
            "is_menu_open": False,
        },
        "history_lines": [
            {
                "speaker": "雪乃",
                "text": "今天先回去吧。",
                "line_id": "ocr:line-stable",
                "scene_id": "ocr:game:scene-0001",
                "route_id": "ocr",
                "stability": "stable",
            }
        ],
        "history_observed_lines": [
            {
                "speaker": "",
                "text": "OCR 目标窗口：等待截图",
                "line_id": "ocr:diagnostic",
                "scene_id": "ocr:game:scene-0001",
                "route_id": "ocr",
                "stability": "tentative",
            },
            {
                "speaker": "",
                "text": "她轻声说：走吧。",
                "line_id": "ocr:line-observed",
                "scene_id": "ocr:game:scene-0001",
                "route_id": "ocr",
                "stability": "tentative",
            },
        ],
        "history_choices": [],
    }


def _patch_status_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(galgame_service, "inspect_dxcam_installation", lambda: {})
    monkeypatch.setattr(galgame_service, "inspect_textractor_installation", lambda **kwargs: {})
    monkeypatch.setattr(galgame_service, "inspect_rapidocr_installation", lambda **kwargs: {})
    monkeypatch.setattr(galgame_service, "inspect_tesseract_installation", lambda **kwargs: {})
    monkeypatch.setattr(galgame_service, "_current_process_performance", lambda: {})


def test_build_config_keeps_string_memory_reader_hook_code_whole(tmp_path) -> None:
    cfg = galgame_service.build_config(
        {
            "galgame": {"bridge_root": str(tmp_path / "bridge")},
            "memory_reader": {"hook_codes": "/HSN-4@1234"},
        }
    )

    assert cfg.memory_reader_hook_codes == ["/HSN-4@1234"]


def test_build_config_keeps_memory_reader_engine_hooks(tmp_path) -> None:
    cfg = galgame_service.build_config(
        {
            "galgame": {"bridge_root": str(tmp_path / "bridge")},
            "memory_reader": {
                "hook_codes": "/legacy@1234",
                "engine_hooks": {
                    "unity": ["/unity@1234"],
                    "kirikiri": [],
                    "renpy": "/renpy@1234",
                },
            },
        }
    )

    assert cfg.memory_reader_hook_codes == ["/legacy@1234"]
    assert cfg.memory_reader_engine_hook_codes == {
        "unity": ["/unity@1234"],
        "kirikiri": [],
        "renpy": ["/renpy@1234"],
    }


def test_build_config_defaults_ocr_trigger_mode_to_after_advance() -> None:
    cfg = galgame_service.build_config({})

    assert cfg.ocr_reader_trigger_mode == OCR_TRIGGER_MODE_AFTER_ADVANCE


def test_build_config_reads_auto_open_ui_flag() -> None:
    default_cfg = galgame_service.build_config({})
    enabled_cfg = galgame_service.build_config({"galgame": {"auto_open_ui": True}})
    invalid_cfg = galgame_service.build_config({"galgame": {"auto_open_ui": "true"}})

    assert default_cfg.auto_open_ui is False
    assert enabled_cfg.auto_open_ui is True
    assert invalid_cfg.auto_open_ui is False


def test_build_config_defaults_fast_loop_enabled() -> None:
    default_cfg = galgame_service.build_config({})
    disabled_cfg = galgame_service.build_config({"ocr_reader": {"fast_loop_enabled": False}})
    invalid_cfg = galgame_service.build_config({"ocr_reader": {"fast_loop_enabled": "false"}})

    assert default_cfg.ocr_reader_fast_loop_enabled is True
    assert disabled_cfg.ocr_reader_fast_loop_enabled is False
    assert invalid_cfg.ocr_reader_fast_loop_enabled is True


def test_build_config_defaults_ocr_poll_interval_to_fast_capture() -> None:
    cfg = galgame_service.build_config({})

    assert cfg.ocr_reader_poll_interval_seconds == 0.5


def _candidate(
    tmp_path,
    *,
    game_id: str,
    data_source: str,
    text: str = "",
    choices: list[dict[str, object]] | None = None,
    last_seq: int = 1,
) -> SessionCandidate:
    session_path = tmp_path / game_id / "session.json"
    events_path = tmp_path / game_id / "events.jsonl"
    if data_source == DATA_SOURCE_OCR_READER and text:
        events_path.parent.mkdir(parents=True, exist_ok=True)
        events_path.write_text(
            json.dumps(
                {
                    "seq": last_seq,
                    "type": "line_changed",
                    "payload": {"text": text, "stability": "stable"},
                },
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return SessionCandidate(
        game_id=game_id,
        session_path=session_path,
        events_path=events_path,
        data_source=data_source,
        session={
            "session_id": f"session-{game_id}",
            "started_at": "2026-04-29T00:00:00Z",
            "last_seq": last_seq,
            "state": {
                "text": text,
                "choices": list(choices or []),
                "ts": "2026-04-29T00:00:01Z",
            },
        },
    )


def _status_state(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "current_connection_state": "active",
        "mode": "companion",
        "push_notifications": True,
        "advance_speed": "medium",
        "bound_game_id": "demo",
        "active_game_id": "demo",
        "available_game_ids": ["demo"],
        "active_session_id": "session-1",
        "active_data_source": DATA_SOURCE_OCR_READER,
        "stream_reset_pending": False,
        "last_seq": 1,
        "last_error": {},
        "memory_reader_runtime": {},
        "ocr_reader_runtime": {"status": "running"},
        "ocr_capture_profiles": {},
        "latest_snapshot": {
            "speaker": "雪乃",
            "text": "今天先回去吧。",
            "line_id": "line-1",
            "scene_id": "scene-1",
            "route_id": "ocr",
            "choices": [],
            "is_menu_open": False,
        },
        "history_observed_lines": [],
        "history_lines": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_service_summarize_context_filters_overlay_diagnostics() -> None:
    context = build_summarize_context(_local_state(), scene_id="ocr:game:scene-0001")

    texts = [item["text"] for item in context["recent_lines"]]

    assert "OCR 目标窗口：等待截图" not in texts
    assert "今天先回去吧。" in texts
    assert "她轻声说：走吧。" in texts
    assert context["input_degraded"] is True
    assert "ocr_reader_source" in context["degraded_reasons"]


def test_service_summarize_context_seed_keeps_observed_lines_tentative() -> None:
    state = _local_state()
    state["history_lines"] = []
    state["latest_snapshot"] = {
        "speaker": "雪乃",
        "text": "也许我并不讨厌这样。",
        "line_id": "ocr:line-observed",
        "scene_id": "ocr:game:scene-0001",
        "route_id": "ocr",
        "stability": "tentative",
        "choices": [],
        "is_menu_open": False,
    }
    state["history_observed_lines"] = [
        {
            "speaker": "雪乃",
            "text": "也许我并不讨厌这样。",
            "line_id": "ocr:line-observed",
            "scene_id": "ocr:game:scene-0001",
            "route_id": "ocr",
            "stability": "tentative",
        }
    ]

    context = build_summarize_context(state, scene_id="ocr:game:scene-0001")

    assert context["stable_lines"] == []
    assert context["observed_lines"][0]["text"] == "也许我并不讨厌这样。"
    assert "也许我并不讨厌这样。" not in context["scene_summary_seed"]
    assert "暂时没有足够台词上下文" in context["scene_summary_seed"]


def test_untrusted_ocr_candidate_is_rejected_before_history_lines_update() -> None:
    config = galgame_service.build_config({})
    snapshot = {
        "speaker": "",
        "text": "previous trusted line",
        "line_id": "line-old",
        "scene_id": "scene-a",
        "route_id": "",
    }
    event = {
        "seq": 1,
        "ts": "2026-05-01T03:20:00Z",
        "type": "line_changed",
        "session_id": "ocr-session",
        "game_id": "ocr-game",
        "payload": {
            "speaker": "",
            "text": "# Galgame Agent 会话重置",
            "line_id": "line-bad",
            "scene_id": "scene-a",
            "route_id": "ocr",
            "ocr_capture_content_trusted": False,
            "ocr_capture_rejected_reason": "self_ui_guard",
        },
    }

    histories = galgame_service.rebuild_histories_from_events(
        events=[event],
        snapshot=snapshot,
        dedupe_window=[],
        config=config,
        game_id="ocr-game",
    )

    history_events, history_lines, history_observed_lines, _choices, _dedupe, updated = histories
    assert history_events == []
    assert history_lines == []
    assert history_observed_lines == []
    assert updated["text"] == "previous trusted line"


def _rebuild_single_event(event: dict[str, object]) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, str]],
    dict[str, object],
]:
    return galgame_service.rebuild_histories_from_events(
        events=[event],
        snapshot={
            "speaker": "",
            "text": "previous trusted line",
            "line_id": "line-old",
            "scene_id": "scene-a",
            "route_id": "ocr",
        },
        dedupe_window=[],
        config=galgame_service.build_config({}),
        game_id="ocr-game",
    )


def test_payload_text_length_cap_allows_multilingual_text_and_unknown_fields() -> None:
    histories = _rebuild_single_event(
        {
            "seq": 2,
            "ts": "2026-05-01T03:21:00Z",
            "type": "line_changed",
            "session_id": "ocr-session",
            "game_id": "ocr-game",
            "payload": {
                "speaker": "雪乃",
                "text": "あいう😊\n次です。",
                "line_id": "line-ok",
                "scene_id": "scene-a",
                "route_id": "ocr",
                "stability": "stable",
                "unknown_field": "x" * 10000,
            },
        }
    )

    history_events, history_lines, history_observed_lines, _choices, _dedupe, updated = histories
    assert len(history_events) == 1
    assert len(history_lines) == 1
    assert len(history_observed_lines) == 1
    assert history_lines[0]["text"] == "あいう😊\n次です。"
    assert updated["text"] == "あいう😊\n次です。"


@pytest.mark.parametrize(
    ("field", "value", "event_type"),
    [
        ("text", "長" * 5001, "line_changed"),
        ("speaker", "名" * 257, "line_changed"),
        ("stability", "stable" * 13, "line_changed"),
        ("choice_text", "選択肢" * 1667, "choice_selected"),
    ],
)
def test_payload_text_length_cap_rejects_oversized_fields(
    caplog: pytest.LogCaptureFixture,
    field: str,
    value: str,
    event_type: str,
) -> None:
    payload: dict[str, object] = {
        "speaker": "雪乃",
        "text": "今日はいい天気です。",
        "line_id": "line-too-long",
        "scene_id": "scene-a",
        "route_id": "ocr",
        "stability": "stable",
        "choice_id": "choice-a",
        "choice_text": "右へ行く",
        "choice_index": 0,
    }
    payload[field] = value

    with caplog.at_level(logging.WARNING, logger=galgame_service._logger.name):
        histories = _rebuild_single_event(
            {
                "seq": 3,
                "ts": "2026-05-01T03:22:00Z",
                "type": event_type,
                "session_id": "ocr-session",
                "game_id": "ocr-game",
                "payload": payload,
            }
        )

    history_events, history_lines, history_observed_lines, history_choices, _dedupe, updated = histories
    assert history_events == []
    assert history_lines == []
    assert history_observed_lines == []
    assert history_choices == []
    assert updated["text"] == "previous trusted line"
    assert f"field={field}" in caplog.text
    assert "payload field exceeded length limit" in caplog.text


def test_service_explain_context_uses_history_when_snapshot_is_diagnostic() -> None:
    context = build_explain_context(_local_state(), line_id="ocr:line-stable")

    assert context["speaker"] == "雪乃"
    assert context["text"] == "今天先回去吧。"
    assert context["evidence"]
    assert context["input_degraded"] is True


def test_choose_candidate_auto_prefers_bridge_text_then_memory_text(tmp_path) -> None:
    bridge = _candidate(
        tmp_path,
        game_id="bridge",
        data_source=DATA_SOURCE_BRIDGE_SDK,
        text="stable bridge text",
        last_seq=1,
    )
    memory = _candidate(
        tmp_path,
        game_id="memory",
        data_source=DATA_SOURCE_MEMORY_READER,
        text="memory reader text",
        last_seq=100,
    )

    assert choose_candidate(
        {"bridge": bridge, "memory": memory},
        bound_game_id="",
        current_game_id="",
        keep_current=False,
    ) is bridge

    empty_bridge = _candidate(
        tmp_path,
        game_id="empty-bridge",
        data_source=DATA_SOURCE_BRIDGE_SDK,
        text="",
        last_seq=200,
    )
    assert choose_candidate(
        {"empty-bridge": empty_bridge, "memory": memory},
        bound_game_id="",
        current_game_id="",
        keep_current=False,
    ) is memory


def test_choose_candidate_prefers_newer_session_time_over_cross_session_seq(tmp_path) -> None:
    old_high_seq = _candidate(
        tmp_path,
        game_id="old-ocr",
        data_source=DATA_SOURCE_OCR_READER,
        text="old ocr text",
        last_seq=512,
    )
    old_high_seq.session["started_at"] = "2026-04-24T00:00:00Z"
    old_high_seq.session["state"]["ts"] = "2026-04-24T05:08:34Z"
    new_low_seq = _candidate(
        tmp_path,
        game_id="new-ocr",
        data_source=DATA_SOURCE_OCR_READER,
        text="new ocr text",
        last_seq=70,
    )
    new_low_seq.session["started_at"] = "2026-04-30T04:33:02Z"
    new_low_seq.session["state"]["ts"] = "2026-04-30T04:36:47Z"

    assert choose_candidate(
        {"old-ocr": old_high_seq, "new-ocr": new_low_seq},
        bound_game_id="",
        current_game_id="",
        keep_current=False,
    ) is new_low_seq


def test_filter_ocr_reader_keeps_context_session_when_window_temporarily_invalid(tmp_path) -> None:
    ocr = _candidate(
        tmp_path,
        game_id="ocr-game",
        data_source=DATA_SOURCE_OCR_READER,
        text="stable ocr line",
        last_seq=17,
    )
    memory = _candidate(
        tmp_path,
        game_id="memory-game",
        data_source=DATA_SOURCE_MEMORY_READER,
        text="",
        last_seq=1,
    )

    available, candidates = filter_ocr_reader_candidates(
        ["ocr-game", "memory-game"],
        {"ocr-game": ocr, "memory-game": memory},
        runtime={
            "status": "idle",
            "detail": "waiting_for_valid_window",
            "game_id": "ocr-game",
            "session_id": "session-ocr-game",
            "last_stable_line": {
                "text": "stable ocr line",
                "scene_id": "ocr:ocr-game:scene-0001",
            },
        },
    )

    assert available == ["ocr-game", "memory-game"]
    assert candidates["ocr-game"] is ocr
    assert candidates["memory-game"] is memory


def test_filter_ocr_reader_keeps_text_session_after_runtime_context_reset(tmp_path) -> None:
    ocr = _candidate(
        tmp_path,
        game_id="ocr-game",
        data_source=DATA_SOURCE_OCR_READER,
        text="stable ocr line",
        last_seq=17,
    )
    empty_ocr = _candidate(
        tmp_path,
        game_id="empty-ocr-game",
        data_source=DATA_SOURCE_OCR_READER,
        text="",
        last_seq=18,
    )

    available, candidates = filter_ocr_reader_candidates(
        ["ocr-game", "empty-ocr-game"],
        {"ocr-game": ocr, "empty-ocr-game": empty_ocr},
        runtime={
            "status": "idle",
            "detail": "waiting_for_valid_window",
            "game_id": "",
            "session_id": "",
        },
    )

    assert available == ["ocr-game"]
    assert candidates == {"ocr-game": ocr}


def test_filter_ocr_reader_drops_observed_only_session(tmp_path) -> None:
    observed_only = _candidate(
        tmp_path,
        game_id="observed-only",
        data_source=DATA_SOURCE_OCR_READER,
        text="tentative ocr line",
        last_seq=9,
    )
    observed_only.events_path.write_text(
        json.dumps(
            {
                "seq": 9,
                "type": "line_observed",
                "payload": {"text": "tentative ocr line", "stability": "tentative"},
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    available, candidates = filter_ocr_reader_candidates(
        ["observed-only"],
        {"observed-only": observed_only},
        runtime={
            "status": "idle",
            "detail": "waiting_for_valid_window",
            "game_id": "",
            "session_id": "",
        },
    )

    assert available == []
    assert candidates == {}


def test_status_payload_snapshot_fast_path_skips_json_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    config = galgame_service.build_config({})
    state = _status_state()
    _patch_status_dependencies(monkeypatch)

    def _unexpected_json_copy(value: object) -> object:
        raise AssertionError(f"json_copy should not be called for snapshot input: {value!r}")

    monkeypatch.setattr(galgame_service, "json_copy", _unexpected_json_copy)

    payload = galgame_service.build_status_payload(
        state,
        config=config,
        state_is_snapshot=True,
    )

    assert payload["effective_current_line"]["text"] == "今天先回去吧。"
    assert payload["ocr_reader_runtime"] == {"status": "running"}
    assert payload["primary_diagnosis"]["title"]


def test_status_payload_exposes_ocr_decision_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = galgame_service.build_config({})
    state = _status_state(
        ocr_reader_runtime={
            "status": "active",
            "ocr_tick_allowed": False,
            "ocr_tick_block_reason": "trigger_mode_after_advance_waiting_for_input",
            "ocr_emit_block_reason": "",
            "ocr_reader_allowed": True,
            "ocr_reader_allowed_block_reason": "",
            "ocr_trigger_mode_effective": "after_advance",
            "ocr_waiting_for_advance": True,
            "ocr_waiting_for_advance_reason": "trigger_mode_after_advance_waiting_for_input",
            "ocr_last_tick_decision_at": "2026-04-29T00:00:00Z",
            "display_source_not_ocr_reason": "",
            "ocr_tick_gate_allowed": False,
            "ocr_reader_manager_available": True,
            "ocr_tick_skipped_reason": "tick_gate_closed",
            "pending_ocr_advance_capture": True,
            "pending_manual_foreground_ocr_capture": True,
            "pending_ocr_delay_remaining": 0.12,
            "pending_ocr_advance_capture_age_seconds": 1.25,
            "pending_ocr_advance_reason": "manual_foreground_advance",
            "pending_ocr_advance_clear_reason": "",
            "ocr_bootstrap_capture_needed": False,
            "after_advance_screen_refresh_tick_needed": True,
            "companion_after_advance_ocr_refresh_tick_needed": False,
            "ocr_runtime_status": "active",
            "foreground_refresh_attempted": False,
            "foreground_refresh_skipped_reason": "ocr_reader_not_allowed",
        },
    )
    _patch_status_dependencies(monkeypatch)

    payload = galgame_service.build_status_payload(
        state,
        config=config,
        state_is_snapshot=True,
    )

    assert payload["ocr_tick_allowed"] is False
    assert payload["ocr_tick_block_reason"] == "trigger_mode_after_advance_waiting_for_input"
    assert payload["ocr_emit_block_reason"] == ""
    assert payload["ocr_reader_allowed"] is True
    assert payload["ocr_reader_allowed_block_reason"] == ""
    assert payload["ocr_trigger_mode_effective"] == "after_advance"
    assert payload["ocr_waiting_for_advance"] is True
    assert payload["ocr_waiting_for_advance_reason"] == "trigger_mode_after_advance_waiting_for_input"
    assert payload["ocr_last_tick_decision_at"] == "2026-04-29T00:00:00Z"
    assert payload["display_source_not_ocr_reason"] == ""
    assert payload["ocr_tick_gate_allowed"] is False
    assert payload["ocr_reader_manager_available"] is True
    assert payload["ocr_tick_skipped_reason"] == "tick_gate_closed"
    assert payload["pending_ocr_advance_capture"] is True
    assert payload["pending_manual_foreground_ocr_capture"] is True
    assert payload["pending_ocr_delay_remaining"] == pytest.approx(0.12)
    assert payload["pending_ocr_advance_capture_age_seconds"] == pytest.approx(1.25)
    assert payload["pending_ocr_advance_reason"] == "manual_foreground_advance"
    assert payload["pending_ocr_advance_clear_reason"] == ""
    assert payload["ocr_bootstrap_capture_needed"] is False
    assert payload["after_advance_screen_refresh_tick_needed"] is True
    assert payload["companion_after_advance_ocr_refresh_tick_needed"] is False
    assert payload["ocr_runtime_status"] == "active"
    assert payload["foreground_refresh_attempted"] is False
    assert payload["foreground_refresh_skipped_reason"] == "ocr_reader_not_allowed"


def test_status_payload_primary_diagnosis_reports_minimized_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = galgame_service.build_config({})
    state = _status_state(
        ocr_reader_runtime={
            "status": "running",
            "target_selection_detail": "memory_reader_window_minimized",
            "last_exclude_reason": "excluded_minimized_window",
            "candidate_count": 0,
        },
        latest_snapshot={
            "speaker": "",
            "text": "",
            "line_id": "",
            "scene_id": "scene-1",
            "route_id": "ocr",
            "choices": [],
            "is_menu_open": False,
        },
    )
    _patch_status_dependencies(monkeypatch)

    payload = galgame_service.build_status_payload(
        state,
        config=config,
        state_is_snapshot=True,
    )

    diagnosis = payload["primary_diagnosis"]
    assert diagnosis["severity"] == "warning"
    assert diagnosis["title"] == "游戏窗口已最小化"
    assert [action["id"] for action in diagnosis["actions"]] == [
        "refresh_ocr_windows",
        "select_ocr_window",
    ]


def test_status_payload_primary_diagnosis_reports_capture_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = galgame_service.build_config({})
    state = _status_state(
        ocr_reader_runtime={
            "status": "running",
            "ocr_context_state": "capture_failed",
            "last_capture_error": "PrintWindow timeout",
            "effective_window_key": "pid:100:hwnd:200",
        },
        latest_snapshot={
            "speaker": "",
            "text": "",
            "line_id": "",
            "scene_id": "scene-1",
            "route_id": "ocr",
            "choices": [],
            "is_menu_open": False,
        },
    )
    _patch_status_dependencies(monkeypatch)

    payload = galgame_service.build_status_payload(
        state,
        config=config,
        state_is_snapshot=True,
    )

    diagnosis = payload["primary_diagnosis"]
    assert diagnosis["severity"] == "error"
    assert diagnosis["title"] == "截图或文字识别失败"
    assert diagnosis["message"] == "PrintWindow timeout"
    assert {"recalibrate_ocr", "capture_backend", "debug_details"} <= {
        action["id"] for action in diagnosis["actions"]
    }


def test_status_payload_exposes_interval_background_polling_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = galgame_service.build_config({"ocr_reader": {"trigger_mode": "interval"}})
    state = _status_state(
        ocr_reader_runtime={
            "status": "active",
            "target_is_foreground": False,
            "effective_window_key": "pid:100:hwnd:200",
            "capture_backend_kind": "printwindow",
        },
    )
    _patch_status_dependencies(monkeypatch)

    payload = galgame_service.build_status_payload(
        state,
        config=config,
        state_is_snapshot=True,
    )

    assert payload["ocr_background_state"] == "background_polling"
    assert payload["ocr_background_polling"] is True
    assert payload["ocr_foreground_resume_pending"] is False
    assert payload["ocr_capture_backend_blocked"] is False
    assert "后台读取" in payload["ocr_background_message"]


def test_status_payload_reports_interval_background_capture_failure_with_backend_advice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = galgame_service.build_config({"ocr_reader": {"trigger_mode": "interval"}})
    state = _status_state(
        ocr_reader_runtime={
            "status": "active",
            "ocr_context_state": "capture_failed",
            "last_capture_error": "PrintWindow timeout",
            "target_is_foreground": False,
            "effective_window_key": "pid:100:hwnd:200",
            "capture_backend_kind": "printwindow",
        },
    )
    _patch_status_dependencies(monkeypatch)

    payload = galgame_service.build_status_payload(
        state,
        config=config,
        state_is_snapshot=True,
    )

    assert payload["ocr_background_state"] == "capture_backend_blocked"
    assert payload["ocr_capture_backend_blocked"] is True
    assert "窗口可见且未最小化" in payload["ocr_background_message"]
    diagnosis = payload["primary_diagnosis"]
    assert diagnosis["severity"] == "error"
    assert diagnosis["title"] == "截图或文字识别失败"
    assert "定时 OCR 后台读取" in diagnosis["message"]
    assert "切换截图方式" in diagnosis["message"]
    assert {"focus_game", "capture_backend", "select_ocr_window", "debug_details"} <= {
        action["id"] for action in diagnosis["actions"]
    }


def test_status_payload_primary_diagnosis_reports_poll_not_running() -> None:
    diagnosis = galgame_service.build_primary_diagnosis(
        {
            "ocr_reader_runtime": {
                "status": "running",
                "ocr_context_state": "poll_not_running",
            },
            "ocr_context_state": "poll_not_running",
            "ocr_capture_diagnostic": "OCR 轮询未继续执行。",
        }
    )

    assert diagnosis["severity"] == "error"
    assert diagnosis["title"] == "OCR 轮询没有继续运行"
    assert diagnosis["message"] == "OCR 轮询未继续执行。"


def test_status_payload_primary_diagnosis_reports_after_advance_waiting_gate() -> None:
    diagnosis = galgame_service.build_primary_diagnosis(
        {
            "ocr_tick_block_reason": "trigger_mode_after_advance_waiting_for_input",
            "ocr_reader_trigger_mode": "after_advance",
        }
    )

    assert diagnosis["severity"] == "info"
    assert diagnosis["title"] == "OCR 正在等待游戏推进"
    assert "点击对白后识别模式" in diagnosis["message"]
    assert {action["id"] for action in diagnosis["actions"]} == {"focus_game", "debug_details"}


def test_status_payload_primary_diagnosis_reports_ocr_emit_without_new_line() -> None:
    diagnosis = galgame_service.build_primary_diagnosis(
        {
            "ocr_emit_block_reason": "duplicate_stable_text",
        }
    )

    assert diagnosis["severity"] == "info"
    assert diagnosis["title"] == "OCR 已执行但没有新台词"
    assert "重复写入" in diagnosis["message"]
    assert {action["id"] for action in diagnosis["actions"]} == {"line_details", "debug_details"}


def test_status_payload_primary_diagnosis_ignores_stale_self_ui_guard_after_new_ocr() -> None:
    diagnosis = galgame_service.build_primary_diagnosis(
        {
            "ocr_reader_runtime": {
                "status": "active",
                "detail": "receiving_text",
                "last_rejected_ocr_reason": "self_ui_guard",
                "last_rejected_ocr_text": "Visual Studio Code terminal text",
                "last_rejected_ocr_at": "2026-05-01T03:20:00Z",
                "last_observed_at": "2026-05-01T03:22:00Z",
                "last_stable_line": {
                    "text": "杨军爷 [好！再干一杯！]",
                    "line_id": "ocr:new-line",
                    "ts": "2026-05-01T03:22:00Z",
                },
            },
        }
    )

    assert diagnosis["title"] != "OCR 抓到了非游戏画面"


def test_status_payload_primary_diagnosis_reports_observed_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = galgame_service.build_config({})
    state = _status_state(
        ocr_reader_runtime={
            "status": "running",
            "effective_window_key": "pid:100:hwnd:200",
            "last_observed_line": {
                "text": "新的候选台词。",
                "line_id": "line-observed",
            },
            "last_stable_line": {
                "text": "旧台词。",
                "line_id": "line-stable",
            },
        },
        latest_snapshot={
            "speaker": "",
            "text": "",
            "line_id": "",
            "scene_id": "scene-1",
            "route_id": "ocr",
            "choices": [],
            "is_menu_open": False,
        },
    )
    _patch_status_dependencies(monkeypatch)

    payload = galgame_service.build_status_payload(
        state,
        config=config,
        state_is_snapshot=True,
    )

    diagnosis = payload["primary_diagnosis"]
    assert diagnosis["severity"] == "info"
    assert diagnosis["title"] == "刚读到新文字"
    assert [action["id"] for action in diagnosis["actions"]] == ["line_details"]


@pytest.mark.parametrize(
    ("trigger_mode", "expected_message_parts", "unexpected_message_parts"),
    [
        (
            "after_advance",
            ["后台期间不会持续 OCR", "触发 OCR 重新采集"],
            ["伴读信息仍会刷新", "会尝试定时后台读取"],
        ),
        (
            "interval",
            ["会尝试定时后台读取", "取决于窗口可见性、非最小化状态和捕获后端"],
            ["伴读信息仍会刷新", "后台期间不会持续 OCR"],
        ),
        (
            "unknown",
            ["切回游戏窗口后会继续"],
            ["伴读信息仍会刷新", "后台读取"],
        ),
    ],
)
def test_status_payload_primary_diagnosis_reports_window_not_foreground_by_trigger_mode(
    trigger_mode: str,
    expected_message_parts: list[str],
    unexpected_message_parts: list[str],
) -> None:
    diagnosis = galgame_service.build_primary_diagnosis(
        {
            "agent_pause_kind": "window_not_foreground",
            "agent_user_status": "paused_window_not_foreground",
            "ocr_reader_trigger_mode": trigger_mode,
        }
    )

    assert diagnosis["severity"] == "info"
    assert diagnosis["title"] == "游戏不在前台"
    assert [action["id"] for action in diagnosis["actions"]] == ["focus_game"]
    for message_part in expected_message_parts:
        assert message_part in diagnosis["message"]
    for message_part in unexpected_message_parts:
        assert message_part not in diagnosis["message"]
