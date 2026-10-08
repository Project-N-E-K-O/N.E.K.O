"""Explicit ``memory_enabled`` key on mirror events (visit design PR-08, section 3.7.8).

Visit mirror events always carry ``{'memory_enabled': False}``; the key wins
over every other rule. Events without the key must keep the previous
behaviour exactly, which is pinned here against a frozen copy of the
pre-change implementation over a matrix of payloads.
"""

from __future__ import annotations

import itertools
from typing import Optional

import pytest

from main_logic.mirror_meta import (
    build_mirror_meta,
    is_mirror_assistant_message,
    is_mirror_event_memory_disabled,
    is_mirror_turn_end_meta,
)


def _frozen_payload_bool_from_keys(data: dict, *keys: str) -> Optional[bool]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
    return None


def _frozen_is_mirror_event_memory_disabled(event: dict) -> bool:
    # 改动前的实现逐字照抄：无显式键的事件必须与它逐个相等
    has_user_input = event.get("hasUserSpeech") is True or event.get("hasUserText") is True
    if has_user_input:
        player_interaction_enabled = _frozen_payload_bool_from_keys(
            event,
            "soccer_game_memory_player_interaction_enabled",
            "soccerGameMemoryPlayerInteractionEnabled",
        )
        if player_interaction_enabled is not None:
            return player_interaction_enabled is False
    else:
        event_reply_enabled = _frozen_payload_bool_from_keys(
            event,
            "soccer_game_memory_event_reply_enabled",
            "soccerGameMemoryEventReplyEnabled",
        )
        if event_reply_enabled is not None:
            return event_reply_enabled is False

    legacy_enabled = _frozen_payload_bool_from_keys(event, "game_memory_enabled", "gameMemoryEnabled")
    if legacy_enabled is not None:
        return legacy_enabled is False
    return not has_user_input


_VALUES = (None, True, False, "true", 1)
_KEYS = (
    "hasUserSpeech",
    "hasUserText",
    "soccer_game_memory_player_interaction_enabled",
    "soccerGameMemoryEventReplyEnabled",
    "game_memory_enabled",
)


def _event_matrix():
    for combo in itertools.product(_VALUES, repeat=len(_KEYS)):
        yield {key: value for key, value in zip(_KEYS, combo) if value is not None}


@pytest.mark.unit
def test_explicit_false_disables_memory_even_with_user_input():
    assert is_mirror_event_memory_disabled({"memory_enabled": False}) is True
    assert is_mirror_event_memory_disabled({
        "memory_enabled": False, "hasUserSpeech": True, "game_memory_enabled": True,
    }) is True


@pytest.mark.unit
def test_explicit_true_cannot_opt_a_line_into_memory():
    # 显式键只能「关」：外部控制器（小游戏）塞 True 绕不过宿主自己的记忆策略
    game_off = {"hasUserSpeech": True, "game_memory_enabled": False}
    assert is_mirror_event_memory_disabled({**game_off, "memory_enabled": True}) is True
    assert is_mirror_event_memory_disabled({"memory_enabled": True}) is _frozen_is_mirror_event_memory_disabled({})


@pytest.mark.unit
def test_events_without_the_key_replay_the_previous_behaviour():
    count = 0
    for event in _event_matrix():
        assert "memory_enabled" not in event
        assert is_mirror_event_memory_disabled(event) is _frozen_is_mirror_event_memory_disabled(event), event
        count += 1
    assert count == len(_VALUES) ** len(_KEYS)


@pytest.mark.unit
def test_visit_meta_filters_assistant_message_and_turn_end():
    meta = build_mirror_meta(
        source="neko_visit", kind="visit_debrief", session_id="v" * 22,
        event={"memory_enabled": False},
    )
    assert is_mirror_assistant_message({"type": "gemini_response", "metadata": meta}) is True
    assert is_mirror_turn_end_meta(meta) is True


@pytest.mark.unit
@pytest.mark.parametrize("value", [True, "false", "true", 0, 1, None, [], {}])
def test_anything_but_false_falls_back_to_the_previous_rules(value):
    for base in ({}, {"hasUserSpeech": True}, {"game_memory_enabled": False}):
        event = {**base, "memory_enabled": value}
        assert is_mirror_event_memory_disabled(event) is _frozen_is_mirror_event_memory_disabled(base)
