import pytest

from config.prompts.prompts_avatar_interaction import (
    _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES,
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
)
from main_logic.cross_server import _should_persist_avatar_interaction_memory
from main_logic.mirror_meta import (
    is_mirror_assistant_message,
    is_mirror_turn_end_meta,
)


# Shared test master_name: any string works; verify it expands verbatim into memory_note,
# rather than falling back to objectifying forms such as 'master'.
MASTER = "小明"


@pytest.mark.unit
def test_avatar_interaction_memory_meta_promotes_fist_and_hammer_summaries():
    fist_normal = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "normal",
    }, MASTER)
    fist_rapid = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "rapid",
    }, MASTER)
    hammer_normal = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "hammer",
        "action_id": "bonk",
        "intensity": "normal",
    }, MASTER)
    hammer_burst = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "hammer",
        "action_id": "bonk",
        "intensity": "burst",
    }, MASTER)

    assert fist_normal["memory_note"] == f"[{MASTER}用猫爪轻轻碰了你]"
    assert fist_rapid["memory_note"] == f"[{MASTER}用猫爪连续轻轻碰了你几下]"
    assert fist_rapid["memory_dedupe_rank"] > fist_normal["memory_dedupe_rank"]
    assert fist_rapid["memory_dedupe_key"] == fist_normal["memory_dedupe_key"] == "fist_touch"

    assert hammer_normal["memory_note"] == f"[{MASTER}用锤子敲了你一下]"
    assert hammer_burst["memory_note"] == f"[{MASTER}连续用锤子敲了你好几下]"
    assert hammer_burst["memory_dedupe_rank"] > hammer_normal["memory_dedupe_rank"]
    assert hammer_burst["memory_dedupe_key"] == hammer_normal["memory_dedupe_key"] == "hammer_bonk"


@pytest.mark.unit
def test_avatar_interaction_memory_window_allows_rank_upgrade_within_window():
    cache: dict[str, dict[str, int | str]] = {}

    first_persisted = _should_persist_avatar_interaction_memory(
        cache,
        f"[{MASTER}用猫爪轻轻碰了你]",
        "fist_touch",
        1,
    )
    upgraded_persisted = _should_persist_avatar_interaction_memory(
        cache,
        f"[{MASTER}用猫爪连续轻轻碰了你几下]",
        "fist_touch",
        2,
    )
    duplicate_summary_persisted = _should_persist_avatar_interaction_memory(
        cache,
        f"[{MASTER}用猫爪连续轻轻碰了你几下]",
        "fist_touch",
        2,
    )

    assert first_persisted is True
    assert upgraded_persisted is True
    assert duplicate_summary_persisted is False


@pytest.mark.unit
def test_rps_memory_window_dedupes_different_round_results_under_one_key():
    cache: dict[str, dict[str, int | str]] = {}
    user_win = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "rps",
        "user_gesture": "rock",
        "avatar_gesture": "scissors",
        "round_result": "user_win",
    }, MASTER)
    avatar_win = _build_avatar_interaction_memory_meta("zh", {
        "tool_id": "rps",
        "user_gesture": "rock",
        "avatar_gesture": "paper",
        "round_result": "avatar_win",
    }, MASTER)

    assert _should_persist_avatar_interaction_memory(
        cache,
        user_win["memory_note"],
        user_win["memory_dedupe_key"],
        user_win["memory_dedupe_rank"],
    ) is True
    assert _should_persist_avatar_interaction_memory(
        cache,
        avatar_win["memory_note"],
        avatar_win["memory_dedupe_key"],
        avatar_win["memory_dedupe_rank"],
    ) is False


