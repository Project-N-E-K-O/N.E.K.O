import pytest

from config.prompts.prompts_minigame_route import (
    GAME_CONTEXT_SIGNAL_GROUP_KEYS,
    get_game_archive_fallback_highlight_labels,
    get_game_archive_highlight_source_labels,
    get_game_archive_memory_highlighter_system_prompt,
    get_game_archive_memory_highlighter_user_prompt,
    get_game_archive_memory_summary_labels,
    get_game_archive_memory_text_labels,
    get_game_chat_event_user_prompt,
    get_game_context_formatter_labels,
    get_game_context_organizer_system_prompt,
    get_game_context_organizer_user_prompt,
    get_game_dialog_memory_line_labels,
    get_game_postgame_context_labels,
    get_game_postgame_event_texts,
    get_game_postgame_realtime_nudge_labels,
    get_game_recent_history_message_labels,
)
from main_routers import game_router
from main_routers.game_router import game_context as gr_game_context
from main_routers.game_router import runtime as gr_runtime


LOCALES = ("zh", "en", "ja", "ko", "ru", "es", "pt")
LEGACY_SIGNAL_GROUP_KEYS = ("玩家信号", "关系互动信号", "猫娘信号", "本局事实", "口头声明")


@pytest.mark.unit
@pytest.mark.parametrize("locale", LOCALES)
def test_game_route_prompt_getters_return_locale_content(locale):
    assert get_game_context_organizer_system_prompt(locale)
    assert get_game_context_organizer_user_prompt(locale)
    assert get_game_chat_event_user_prompt(locale)
    assert get_game_archive_memory_highlighter_system_prompt(locale)

    label_getters = (
        get_game_archive_highlight_source_labels,
        get_game_archive_fallback_highlight_labels,
        get_game_archive_memory_summary_labels,
        get_game_archive_memory_text_labels,
        get_game_context_formatter_labels,
        get_game_dialog_memory_line_labels,
        get_game_recent_history_message_labels,
        get_game_postgame_context_labels,
        get_game_postgame_realtime_nudge_labels,
        get_game_postgame_event_texts,
    )
    for getter in label_getters:
        labels = getter(locale)
        assert labels
        assert all(str(value).strip() for value in labels.values())


@pytest.mark.unit
@pytest.mark.parametrize("locale", LOCALES)
def test_game_context_organizer_schema_keys_are_english_wire_format(locale):
    prompt = get_game_context_organizer_system_prompt(locale)

    for key in GAME_CONTEXT_SIGNAL_GROUP_KEYS:
        assert key in prompt
    for legacy_key in LEGACY_SIGNAL_GROUP_KEYS:
        assert legacy_key not in prompt


@pytest.mark.unit
@pytest.mark.parametrize("locale", LOCALES)
def test_naked_game_route_user_prompts_keep_chinese_watermark(locale):
    organizer_prompt = get_game_context_organizer_user_prompt(locale).format(payload="{}")
    game_chat_prompt = get_game_chat_event_user_prompt(locale).format(event="{}")
    highlighter_prompt = get_game_archive_memory_highlighter_user_prompt(locale).format(source="材料")

    assert "======以上为游戏上下文整理输入======" in organizer_prompt
    assert "======以上为游戏事件输入======" in game_chat_prompt
    assert "======以上为赛后记忆筛选材料======" in highlighter_prompt


@pytest.mark.unit
def test_english_archive_prompts_keep_literal_localized_markers():
    system_prompt = get_game_archive_memory_highlighter_system_prompt("en")
    source_labels = get_game_archive_highlight_source_labels("en")

    assert 'literal marker "Player:"' in system_prompt
    assert '"event text" inside "Game event" lines' in system_prompt
    assert 'literal marker "Player:"' in source_labels["role_explanation"]
    assert '"event text" inside "Game event" lines' in source_labels["role_explanation"]
    assert "explicitly marked as player speech" not in system_prompt
    assert "explicitly marked as player speech" not in source_labels["role_explanation"]


@pytest.mark.unit
def test_game_context_signal_normalizer_accepts_legacy_zh_group_keys():
    normalized = gr_game_context._normalize_game_context_signals({
        "玩家信号": [{
            "signalLabel": "玩家在意追分",
            "summary": "玩家多次提到追分。",
            "evidence": [{"id": "glog_0001", "quote": "快追上了"}],
            "lastRound": 1,
            "count": 1,
        }],
        "关系互动信号": ["轻松互相调侃"],
    })

    assert normalized["player_signals"][0]["signalLabel"] == "玩家在意追分"
    assert normalized["relationship_signals"][0]["signalLabel"] == "轻松互相调侃"
    assert normalized["character_signals"] == []
    assert normalized["session_facts"] == []
    assert normalized["verbal_claims"] == []


@pytest.mark.unit
def test_router_formatter_uses_requested_english_locale():
    zh_text = gr_runtime._compact_realtime_context_text(
        "soccer",
        {"state": {"score": {"player": 1, "ai": 2}}, "pendingItems": []},
        "zh",
    )
    en_text = gr_runtime._compact_realtime_context_text(
        "soccer",
        {"state": {"score": {"player": 1, "ai": 2}}, "pendingItems": []},
        "en",
    )

    assert "[游戏上下文更新]" in zh_text
    assert "[Game Context Update]" in en_text
    assert "non-voice game context" in en_text
    assert en_text != zh_text
