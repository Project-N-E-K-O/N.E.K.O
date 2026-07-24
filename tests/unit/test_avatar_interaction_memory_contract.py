import pytest

from config.prompts.prompts_avatar_interaction import (
    _AVATAR_INTERACTION_TOUCH_ZONE_FACTS,
    _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES,
    _build_avatar_interaction_instruction,
    _build_avatar_interaction_memory_meta,
)
from main_logic.cross_server import _should_persist_avatar_interaction_memory
from main_logic.mirror_meta import (
    is_mirror_assistant_message,
    is_mirror_turn_end_meta,
)


# 测试公用 master_name —— 任意字符串即可，关键是验证它会被原样展开进 memory_note，
# 而不是回落到"主人"等物化称呼。
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
@pytest.mark.parametrize("tool_id", ["fist", "hammer"])
def test_avatar_prompt_and_memory_preserve_touch_zone_facts_in_all_locales(tool_id):
    locales = ("zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt")
    action_id = "poke" if tool_id == "fist" else "bonk"

    for locale in locales:
        instructions = []
        memory_notes = []
        for touch_zone, expected_fact in _AVATAR_INTERACTION_TOUCH_ZONE_FACTS[
            locale
        ].items():
            payload = {
                "tool_id": tool_id,
                "action_id": action_id,
                "intensity": "normal",
                "touch_zone": touch_zone,
            }
            instruction = _build_avatar_interaction_instruction(
                locale, "YUI", MASTER, payload
            )
            memory_note = _build_avatar_interaction_memory_meta(
                locale, payload, MASTER
            )["memory_note"]

            assert expected_fact in instruction
            assert expected_fact.rstrip(".。") in memory_note
            instructions.append(instruction)
            memory_notes.append(memory_note)

        assert len(set(instructions)) == 4
        assert len(set(memory_notes)) == 4


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

    assert "这次互动的位置" not in fist_instruction
    assert "这次互动的位置" not in fist_memory
    assert "这次互动的位置" not in lollipop_instruction
    assert "这次互动的位置" not in lollipop_memory


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
# 反 AI 物化护栏：禁止"主人 / Your master / ご主人さま / 주인 / Хозяин"
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_avatar_memory_templates_have_no_dehumanizing_literals():
    """模板字面量层面：所有语言的所有 (tool, action) 模板都不能含物化称呼。

    这是项目核心价值观的护栏——以后再有人加新语言/新 action 模板，手滑写进
    "主人"等附属称呼会被这条直接 fail 拦下。注意是检查模板原文（含 {master}
    占位符），不是格式化后的输出。
    """
    forbidden = [
        "主人",
        "Your master",
        "your master",
        "Master",
        "master",  # 跟 {master} 占位符以外的文字共存时 substring 也会命中
        "ご主人",
        "주인",
        "хозяин",
        "Хозяин",
    ]
    for locale, by_tool in _AVATAR_INTERACTION_MEMORY_NOTE_TEMPLATES.items():
        for tool_id, by_action in by_tool.items():
            for action_id, template in by_action.items():
                # 把 {master} 占位符抠掉，再检查残留文本是否含禁词
                stripped = template.replace("{master}", "")
                for word in forbidden:
                    assert word not in stripped, (
                        f"locale={locale} tool={tool_id} action={action_id} "
                        f"template={template!r} 含物化称呼 '{word}'"
                    )


@pytest.mark.unit
def test_avatar_memory_meta_expands_master_name_literally():
    """build 出来的 memory_note 应该包含传入的 master_name，且不含物化称呼。"""
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
    """master_name 传空时按本地化中性词兜底（"对方 / they / 相手 / 상대 /
    собеседник"），同样不会回落到物化称呼。"""
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
    """各种 master_name 输入都应原样展开（中文、英文、emoji、含空格名字等）。"""
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