@pytest.mark.unit
@pytest.mark.parametrize(("tool_id", "action_id"), [("fist", "poke"), ("hammer", "bonk")])
@pytest.mark.parametrize(
    ("locale", "fist_instruction", "fist_memory", "hammer_instruction", "hammer_memory"),
    [
        (
            "zh",
            f"{MASTER}刚刚用猫爪轻轻碰了你一下。",
            f"[{MASTER}用猫爪轻轻碰了你]",
            f"{MASTER}刚刚用锤子敲中了你一次。",
            f"[{MASTER}用锤子敲了你一下]",
        ),
        (
            "zh-TW",
            f"{MASTER}剛剛用貓爪輕輕碰了你一下。",
            f"[{MASTER}用貓爪輕輕碰了你]",
            f"{MASTER}剛剛用槌子敲中了你一次。",
            f"[{MASTER}用槌子敲了你一下]",
        ),
        (
            "en",
            f"{MASTER} just lightly touched you once with the cat paw.",
            f"[{MASTER} lightly touched you with the cat paw]",
            f"{MASTER} just hit you once with the hammer.",
            f"[{MASTER} bonked you once with a hammer]",
        ),
        (
            "ja",
            f"{MASTER}が今、猫の肉球で一度だけ軽く触れた。",
            f"[{MASTER}が猫の手であなたにそっと触れた]",
            f"{MASTER}が今、ハンマーで一度当てた。",
            f"[{MASTER}がハンマーであなたを一度叩いた]",
        ),
        (
            "ko",
            f"{MASTER} 방금 고양이 발로 한 번 가볍게 건드렸다.",
            f"[{MASTER}이 고양이 발로 너를 살짝 건드렸다]",
            f"{MASTER} 방금 망치로 한 번 맞혔다.",
            f"[{MASTER}이 망치로 너를 한 번 쳤다]",
        ),
        (
            "ru",
            f"{MASTER} только что один раз легко коснулся тебя кошачьей лапкой.",
            f"[{MASTER} слегка коснулся тебя кошачьей лапкой]",
            f"{MASTER} только что один раз попал по тебе молотком.",
            f"[{MASTER} один раз стукнул тебя молотком]",
        ),
        (
            "es",
            f"{MASTER} acaba de tocarte una vez con la patita de gato.",
            f"[{MASTER} te tocó suavemente con la pata de gato]",
            f"{MASTER} acaba de golpearte una vez con el martillo.",
            f"[{MASTER} te dio un golpe con un martillo]",
        ),
        (
            "pt",
            f"{MASTER} acabou de tocar em você uma vez com a patinha de gato.",
            f"[{MASTER} tocou você de leve com a patinha de gato]",
            f"{MASTER} acabou de bater em você uma vez com o martelo.",
            f"[{MASTER} bateu em você uma vez com um martelo]",
        ),
    ],
)
def test_avatar_prompt_and_memory_only_describe_actions_for_all_touch_zones(
    tool_id, action_id, locale,
    fist_instruction, fist_memory, hammer_instruction, hammer_memory,
):
    expected_instruction, expected_memory_note = (
        (fist_instruction, fist_memory)
        if tool_id == "fist"
        else (hammer_instruction, hammer_memory)
    )
    payload = {
        "tool_id": tool_id,
        "action_id": action_id,
        "intensity": "normal",
    }
    instruction_without_zone = _build_avatar_interaction_instruction(
        locale, "YUI", MASTER, payload
    )
    expected_memory = _build_avatar_interaction_memory_meta(locale, payload, MASTER)
    assert instruction_without_zone == expected_instruction
    assert expected_memory["memory_note"] == expected_memory_note

    for touch_zone in ("ear", "head", "face", "body"):
        zone_payload = {**payload, "touch_zone": touch_zone}
        instruction = _build_avatar_interaction_instruction(
            locale, "YUI", MASTER, zone_payload
        )
        memory = _build_avatar_interaction_memory_meta(locale, zone_payload, MASTER)

        assert instruction == expected_instruction
        assert memory == expected_memory


@pytest.mark.unit
def test_avatar_touch_zone_is_not_invented_when_missing_or_unsupported():
    fist = {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "normal",
    }
    lollipop = {
        "tool_id": "lollipop",
        "action_id": "offer",
        "intensity": "normal",
        "touch_zone": "head",
    }

    fist_instruction = _build_avatar_interaction_instruction("zh", "YUI", MASTER, fist)
    fist_memory = _build_avatar_interaction_memory_meta("zh", fist, MASTER)[
        "memory_note"
    ]
    lollipop_instruction = _build_avatar_interaction_instruction(
        "zh", "YUI", MASTER, lollipop
    )
    lollipop_memory = _build_avatar_interaction_memory_meta("zh", lollipop, MASTER)[
        "memory_note"
    ]

    assert fist_instruction == f"{MASTER}刚刚用猫爪轻轻碰了你一下。"
    assert fist_memory == f"[{MASTER}用猫爪轻轻碰了你]"
    assert "这次互动" not in lollipop_instruction
    assert "这次互动" not in lollipop_memory


@pytest.mark.unit
def test_avatar_instruction_is_one_direct_fact_and_ignores_text_context():
    locales = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
    payload = {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "rapid",
        "reward_drop": True,
        "touch_zone": "face",
    }

    for locale in locales:
        expected = _build_avatar_interaction_instruction(locale, "YUI", MASTER, payload)
        with_compatibility_draft = _build_avatar_interaction_instruction(
            locale,
            "YUI",
            MASTER,
            {**payload, "text_context": "这段历史草稿不得进入模型事件事实"},
        )

        assert with_compatibility_draft == expected
        assert "\n" not in expected


