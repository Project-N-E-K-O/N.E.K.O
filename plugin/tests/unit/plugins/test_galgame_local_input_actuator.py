from __future__ import annotations

import pytest

from plugin.plugins.galgame_plugin import local_input_actuator as local_input


pytestmark = pytest.mark.plugin_unit


def test_local_input_visible_choice_detection_accepts_menu_flag_or_choices() -> None:
    assert local_input._snapshot_has_visible_choices(
        {"latest_snapshot": {"is_menu_open": True, "choices": []}}
    ) is True
    assert local_input._snapshot_has_visible_choices(
        {"latest_snapshot": {"is_menu_open": False, "choices": [{"text": "Left"}]}}
    ) is True
    assert local_input._snapshot_has_visible_choices(
        {"latest_snapshot": {"is_menu_open": False, "choices": []}}
    ) is False


def test_local_input_choose_index_uses_choice_payload_index() -> None:
    assert local_input._choose_index(
        {
            "candidate_index": 0,
            "candidate_choices": [
                {"text": "First visible", "index": 2},
                {"text": "Second visible", "index": 4},
            ],
        }
    ) == 2


def test_local_input_choose_keyboard_reset_uses_visible_choice_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    taps: list[tuple[int, int]] = []
    choices = [{"text": f"Choice {index}", "index": index} for index in range(12)]

    monkeypatch.setattr(local_input.sys, "platform", "win32")
    monkeypatch.setattr(local_input, "_runtime_target", lambda shared: {"pid": 1234})
    monkeypatch.setattr(local_input, "_find_window_for_pid", lambda pid: (5678, (0, 0, 1280, 720)))
    monkeypatch.setattr(local_input, "_window_text", lambda hwnd: "Demo Game")
    monkeypatch.setattr(local_input, "_input_safety_policy_block_reason", lambda **kwargs: "")
    monkeypatch.setattr(local_input, "_focus_window", lambda hwnd: True)
    monkeypatch.setattr(
        local_input,
        "_tap_key",
        lambda hwnd, vk, count=1, delay=0.05: taps.append((vk, count)),
    )

    result = local_input.perform_local_input_actuation(
        {"ocr_reader_runtime": {"pid": 1234}},
        {"kind": "choose", "candidate_index": 10, "candidate_choices": choices},
    )

    assert result["success"] is True
    assert taps[0] == (local_input.VK_UP, len(choices))
    assert taps[1] == (local_input.VK_DOWN, 10)
    assert taps[2] == (local_input.VK_RETURN, 1)


def test_local_input_virtual_mouse_skips_forbidden_candidate() -> None:
    target = local_input._resolve_virtual_mouse_dialogue_target(
        {"virtual_mouse_target_id": "unsafe"},
        (100, 200, 1100, 1000),
        candidates=(
            {"target_id": "unsafe", "relative_x": 0.9, "relative_y": 0.9},
            {"target_id": "safe", "relative_x": 0.3, "relative_y": 0.7},
        ),
    )

    assert target["success"] is True
    assert target["target_id"] == "safe"
    assert target["screen_x"] == 400
    assert target["screen_y"] == 760
    assert target["skipped_candidates"][0]["forbidden_zone"] == "bottom_toolbar"