# ─────────────────────────────────────────────────────────────────────────────
# Guard against AI objectification: forbid "主人 / Your master / ご主人さま / 주인 / Хозяин"
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_avatar_memory_templates_have_no_dehumanizing_literals():
    """At the template-literal level, no (tool, action) template in any language may contain objectifying forms of address.

    This guards a core project value: new language/action templates accidentally containing
    subordinate forms such as 'master' immediately fail this test. Check the raw template (including the {master}
    placeholder), rather than formatted output.
    """
    forbidden = [
        "主人",
        "Your master",
        "your master",
        "Master",
        "master",  # Substring matching also catches terms alongside text other than the {master} placeholder
        "ご主人",
        "주인",
        "хозяин",
        "Хозяин",
    ]
    for locale, by_tool in _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.items():
        for tool_id, by_action in by_tool.items():
            for action_id, template in by_action.items():
                # Remove the {master} placeholder, then inspect remaining text for prohibited terms
                stripped = template.replace("{master}", "")
                for word in forbidden:
                    assert word not in stripped, (
                        f"locale={locale} tool={tool_id} action={action_id} "
                        f"template={template!r} 含物化称呼 '{word}'"
                    )


@pytest.mark.unit
def test_avatar_memory_meta_expands_master_name_literally():
    """The built memory_note must contain the supplied master_name without objectifying forms of address."""
    forbidden = ["主人", "Your master", "your master", "ご主人", "주인", "Хозяин", "хозяин"]
    payload = {"tool_id": "fist", "action_id": "poke", "intensity": "normal"}
    for locale in ("zh", "en", "ja", "ko", "ru", "zh-TW"):
        meta = _build_avatar_interaction_memory_meta(locale, payload, MASTER)
        note = meta["memory_note"]
        assert MASTER in note, f"locale={locale} note={note!r} 没展开 master_name"
        for word in forbidden:
            assert word not in note, f"locale={locale} note={note!r} 含物化称呼 '{word}'"


@pytest.mark.unit
def test_avatar_memory_meta_empty_master_falls_back_to_neutral_word():
    """Empty master_name falls back to localized neutral terms (\"对方 / they / 相手 / 상대 /
    собеседник\"), without reverting to objectifying address."""  # noqa: DOCSTRING_CJK
    forbidden = ["主人", "Your master", "your master", "ご主人", "주인", "Хозяин", "хозяин"]
    expected_neutral = {
        "zh": "对方",
        "zh-TW": "對方",
        "en": "they",
        "ja": "相手",
        "ko": "상대",
        "ru": "собеседник",
    }
    payload = {"tool_id": "fist", "action_id": "poke", "intensity": "normal"}
    for locale, neutral in expected_neutral.items():
        meta = _build_avatar_interaction_memory_meta(locale, payload, "")
        note = meta["memory_note"]
        assert neutral in note, f"locale={locale} note={note!r} 没用中性词回退"
        for word in forbidden:
            assert word not in note, f"locale={locale} note={note!r} 兜底回落到物化称呼 '{word}'"


@pytest.mark.unit
def test_avatar_memory_meta_master_name_passes_through_unchanged():
    """Expand every master_name input verbatim (Chinese, English, emoji, names with spaces, etc.)."""
    payload = {"tool_id": "fist", "action_id": "poke", "intensity": "normal"}
    for name in ("小明", "Alice", "mochi", "猫猫", "Mei Wang"):
        meta = _build_avatar_interaction_memory_meta("zh", payload, name)
        assert name in meta["memory_note"]


@pytest.mark.unit
def test_avatar_instruction_empty_master_uses_localized_neutral_actor():
    lollipop_payloads = [
        {"tool_id": "lollipop", "action_id": "offer", "intensity": "normal"},
        {"tool_id": "lollipop", "action_id": "tease", "intensity": "normal"},
        {"tool_id": "lollipop", "action_id": "tap_soft", "intensity": "rapid"},
        {"tool_id": "lollipop", "action_id": "tap_soft", "intensity": "burst"},
    ]
    expected_actor = {
        "zh": "对方",
        "zh-TW": "對方",
        "en": "The other person",
        "ja": "相手",
        "ko": "상대",
        "ru": "Собеседник",
        "es": "Esa persona",
        "pt": "A outra pessoa",
    }

    for locale, actor in expected_actor.items():
        for payload in lollipop_payloads:
            instruction = _build_avatar_interaction_instruction(locale, "YUI", "", payload)
            assert instruction.startswith(actor), (
                f"locale={locale} payload={payload} instruction={instruction!r} "
                "没把本地化中性 actor 放在事件主体位置"
            )
            assert not instruction.startswith((" ", "刚刚", "剛剛", "が", "이 "))

            whitespace_instruction = _build_avatar_interaction_instruction(
                locale, "YUI", "   ", payload
            )
            assert whitespace_instruction == instruction


@pytest.mark.unit
def test_avatar_instruction_named_master_still_uses_given_actor():
    payload = {
        "tool_id": "fist",
        "action_id": "poke",
        "intensity": "normal",
    }

    zh = _build_avatar_interaction_instruction("zh", "YUI", "哥哥", payload)
    en = _build_avatar_interaction_instruction("en", "YUI", "Alice", payload)
    ko_vowel = _build_avatar_interaction_instruction("ko", "YUI", "유이", payload)
    ko_final = _build_avatar_interaction_instruction("ko", "YUI", "민준", payload)
    ko_cjk = _build_avatar_interaction_instruction("ko", "YUI", "哥哥", payload)

    assert zh.startswith("哥哥刚刚用猫爪")
    assert en.startswith("Alice just lightly touched")
    assert ko_vowel.startswith("유이가 방금")
    assert ko_final.startswith("민준이 방금")
    assert ko_cjk.startswith("哥哥 방금")


@pytest.mark.unit
def test_avatar_instruction_rapid_fist_reward_keeps_repeated_touch_fact():
    instruction = _build_avatar_interaction_instruction(
        "zh",
        "YUI",
        "哥哥",
        {
            "tool_id": "fist",
            "action_id": "poke",
            "intensity": "rapid",
            "reward_drop": True,
        },
    )

    assert "连续轻轻碰" in instruction
    assert "奖励" in instruction


@pytest.mark.unit
def test_game_route_auto_assistant_lines_are_game_only_for_ordinary_memory():
    assert is_mirror_assistant_message({
        "type": "gemini_response",
        "text": "嘿嘿，这球归我啦",
        "metadata": {
            "source": "game-llm-result",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {
                    "kind": "goal-scored",
                    "hasUserSpeech": False,
                    "hasUserText": False,
                },
            },
        },
    }) is True

    assert is_mirror_assistant_message({
        "type": "gemini_response",
        "text": "看我这一脚",
        "metadata": {
            "source": "game-llm-result",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {"kind": "opening-line"},
            },
        },
    }) is True


@pytest.mark.unit
def test_game_route_user_reply_assistant_lines_stay_in_ordinary_memory():
    assert is_mirror_assistant_message({
        "type": "gemini_response",
        "text": "好啦，我听见你说难了。",
        "metadata": {
            "source": "game-llm-result",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {
                    "kind": "user-text",
                    "hasUserText": True,
                },
            },
        },
    }) is False

    assert is_mirror_assistant_message({
        "type": "gemini_response",
        "text": "普通回复",
        "metadata": {"source": "normal_chat"},
    }) is False


@pytest.mark.unit
def test_game_route_memory_disabled_user_reply_lines_are_game_only():
    assert is_mirror_assistant_message({
        "type": "gemini_response",
        "text": "我听到你说难了，但这局不进记忆。",
        "metadata": {
            "source": "game-llm-result",
            "mirror": {
                "kind": "soccer",
                "session_id": "match_1",
                "event": {
                    "kind": "user-text",
                    "hasUserText": True,
                    "soccerGameMemoryPlayerInteractionEnabled": False,
                },
            },
        },
    }) is True


@pytest.mark.unit
def test_game_route_auto_tts_turn_end_is_game_only_for_ordinary_memory():
    assert is_mirror_turn_end_meta({
        "source": "game_route",
        "mirror": {
            "kind": "soccer",
            "session_id": "match_1",
            "event": {
                "kind": "goal-scored",
                "hasUserSpeech": False,
                "hasUserText": False,
            },
        },
    }) is True

    assert is_mirror_turn_end_meta({
        "source": "game_route",
        "mirror": {
            "kind": "soccer",
            "session_id": "match_1",
            "event": {
                "kind": "user-voice",
                "hasUserSpeech": True,
            },
        },
    }) is False

    assert is_mirror_turn_end_meta({
        "source": "game_route",
        "game_type": "soccer",
        "session_id": "match_1",
    }) is False

    assert is_mirror_turn_end_meta({
        "source": "game_route",
        "mirror": {
            "kind": "soccer",
            "session_id": "match_1",
            "event": {
                "kind": "user-voice",
                "hasUserSpeech": True,
                "soccer_game_memory_player_interaction_enabled": False,
            },
        },
    }) is True

    assert is_mirror_turn_end_meta({
        "source": "game_route",
        "mirror": {
            "kind": "soccer",
            "session_id": "match_1",
            "event": {
                "kind": "goal-scored",
                "hasUserSpeech": False,
                "hasUserText": False,
                "soccer_game_memory_event_reply_enabled": True,
            },
        },
    }) is False
