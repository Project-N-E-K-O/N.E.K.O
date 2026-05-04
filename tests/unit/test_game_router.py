import asyncio
from unittest.mock import AsyncMock

import pytest

from .game_route_test_helpers import (
    mark_game_started as _mark_game_started,
    set_soccer_game_memory_policy as _set_soccer_game_memory_policy,
)
from main_routers import game_router


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _put_game_session(lanlan_name, game_type, session_id, session):
    key = game_router._game_session_key(lanlan_name, game_type, session_id)
    game_router._game_sessions[key] = {
        "session": session,
        "reply_chunks": [],
        "lanlan_name": lanlan_name,
        "game_type": game_type,
        "session_id": session_id,
        "last_activity": 0,
        "lock": None,
    }
    return key


@pytest.mark.unit
def test_parse_control_instructions_extracts_json_line():
    result = game_router._parse_control_instructions(
        '这球我拿下了喵\n{"mood":"happy","difficulty":"lv2"}'
    )

    assert result == {
        "line": "这球我拿下了喵",
        "control": {"mood": "happy", "difficulty": "lv2"},
    }


@pytest.mark.unit
def test_soccer_prompt_marks_game_event_text_as_not_user_speech():
    assert "textRaw 只是游戏事件原文或你这边的内建气泡，不是玩家说的话" in game_router._SOCCER_SYSTEM_PROMPT
    assert "goal-conceded=玩家进球/你丢球" in game_router._SOCCER_SYSTEM_PROMPT


@pytest.mark.unit
def test_neutral_pregame_context_uses_lv2_lv3_default(monkeypatch):
    monkeypatch.setattr(game_router.random, "choice", lambda seq: "lv3")

    context, invalid = game_router._normalize_soccer_pregame_context({
        "gameStance": "neutral_play",
        "initialDifficulty": "max",
        "initialMood": "calm",
    })

    assert invalid is True
    assert context["gameStance"] == "neutral_play"
    assert context["initialDifficulty"] == "lv3"


@pytest.mark.unit
def test_special_pregame_context_can_keep_max_difficulty(monkeypatch):
    monkeypatch.setattr(game_router.random, "choice", lambda seq: "lv2")

    context, invalid = game_router._normalize_soccer_pregame_context({
        "gameStance": "punishing",
        "initialDifficulty": "max",
        "initialMood": "angry",
        "emotionIntensity": 0.9,
        "emotionInertia": "high",
    })

    assert invalid is False
    assert context["gameStance"] == "punishing"
    assert context["initialDifficulty"] == "max"
    assert context["initialMood"] == "angry"


@pytest.mark.unit
def test_soccer_anger_pressure_cap_applies_only_to_punishing_anger_context():
    state = {
        "preGameContext": {
            "gameStance": "punishing",
            "nekoEmotion": "angry",
            "initialMood": "angry",
            "launchIntent": "punishment_session",
        },
    }
    event = {
        "score": {"player": 5, "ai": 26},
        "scoreDiff": 21,
        "difficulty": "max",
        "mood": "angry",
        "requestControlReason": True,
    }

    cap = game_router._build_soccer_anger_pressure_cap(event, state)

    assert cap["applicable"] is True
    assert cap["reached"] is True
    assert cap["capGoals"] == 25
    assert cap["recommendedDifficulty"] == "lv4"

    neutral = {
        "preGameContext": {
            "gameStance": "competitive",
            "nekoEmotion": "happy",
            "initialMood": "happy",
        },
    }
    assert game_router._build_soccer_anger_pressure_cap(event, neutral) == {}


@pytest.mark.unit
def test_soccer_anger_pressure_cap_uses_persona_stamina_bounds():
    event = {
        "score": {"player": 1, "ai": 9},
        "scoreDiff": 8,
        "difficulty": "max",
        "mood": "angry",
    }
    state = {
        "preGameContext": {
            "gameStance": "punishing",
            "nekoEmotion": "angry",
            "initialMood": "angry",
        },
    }

    weak_cap = game_router._build_soccer_anger_pressure_cap(
        event,
        state,
        lanlan_prompt="体力弱，不擅长运动，跑一会儿就容易累。",
    )
    strong_cap = game_router._build_soccer_anger_pressure_cap(
        event,
        state,
        lanlan_prompt="擅长运动，体力强，运动神经很好。",
    )

    assert weak_cap["capGoals"] == 8
    assert weak_cap["reached"] is True
    assert strong_cap["capGoals"] == 50
    assert strong_cap["reached"] is False


@pytest.mark.unit
def test_soccer_anger_pressure_cap_clamps_max_control_after_limit():
    event = {
        "score": {"player": 4, "ai": 26},
        "scoreDiff": 22,
        "difficulty": "max",
        "mood": "angry",
        "requestControlReason": True,
        "angerPressureCap": {
            "applicable": True,
            "reached": True,
            "capGoals": 25,
            "aiGoals": 26,
            "playerGoals": 4,
            "scoreDiff": 22,
            "recommendedDifficulty": "lv4",
        },
    }
    result = {
        "line": "还没完。",
        "control": {
            "mood": "angry",
            "difficulty": "max",
            "reason": "继续惩罚玩家",
        },
    }

    adjusted = game_router._apply_soccer_anger_pressure_cap(result, event)

    assert adjusted["control"]["difficulty"] == "lv4"
    assert "继续惩罚玩家" in adjusted["control"]["reason"]
    assert "体力上限" in adjusted["control"]["reason"]
    assert adjusted["anger_pressure_cap"]["adjusted"] is True


@pytest.mark.unit
def test_soccer_anger_pressure_cap_forces_difficulty_when_llm_omits_control():
    event = {
        "score": {"player": 4, "ai": 26},
        "scoreDiff": 22,
        "difficulty": "max",
        "mood": "angry",
        "requestControlReason": True,
        "angerPressureCap": {
            "applicable": True,
            "reached": True,
            "capGoals": 25,
            "aiGoals": 26,
            "playerGoals": 4,
            "scoreDiff": 22,
            "recommendedDifficulty": "lv4",
        },
    }
    result = {"line": "呼……先停一下。", "control": {}}

    adjusted = game_router._apply_soccer_anger_pressure_cap(result, event)

    assert adjusted["control"]["difficulty"] == "lv4"
    assert adjusted["control"]["reason"] == "狂怒压制已到体力上限，改为降强度继续处理情绪"
    assert adjusted["anger_pressure_cap"]["adjusted"] is True


@pytest.mark.unit
def test_pregame_opening_line_is_short_and_does_not_repeat_invite():
    context, invalid = game_router._normalize_soccer_pregame_context({
        "gameStance": "soft_teasing",
        "initialDifficulty": "lv2",
        "openingLine": "那我认真了",
    })
    assert invalid is False
    assert context["openingLine"] == "那我认真了"

    too_long, too_long_invalid = game_router._normalize_soccer_pregame_context({
        "gameStance": "soft_teasing",
        "initialDifficulty": "lv2",
        "openingLine": "这次要认真看着我踢球哦玩家不许走神",
    })
    assert too_long_invalid is True
    assert too_long["openingLine"] == ""

    repeated, _ = game_router._normalize_soccer_pregame_context(
        {
            "gameStance": "competitive",
            "initialDifficulty": "lv2",
            "openingLine": "来踢球吧，玩家。",
        },
        neko_invite_text="来踢球吧，玩家。",
    )
    assert repeated["openingLine"] == ""


@pytest.mark.unit
def test_game_prompt_includes_pregame_context():
    prompt = game_router._build_game_prompt(
        "soccer",
        "Lan",
        "喜欢陪玩家玩。",
        {"gameStance": "withdrawn", "tonePolicy": "低声回应。"},
    )

    assert "开局上下文" in prompt
    assert '"gameStance":"withdrawn"' in prompt
    assert "不要把 neutral_play 强行解释成哄开心或关系修复" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_pregame_context_uses_empty_history_fallback(monkeypatch):
    monkeypatch.setattr(game_router.random, "choice", lambda seq: "lv2")
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "喜欢踢球。",
        "model": "fake",
        "base_url": "http://fake",
        "api_type": "local",
        "api_key": "key",
    })

    async def fake_fetch(_lanlan_name):
        return "", "recent_history_failed"

    async def fake_ai(**kwargs):
        assert kwargs["recent_history"] == ""
        return {
            "gameStance": "neutral_play",
            "initialMood": "calm",
            "initialDifficulty": "lv2",
        }

    monkeypatch.setattr(game_router, "_fetch_recent_history_for_pregame", fake_fetch)
    monkeypatch.setattr(game_router, "_run_soccer_pregame_context_ai", fake_ai)

    context, source, error = await game_router._build_soccer_pregame_context(
        game_type="soccer",
        session_id="match_1",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
    )

    assert source == "ai"
    assert error == "recent_history_failed"
    assert context["gameStance"] == "neutral_play"
    assert context["initialDifficulty"] == "lv2"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_pregame_context_invalid_json_falls_back(monkeypatch):
    monkeypatch.setattr(game_router.random, "choice", lambda seq: "lv3")
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "",
        "model": "fake",
        "base_url": "http://fake",
        "api_type": "local",
        "api_key": "key",
    })

    async def fake_fetch(_lanlan_name):
        return "玩家 | 来踢球", ""

    async def fake_ai(**_kwargs):
        raise ValueError("bad json")

    monkeypatch.setattr(game_router, "_fetch_recent_history_for_pregame", fake_fetch)
    monkeypatch.setattr(game_router, "_run_soccer_pregame_context_ai", fake_ai)

    context, source, error = await game_router._build_soccer_pregame_context(
        game_type="soccer",
        session_id="match_1",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
    )

    assert source == "fallback"
    assert error == "invalid_json"
    assert context["gameStance"] == "neutral_play"
    assert context["initialDifficulty"] == "lv3"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_pregame_context_partial_invalid_fields(monkeypatch):
    monkeypatch.setattr(game_router.random, "choice", lambda seq: "lv2")
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {
        "lanlan_name": "Lan",
        "master_name": "玩家",
        "lanlan_prompt": "",
        "model": "fake",
        "base_url": "http://fake",
        "api_type": "local",
        "api_key": "key",
    })

    async def fake_fetch(_lanlan_name):
        return "玩家 | 你这个笨蛋！", ""

    async def fake_ai(**_kwargs):
        return {
            "gameStance": "punishing",
            "initialDifficulty": "max",
            "initialMood": "angry",
            "emotionIntensity": 2,
            "openingLine": "那我认真了",
        }

    monkeypatch.setattr(game_router, "_fetch_recent_history_for_pregame", fake_fetch)
    monkeypatch.setattr(game_router, "_run_soccer_pregame_context_ai", fake_ai)

    context, source, error = await game_router._build_soccer_pregame_context(
        game_type="soccer",
        session_id="match_1",
        lanlan_name="Lan",
        neko_initiated=False,
        neko_invite_text="",
    )

    assert source == "ai"
    assert error == "invalid_fields"
    assert context["gameStance"] == "punishing"
    assert context["initialDifficulty"] == "max"
    assert context["emotionIntensity"] == 0.0
    assert context["openingLine"] == "那我认真了"


@pytest.mark.unit
def test_game_archive_memory_payload_uses_system_note_shape():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "summary": "soccer 小游戏结束。最终/最近比分：玩家 1 : 4 Lan。",
        "game_memory_tail_count": 2,
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "memory_highlights": {
            "important_records": ["玩家要求温柔一点，你改成让球式回应。"],
            "important_game_events": ["猫娘大比分领先后开始放水。"],
            "state_carryback": "赛后猫娘仍有点得意，但愿意继续陪玩家玩。",
            "postgame_tone": "得意但放软",
            "memory_summary": "玩家希望猫娘温柔一点，猫娘开始让球。",
        },
        "last_full_dialogues": [
            {"type": "user", "text": "温柔一点"},
            {"type": "assistant", "line": "好好好，让你踢。"},
        ],
        "key_events": [],
        "last_state": {"score": {"player": 1, "ai": 4}},
    }

    messages = game_router._build_game_archive_memory_messages(archive)

    assert [msg["role"] for msg in messages] == ["user", "assistant", "system"]
    assert messages[0]["content"][0]["text"] == "温柔一点"
    assert messages[1]["content"][0]["text"] == "好好好，让你踢。"
    system_text = messages[2]["content"][0]["text"]
    assert "游戏模块归档，不是玩家逐字发言" in system_text
    assert "soccer 游戏结束" not in system_text
    assert "官方结果：玩家 1 : 4 Lan。口头让步不改官方结果。" in system_text
    assert "官方结果永远以 finalScore / last_state.score 为准" not in system_text
    assert "口头让步规则" not in system_text
    assert "重要互动：" in system_text
    assert "玩家要求温柔一点，你改成让球式回应。" in system_text
    assert "猫娘记住的本局事件：" in system_text
    assert "赛后状态延续：赛后猫娘仍有点得意，但愿意继续陪玩家玩。" in system_text
    assert "赛后语气：得意但放软" in system_text
    assert "后续记忆摘要：玩家希望猫娘温柔一点，猫娘开始让球。" in system_text
    assert "倒数 2 条规则" in system_text
    assert "本条 system 归档不计入倒数 2 条" in system_text
    assert "本局记录了" not in system_text
    assert "外部接管模式" not in system_text
    assert "玩家最近在比赛里说：温柔一点" not in system_text
    assert "你最后回应：好好好，让你踢。" not in system_text


@pytest.mark.unit
def test_game_archive_memory_tail_uses_game_dialog_order_without_event_labels():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "summary": "soccer 小游戏结束。",
        "game_memory_tail_count": 4,
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "memory_highlights": {},
        "full_dialogues": [
            {"type": "user", "text": "很早的话"},
            {"type": "game_event", "kind": "steal", "text": "纯事实没有台词"},
            {"type": "game_event", "kind": "goal-scored", "text": "进球", "result_line": "嘿嘿，这球归我啦"},
            {"type": "user", "text": "你刚才说算我赢？"},
            {"type": "assistant", "source": "game_llm", "line": "那是哄你的，比分可没改哦。"},
        ],
        "last_state": {"score": {"player": 9, "ai": 20}},
    }

    messages = game_router._build_game_archive_memory_messages(archive)

    assert [msg["role"] for msg in messages] == ["assistant", "user", "assistant", "system"]
    assert messages[0]["content"][0]["text"] == "嘿嘿，这球归我啦"
    assert "本局游戏事件" not in messages[0]["content"][0]["text"]
    assert messages[1]["content"][0]["text"] == "你刚才说算我赢？"
    assert messages[2]["content"][0]["text"] == "那是哄你的，比分可没改哦。"
    system_text = messages[-1]["content"][0]["text"]
    assert "官方结果：玩家 9 : 20 Lan。口头让步不改官方结果。" in system_text
    assert "口头让步规则" not in system_text
    assert "倒数 4 条规则" in system_text


@pytest.mark.unit
def test_game_archive_memory_prefers_final_score_over_oral_concession_text():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "summary": "soccer 小游戏结束。",
        "finalScore": {"player": 9, "ai": 20},
        "last_state": {"score": {"player": 99, "ai": 0}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {"type": "game_event", "kind": "goal-scored", "result_line": "行吧，这局算你赢。"},
        ],
    }

    messages = game_router._build_game_archive_memory_messages(archive, tail_count=1)
    system_text = messages[-1]["content"][0]["text"]

    assert "官方结果：玩家 9 : 20 Lan。口头让步不改官方结果。" in system_text
    assert "官方结果永远以 finalScore / last_state.score 为准" not in system_text
    assert "口头让步规则" not in system_text
    assert messages[0]["role"] == "assistant"
    assert messages[0]["content"][0]["text"] == "行吧，这局算你赢。"


@pytest.mark.unit
def test_game_archive_tail_respects_independent_soccer_memory_policy():
    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "summary": "soccer 小游戏结束。",
        "last_state": {"score": {"player": 1, "ai": 2}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": False,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {"type": "user", "text": "这句不进记忆"},
            {"type": "assistant", "source": "game_llm", "line": "直接回复也不进记忆"},
            {"type": "game_event", "kind": "goal-scored", "result_line": "事件回复可以进记忆"},
        ],
    }

    messages = game_router._build_game_archive_memory_messages(archive, tail_count=3)

    assert [msg["role"] for msg in messages] == ["assistant", "system"]
    assert messages[0]["content"][0]["text"] == "事件回复可以进记忆"

    archive["soccer_game_memory_player_interaction_enabled"] = True
    archive["soccer_game_memory_event_reply_enabled"] = False
    messages = game_router._build_game_archive_memory_messages(archive, tail_count=3)

    assert [msg["role"] for msg in messages] == ["user", "assistant", "system"]
    assert messages[0]["content"][0]["text"] == "这句不进记忆"
    assert messages[1]["content"][0]["text"] == "直接回复也不进记忆"


@pytest.mark.unit
def test_postgame_event_aligns_current_state_score_to_final_score():
    event = game_router._build_game_postgame_event(
        "soccer",
        {
            "summary": "soccer 小游戏结束。",
            "lanlan_name": "Lan",
            "finalScore": {"player": 6, "ai": 14},
            "last_state": {
                "score": {"player": 6, "ai": 10},
                "round": 17,
                "mood": "sad",
            },
            "last_full_dialogues": [],
            "memory_highlights": {},
        },
        {"max_chars": 60},
    )

    assert event["scoreText"] == "玩家 6 : 14 Lan"
    assert event["finalScore"] == {"player": 6, "ai": 14}
    assert event["currentState"]["score"] == {"player": 6, "ai": 14}
    assert event["currentState"]["round"] == 17
    assert "scoreText/finalScore" in event["request"]


@pytest.mark.unit
def test_game_archive_summary_keeps_score_not_counters():
    summary = game_router._summarize_game_archive(
        {"game_type": "soccer", "lanlan_name": "Lan", "last_state": {"score": {"player": 0, "ai": 5}}},
        [
            {"type": "game_event"},
            {"type": "user"},
            {"type": "assistant"},
        ],
    )

    assert summary == "soccer 游戏结束。最终/最近结果：玩家 0 : 5 Lan。"
    assert "本局记录了" not in summary
    assert "外部接管模式" not in summary


@pytest.mark.unit
def test_game_event_memory_line_does_not_attribute_event_text_to_user():
    line = game_router._dialog_memory_line({
        "type": "game_event",
        "kind": "goal-conceded",
        "text": "不算不算嘛",
        "result_line": "又耍赖？我都懒得防你了，随便你吧。",
    })

    assert "游戏事件 goal-conceded（玩家进球 / 猫娘丢球）" in line
    assert "事件原文「不算不算嘛」" in line
    assert "猫娘回应「又耍赖？我都懒得防你了，随便你吧。」" in line
    assert "玩家：" not in line


@pytest.mark.unit
def test_memory_highlight_source_explains_game_event_text_is_not_user_speech():
    source = game_router._build_game_archive_memory_highlight_source({
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "last_state": {"score": {"player": 1, "ai": 2}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {
                "type": "game_event",
                "kind": "goal-conceded",
                "text": "不算不算嘛",
                "result_line": "又耍赖？",
            },
        ],
    })

    assert "只有“玩家：...”行是玩家亲口说的话" in source
    assert "事件原文是游戏模块/猫娘气泡或事件标签，不要归因给玩家" in source
    assert "游戏事件 goal-conceded（玩家进球 / 猫娘丢球）" in source
    assert "固定顺序是玩家在前、当前角色在后" in source
    assert "官方结果，来源优先级为 finalScore / last_state.score" in source
    assert "口头让步、安抚或玩笑" in source


@pytest.mark.unit
def test_memory_highlight_prompt_rejects_bare_or_reversed_scores(monkeypatch):
    captured = {}

    class FakeLlm:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, messages):
            captured["system"] = messages[0].content
            captured["user"] = messages[1].content

            class Result:
                content = '{"important_records":[],"important_game_events":[]}'

            return Result()

    def fake_create_chat_llm(*_args, **_kwargs):
        return FakeLlm()

    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {
        "model": "test-model",
        "base_url": "http://example.test",
        "api_key": "key",
        "api_type": "",
    })
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    result = asyncio.run(game_router._select_game_archive_memory_highlights({
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "last_state": {"score": {"player": 0, "ai": 10}},
        "full_dialogues": [],
    }))

    assert result["important_records"] == []
    assert result["important_game_events"] == []
    assert "不要写无主体裸结果" in captured["system"]
    assert "不要前后混用不同视角" in captured["system"]
    assert "固定顺序是玩家在前、当前角色在后" in captured["user"]


@pytest.mark.unit
def test_memory_review_prompt_protects_soccer_archive_records():
    from config.prompts_memory import get_history_review_prompt

    prompt = get_history_review_prompt("zh")

    assert "游戏模块归档" in prompt
    assert "不同时间/会话的同一类游戏默认代表不同局" in prompt
    assert "不要整条删除" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_highlight_selector_uses_full_dialogue_log(monkeypatch):
    calls = []

    class _FakeLLM:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def ainvoke(self, messages):
            calls.append(messages)
            return type("Resp", (), {
                "content": '{"important_records":["保留了第一句互动"],"important_game_events":["记住了关键抢断"]}'
            })()

    def fake_create_chat_llm(*_args, **_kwargs):
        return _FakeLLM()

    monkeypatch.setattr(
        game_router,
        "_get_current_character_info",
        lambda: {
            "model": "test-model",
            "base_url": "http://llm.test",
            "api_key": "key",
            "api_type": "test",
        },
    )
    monkeypatch.setattr("utils.llm_client.create_chat_llm", fake_create_chat_llm)

    archive = {
        "game_type": "soccer",
        "session_id": "match_1",
        "lanlan_name": "Lan",
        "last_state": {"score": {"player": 0, "ai": 5}},
        "soccer_game_memory_enabled": True,
        "soccer_game_memory_player_interaction_enabled": True,
        "soccer_game_memory_event_reply_enabled": True,
        "soccer_game_memory_archive_enabled": True,
        "soccer_game_memory_postgame_context_enabled": True,
        "full_dialogues": [
            {"type": "user", "text": "第一句也要参与筛选"},
            {"type": "assistant", "line": "我记着呢。"},
            {"type": "user", "text": "最后一句"},
        ],
        "last_full_dialogues": [
            {"type": "user", "text": "最后一句"},
        ],
        "key_events": [],
    }

    highlights = await game_router._select_game_archive_memory_highlights(archive)

    assert highlights["important_records"] == ["保留了第一句互动"]
    assert highlights["important_game_events"] == ["记住了关键抢断"]
    assert "第一句也要参与筛选" in calls[0][1].content


@pytest.mark.unit
def test_route_liveness_ignores_recent_activity_when_heartbeat_is_stale():
    state = {
        "created_at": 100.0,
        "last_heartbeat_at": 110.0,
        "last_activity": 125.0,
    }

    assert game_router._route_liveness_at(state) == 110.0


@pytest.mark.unit
def test_route_liveness_uses_created_at_before_first_heartbeat():
    state = {
        "created_at": 100.0,
        "last_activity": 125.0,
    }

    assert game_router._route_liveness_at(state) == 100.0


@pytest.mark.unit
def test_route_heartbeat_timeout_uses_hidden_grace_window():
    assert game_router._route_heartbeat_timeout_seconds({"page_visible": True}) == (
        game_router._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert game_router._route_heartbeat_timeout_seconds({"page_visible": False}) == (
        game_router._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    )
    assert game_router._route_heartbeat_timeout_seconds({"visibility_state": "hidden"}) == (
        game_router._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_close_and_remove_session_closes_client():
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    key = _put_game_session("Lan", "soccer", "test_sid", fake_session)

    closed = await game_router._close_and_remove_session("soccer", "test_sid", "Lan")

    assert closed is True
    fake_session.close.assert_awaited_once()
    assert key not in game_router._game_sessions


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_returns_closed_flag_for_missing_session():
    result = await game_router.game_end("soccer", _FakeRequest({"session_id": "missing"}))

    assert result == {
        "ok": True,
        "closed": False,
        "session_id": "missing",
        "route_closed": False,
        "archive": None,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_closes_existing_session():
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    _put_game_session("Lan", "soccer", "match_1", fake_session)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "match_1"}),
    )

    assert result == {
        "ok": True,
        "closed": True,
        "session_id": "match_1",
        "route_closed": False,
        "archive": None,
    }
    fake_session.close.assert_awaited_once()


class _FakeRealtimeSession:
    def __init__(self, *, model_lower="qwen-realtime", delivered=True):
        self._model_lower = model_lower
        self.model = model_lower
        self.base_url = "https://generativelanguage.googleapis.com" if "gemini" in model_lower else "https://dashscope.aliyuncs.com"
        self._api_type = "openai"
        self._is_gemini = "gemini" in model_lower
        self._is_responding = False
        self._audio_delta_total = 0
        self._input_audio_committed_total = 0
        self._response_created_total = 0
        self._response_done_total = 0
        self._last_response_transcript = ""
        self._active_instructions = "base realtime instructions"
        self.delivered = delivered
        self.prime_context_calls = []
        self.update_session_calls = []
        self.prompt_calls = []
        self.create_response_calls = []

    async def prime_context(self, text, skipped=False):
        self.prime_context_calls.append((text, skipped))

    async def update_session(self, config):
        self.update_session_calls.append(config)
        if "instructions" in config:
            self._active_instructions = config["instructions"]

    async def prompt_ephemeral(self, *args, language="zh", qwen_manual_commit=False):
        call = {
            "language": language,
            "qwen_manual_commit": qwen_manual_commit,
        }
        if args:
            call["instruction"] = args[0]
        self.prompt_calls.append(call)
        if self.delivered:
            self._input_audio_committed_total += 1
            self._response_created_total += 1
            self._response_done_total += 1
        return self.delivered

    async def create_response(self, text):
        self.create_response_calls.append(text)


class _FakeRealtimeManager:
    def __init__(self, session):
        self.session = session
        self.is_active = True
        self.user_language = "zh-CN"
        self.current_speech_id = "previous-speech"
        self.lock = None
        self.use_tts = False
        self._speech_output_total = 0
        self.voice_nudge_calls = 0
        self.voice_nudge_kwargs = []
        self.voice_nudge_event = asyncio.Event()

    async def trigger_voice_proactive_nudge(self, **kwargs):
        self.voice_nudge_calls += 1
        self.voice_nudge_kwargs.append(kwargs)
        self.voice_nudge_event.set()
        return True


@pytest.fixture
def _fake_realtime(monkeypatch):
    import main_logic.omni_realtime_client as realtime_mod

    monkeypatch.setattr(realtime_mod, "OmniRealtimeClient", _FakeRealtimeSession)
    monkeypatch.setattr(
        game_router,
        "_get_current_character_info",
        lambda: {"lanlan_name": "Lan"},
    )

    return _FakeRealtimeSession


@pytest.mark.unit
@pytest.mark.asyncio
async def test_realtime_context_skips_gemini_prime_to_avoid_hidden_response(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gemini-2.5-flash-native-audio-preview", delivered=True)
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    result = await game_router.game_realtime_context(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "source": "game_event",
            "currentState": {"score": {"player": 1, "ai": 2}},
            "pendingItems": [{"type": "game_event", "kind": "goal-scored"}],
        }),
    )

    assert result["ok"] is True
    assert result["action"] == "skip"
    assert result["reason"] == "gemini_no_session_update"
    assert session.prime_context_calls == []
    assert session.create_response_calls == []


class _FakeGameRouteManager:
    def __init__(self):
        self.is_active = False
        self.session = None
        self.input_mode = "audio"
        self.mirrored = []
        self.assistant_mirrored = []
        self.spoken = []
        self.statuses = []
        self.user_activity_count = 0

    async def mirror_game_user_text(self, text, **kwargs):
        self.mirrored.append((text, kwargs))

    async def mirror_game_assistant_text(self, text, **kwargs):
        self.assistant_mirrored.append((text, kwargs))
        return {"ok": True, "mirrored": True, "method": "project_text_mirror"}

    async def send_user_activity(self):
        self.user_activity_count += 1

    async def speak_game_line(self, line, **kwargs):
        self.spoken.append((line, kwargs))
        return {
            "ok": True,
            "method": "project_tts",
            "speech_id": "game-speech",
            "audio_sent": True,
            "voice_source": {"provider": "project_tts"},
        }

    async def send_status(self, message):
        self.statuses.append(message)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_activates_stt_gate_when_audio_already_active(monkeypatch, _fake_realtime):
    mgr = _FakeGameRouteManager()
    mgr.is_active = True
    mgr.session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    async def fake_pregame_context(**kwargs):
        assert kwargs["neko_initiated"] is False
        return (
            game_router._default_soccer_pregame_context(initial_difficulty="lv2"),
            "fallback",
            "ai_failed",
        )

    monkeypatch.setattr(game_router, "_build_soccer_pregame_context", fake_pregame_context)

    result = await game_router.game_route_start(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "match_1"}),
    )

    assert result["ok"] is True
    state = result["state"]
    assert state["before_game_external_mode"] == "audio"
    assert state["before_game_external_active"] is True
    assert state["game_started"] is False
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert state["preGameContext"]["gameStance"] == "neutral_play"
    assert state["preGameContext"]["initialDifficulty"] == "lv2"
    assert state["pre_game_context_source"] == "fallback"
    assert state["pre_game_context_error"] == "ai_failed"
    assert "GAME_VOICE_STT_GATE_ACTIVE" in mgr.statuses[0]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_accepts_neko_invite_context(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})

    async def fake_pregame_context(**kwargs):
        assert kwargs["neko_initiated"] is True
        assert kwargs["neko_invite_text"] == "来踢球吧，玩家。"
        return (
            {
                **game_router._default_soccer_pregame_context(initial_difficulty="lv3"),
                "launchIntent": "neko_invite",
                "openingLine": "看我这一脚",
            },
            "ai",
            "",
        )

    monkeypatch.setattr(game_router, "_build_soccer_pregame_context", fake_pregame_context)

    result = await game_router.game_route_start(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "nekoInitiated": True,
            "nekoInviteText": "来踢球吧，玩家。",
            "gameMemoryTailCount": 3,
        }),
    )

    assert result["ok"] is True
    state = result["state"]
    assert state["nekoInitiated"] is True
    assert state["nekoInviteText"] == "来踢球吧，玩家。"
    assert state["preGameContext"]["launchIntent"] == "neko_invite"
    assert state["preGameContext"]["initialDifficulty"] == "lv3"
    assert state["preGameContext"]["openingLine"] == "看我这一脚"
    assert state["pre_game_context_source"] == "ai"
    assert state["pre_game_context_error"] == ""
    assert state["game_memory_tail_count"] == 3
    assert state["soccer_game_memory_enabled"] is False
    assert state["soccer_game_memory_player_interaction_enabled"] is False
    assert state["soccer_game_memory_event_reply_enabled"] is False
    assert state["soccer_game_memory_archive_enabled"] is False
    assert state["soccer_game_memory_postgame_context_enabled"] is False
    assert state["game_memory_enabled"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_start_finalizes_old_active_route_before_replacing(monkeypatch):
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    game_router._game_sessions[game_router._game_session_key("Lan", "soccer", "old_match")] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": game_router.time.time(),
        "lock": None,
    }
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    old_state = game_router._activate_game_route("soccer", "old_match", "Lan")
    _set_soccer_game_memory_policy(old_state, enabled=True)
    _mark_game_started(old_state)

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_pregame_context(**_kwargs):
        return (
            game_router._default_soccer_pregame_context(initial_difficulty="lv2"),
            "fallback",
            "",
        )

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)
    monkeypatch.setattr(game_router, "_build_soccer_pregame_context", fake_pregame_context)

    result = await game_router.game_route_start(
        "soccer",
        _FakeRequest({"lanlan_name": "Lan", "session_id": "new_match"}),
    )

    assert result["ok"] is True
    assert result["state"]["session_id"] == "new_match"
    assert old_state["game_route_active"] is False
    assert old_state["exit_reason"] == "superseded_by_route_start"
    assert submitted[0]["session_id"] == "old_match"
    assert submitted[0]["exit_reason"] == "superseded_by_route_start"
    fake_session.close.assert_awaited_once()
    assert game_router._game_route_states[game_router._route_state_key("Lan", "soccer")]["session_id"] == "new_match"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_text_to_game_llm_defers_voice_to_frontend_arbiter(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    state["last_state"] = {
        "round": 3,
        "mood": "happy",
        "difficulty": "lv2",
        "score": {"player": 1, "ai": 4},
    }

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "user-text"
        assert event["userText"] == "你是不是在放水？"
        assert event["scoreDiff"] == 3
        return {
            "line": "才没有放水呢。",
            "control": {"mood": "happy"},
            "llm_source": {"provider": "fake"},
        }

    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    handled = await game_router.route_external_stream_message(
        "Lan",
        {"input_type": "text", "data": "你是不是在放水？", "request_id": "req-1"},
    )

    assert handled is True
    assert state["game_external_text_route_active"] is True
    assert state["game_input_mode"] == "text"
    assert state["activation_source"] == "external_text_hijacked_by_game"
    assert mgr.mirrored == [("你是不是在放水？", {
        "request_id": "req-1",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "external_text_route",
        "input_type": "game_text_no_memory",
        "send_to_frontend": False,
    })]
    assert mgr.user_activity_count == 1
    assert mgr.spoken == []
    assert [output["type"] for output in state["pending_outputs"]] == ["game_external_input", "game_llm_result"]
    assert state["pending_outputs"][0]["meta"]["inputText"] == "你是不是在放水？"
    assert state["pending_outputs"][1]["meta"]["voiceAlreadyHandled"] is False
    assert state["pending_outputs"][1]["result"]["line"] == "才没有放水呢。"
    assert [item["type"] for item in state["game_dialog_log"]] == ["user", "assistant"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_text_uses_no_memory_input_type_when_game_memory_disabled(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})

    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=False)

    async def fake_run_game_chat(game_type, session_id, event):
        assert event["kind"] == "user-text"
        assert event["soccerGameMemoryPlayerInteractionEnabled"] is False
        return {"line": "这句只在本局里回应。", "control": {}, "llm_source": {"provider": "fake"}}

    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    handled = await game_router.route_external_stream_message(
        "Lan",
        {"input_type": "text", "data": "这局不要记", "request_id": "req-no-memory"},
    )

    assert handled is True
    assert mgr.mirrored == [("这局不要记", {
        "request_id": "req-no-memory",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "external_text_route",
        "input_type": "game_text_no_memory",
        "send_to_frontend": False,
    })]
    assert state["pending_outputs"][0]["meta"]["soccerGameMemoryPlayerInteractionEnabled"] is False
    assert state["pending_outputs"][1]["meta"]["soccerGameMemoryPlayerInteractionEnabled"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_audio_activates_game_stt_gate(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    handled = await game_router.route_external_stream_message("Lan", {"input_type": "audio", "data": [0, 1]})
    handled_again = await game_router.route_external_stream_message("Lan", {"input_type": "audio", "data": [2, 3]})
    for idx in range(40):
        assert await game_router.route_external_stream_message(
            "Lan",
            {"input_type": "audio", "data": [idx]},
        ) is True

    assert handled is True
    assert handled_again is True
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert state["activation_source"] == "external_voice_hijacked_by_game"
    assert "GAME_VOICE_STT_GATE_ACTIVE" in mgr.statuses[0]
    assert len(mgr.statuses) == 1
    assert len(state["game_input_activation_log"]) == 1
    assert state["game_input_activation_log"][0]["source"] == "external_voice_hijacked_by_game"
    assert state["game_input_activation_log"][0]["mode"] == "voice"
    assert state["game_input_activation_log"][0]["detail"] == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_external_voice_transcript_to_game_llm(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "user-voice"
        assert event["userVoiceText"] == "我马上要进球了"
        return {
            "line": "那我可要认真防你啦。",
            "control": {"difficulty": "max"},
            "llm_source": {"provider": "fake"},
        }

    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    handled = await game_router.route_external_voice_transcript(
        "Lan",
        "我马上要进球了",
        request_id="voice-1",
        game_type="soccer",
        session_id="match_1",
    )

    assert handled is True
    assert state["game_external_voice_route_active"] is True
    assert state["game_input_mode"] == "voice"
    assert mgr.mirrored == [("我马上要进球了", {
        "request_id": "voice-1",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "external_voice_route",
        "input_type": "game_voice_transcript_no_memory",
        "send_to_frontend": True,
    })]
    assert mgr.user_activity_count == 1
    assert mgr.spoken == []
    assert [output["type"] for output in state["pending_outputs"]] == ["game_external_input", "game_llm_result"]
    assert state["pending_outputs"][0]["meta"]["inputText"] == "我马上要进球了"
    assert state["pending_outputs"][1]["meta"]["kind"] == "user-voice"
    assert state["pending_outputs"][1]["meta"]["hasUserSpeech"] is True
    assert "skipOrdinaryMemory" not in state["pending_outputs"][1]["meta"]
    assert state["pending_outputs"][1]["meta"]["voiceAlreadyHandled"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_refreshes_last_state(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    before = state["last_heartbeat_at"]

    result = await game_router.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "currentState": {"score": {"player": 3, "ai": 2}},
            "gameStarted": True,
            "gameStartedElapsedMs": 15_000,
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert state["last_heartbeat_at"] >= before
    assert state["last_state"] == {"score": {"player": 3, "ai": 2}}
    assert result["heartbeat_timeout_seconds"] == game_router._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    assert state["page_visible"] is True
    assert state["visibility_state"] == "visible"
    assert state["game_started"] is True
    assert state["game_started_elapsed_ms"] == 15_000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_heartbeat_records_hidden_visibility(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    result = await game_router.game_route_heartbeat(
        "soccer",
        _FakeRequest({
            "lanlan_name": "Lan",
            "session_id": "match_1",
            "pageVisible": False,
            "visibilityState": "hidden",
        }),
    )

    assert result["ok"] is True
    assert result["active"] is True
    assert result["heartbeat_timeout_seconds"] == game_router._GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS
    assert state["page_visible"] is False
    assert state["visibility_state"] == "hidden"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_timeout_finalize_archives_and_closes_session(monkeypatch):
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    _put_game_session("Lan", "soccer", "match_1", fake_session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router._finalize_game_route_state(
        state,
        reason="heartbeat_timeout",
        close_game_session=True,
    )

    assert state["game_route_active"] is False
    assert state["heartbeat_enabled"] is False
    assert state["exit_reason"] == "heartbeat_timeout"
    assert result["game_session_closed"] is True
    assert result["archive"]["exit_reason"] == "heartbeat_timeout"
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert submitted[0]["exit_reason"] == "heartbeat_timeout"
    fake_session.close.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_timeout_ignores_recent_activity_and_finalizes(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    now = game_router.time.time()
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    state["last_heartbeat_at"] = now - game_router._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS - 1.0
    state["last_activity"] = now

    assert game_router._route_heartbeat_expired(state, now) is True

    result = await game_router._finalize_game_route_state(
        state,
        reason="heartbeat_timeout",
        close_game_session=False,
    )

    assert state["game_route_active"] is False
    assert state["heartbeat_enabled"] is False
    assert state["exit_reason"] == "heartbeat_timeout"
    assert result["archive"]["exit_reason"] == "heartbeat_timeout"


@pytest.mark.unit
def test_heartbeat_timeout_keeps_fresh_heartbeat_despite_old_activity():
    now = game_router.time.time()
    state = {
        "created_at": now - 600.0,
        "last_heartbeat_at": now - 1.0,
        "last_activity": now - game_router._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS - 20.0,
        "page_visible": True,
    }

    assert game_router._route_heartbeat_expired(state, now) is False


@pytest.mark.unit
def test_heartbeat_timeout_uses_created_at_before_first_heartbeat():
    now = game_router.time.time()
    timeout = game_router._GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    state = {
        "created_at": now - timeout + 1.0,
        "last_activity": now,
        "page_visible": True,
    }

    assert game_router._route_heartbeat_expired(state, now) is False

    state["created_at"] = now - timeout - 1.0
    assert game_router._route_heartbeat_expired(state, now) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heartbeat_timeout_without_start_skips_only_game_archive_memory(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "opening_line",
        "line": "准备好了吗",
    })

    async def fake_submit(_archive):
        raise AssertionError("pre-start heartbeat timeout should not write game archive memory")

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router._finalize_game_route_state(
        state,
        reason="heartbeat_timeout",
        close_game_session=False,
    )

    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "game_not_started"
    assert result["archive"]["memory_skipped"] is True
    assert result["archive"]["last_full_dialogues"][0]["line"] == "准备好了吗"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_memory_disabled_skips_archive_memory(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    _set_soccer_game_memory_policy(state, enabled=False)
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "这局别进记忆",
    })

    async def fake_submit(_archive):
        raise AssertionError("disabled game memory should not submit archive payload")

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router._finalize_game_route_state(
        state,
        reason="manual",
        close_game_session=False,
    )

    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "soccer_game_memory_archive_disabled"
    assert result["archive"]["game_memory_enabled"] is False
    assert result["archive"]["memory_skipped"] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_uses_manager_project_tts(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_speak(
        "soccer",
        _FakeRequest({"line": "换我进攻了", "session_id": "match_1", "request_id": "req-2"}),
    )

    assert result["ok"] is True
    assert result["method"] == "project_tts"
    assert result["voice_source"]["provider"] == "project_tts"
    assert mgr.spoken == [("换我进攻了", {
        "request_id": "req-2",
        "game_type": "soccer",
        "session_id": "match_1",
        "mirror_text": True,
        "emit_turn_end": True,
        "interrupt_audio": False,
        "event": {},
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_can_skip_text_mirror_for_frontend_arbiter(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_speak(
        "soccer",
        _FakeRequest({
            "line": "只播放语音",
            "session_id": "match_1",
            "request_id": "req-voice",
            "mirror_text": False,
            "emit_turn_end": False,
        }),
    )

    assert result["ok"] is True
    assert mgr.spoken == [("只播放语音", {
        "request_id": "req-voice",
        "game_type": "soccer",
        "session_id": "match_1",
        "mirror_text": False,
        "emit_turn_end": False,
        "interrupt_audio": False,
        "event": {},
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_speak_forwards_interrupt_audio(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_speak(
        "soccer",
        _FakeRequest({
            "line": "先听我说完",
            "session_id": "match_1",
            "request_id": "req-interrupt",
            "mirror_text": False,
            "emit_turn_end": False,
            "interrupt_audio": True,
        }),
    )

    assert result["ok"] is True
    assert mgr.spoken == [("先听我说完", {
        "request_id": "req-interrupt",
        "game_type": "soccer",
        "session_id": "match_1",
        "mirror_text": False,
        "emit_turn_end": False,
        "interrupt_audio": True,
        "event": {},
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_uses_text_only_mirror(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "文字先进入主聊天窗",
            "session_id": "match_1",
            "request_id": "req-mirror",
            "turn_id": "turn-mirror",
            "source": "game-llm-result",
        }),
    )

    assert result["ok"] is True
    assert result["method"] == "project_text_mirror"
    assert mgr.assistant_mirrored == [("文字先进入主聊天窗", {
        "request_id": "req-mirror",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "game-llm-result",
        "turn_id": "turn-mirror",
        "event": {},
        "finalize_turn": False,
    })]
    assert mgr.spoken == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_finalizes_user_reply_by_default(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})

    result = await game_router.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "听见啦，我会放慢一点。",
            "session_id": "match_1",
            "request_id": "req-user-reply",
            "source": "game-llm-result",
            "event": {
                "kind": "user-text",
                "hasUserText": True,
            },
        }),
    )

    assert result["ok"] is True
    assert mgr.assistant_mirrored == [("听见啦，我会放慢一点。", {
        "request_id": "req-user-reply",
        "game_type": "soccer",
        "session_id": "match_1",
        "source": "game-llm-result",
        "turn_id": None,
        "event": {
            "kind": "user-text",
            "hasUserText": True,
        },
        "finalize_turn": True,
    })]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_project_mirror_assistant_records_opening_line_in_game_log(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_get_current_character_info", lambda: {"lanlan_name": "Lan"})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")

    result = await game_router.game_project_mirror_assistant(
        "soccer",
        _FakeRequest({
            "line": "看我这一脚",
            "session_id": "match_1",
            "request_id": "opening-1",
            "source": "game-llm-result",
            "event": {
                "kind": "opening-line",
                "hasUserSpeech": False,
                "hasUserText": False,
            },
        }),
    )

    assert result["ok"] is True
    assert mgr.assistant_mirrored[0][0] == "看我这一脚"
    mirror_kwargs = mgr.assistant_mirrored[0][1]
    assert mirror_kwargs["request_id"] == "opening-1"
    assert mirror_kwargs["game_type"] == "soccer"
    assert mirror_kwargs["session_id"] == "match_1"
    assert mirror_kwargs["source"] == "game-llm-result"
    assert mirror_kwargs["turn_id"] is None
    assert mirror_kwargs["finalize_turn"] is False
    assert mirror_kwargs["event"]["kind"] == "opening-line"
    assert mirror_kwargs["event"]["hasUserSpeech"] is False
    assert mirror_kwargs["event"]["hasUserText"] is False
    assert mirror_kwargs["event"]["soccerGameMemoryEventReplyEnabled"] is False
    assert mirror_kwargs["event"]["soccer_game_memory_event_reply_enabled"] is False
    assert state["game_dialog_log"] == [{
        "id": "glog_0001",
        "type": "assistant",
        "source": "opening_line",
        "kind": "opening-line",
        "line": "看我这一脚",
        "request_id": "opening-1",
        "ts": state["game_dialog_log"][0]["ts"],
    }]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_archives_active_route_to_memory(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _mark_game_started(state)
    state["last_state"] = {
        "score": {"player": 2, "ai": 5},
    }
    state["preGameContext"] = {
        **game_router._default_soccer_pregame_context(initial_difficulty="lv2"),
        "gameStance": "soft_teasing",
    }
    state["pre_game_context_source"] = "ai"
    state["pre_game_context_error"] = ""
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "你是不是在放水？",
    })
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "才没有放水呢。",
        "control": {"mood": "happy"},
    })

    submitted = []

    async def fake_submit(archive):
        submitted.append(archive)
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "currentState": {"score": {"player": 3, "ai": 6}, "round": 9},
            "gameMemoryTailCount": 4,
            "gameMemoryEnabled": True,
            "gameStarted": True,
            "gameStartedElapsedMs": 15_000,
        }),
    )

    assert result["route_closed"] is True
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert result["archive"]["summary"].startswith("soccer 游戏结束")
    assert "待接入 memory_server" not in result["archive"]["summary"]
    assert result["archive"]["preGameContext"]["gameStance"] == "soft_teasing"
    assert result["archive"]["pre_game_context_source"] == "ai"
    assert result["archive"]["finalScore"] == {"player": 3, "ai": 6}
    assert result["archive"]["game_memory_tail_count"] == 4
    assert submitted[0]["last_full_dialogues"][-1]["line"] == "才没有放水呢。"
    assert submitted[0]["preGameContext"]["initialDifficulty"] == "lv2"
    assert state["game_route_active"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_game_archive_when_game_never_started(monkeypatch):
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "opening_line",
        "line": "准备好了吗",
    })

    async def fake_submit(_archive):
        raise AssertionError("accidental pre-start entry should not write game archive memory")

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "reason": "accidental_page_entry",
            "gameStarted": False,
            "accidentalGameEntry": True,
        }),
    )

    assert result["route_closed"] is True
    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "accidental_page_entry"
    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}
    assert result["archive"]["memory_skipped"] is True
    assert result["archive"]["last_full_dialogues"][0]["source"] == "opening_line"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_under_10s_skips_archive_without_suppressing_user_reply_memory(monkeypatch):
    mgr = _FakeGameRouteManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state, elapsed_ms=5_000)

    async def fake_run_game_chat(_game_type, _session_id, event):
        assert event["kind"] == "user-voice"
        assert "skipOrdinaryMemory" not in event
        return {"line": "先热身一下。", "control": {}, "llm_source": {"provider": "fake"}}

    async def fake_submit(_archive):
        raise AssertionError("too-short game should not write game archive memory")

    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)
    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    handled = await game_router.route_external_voice_transcript(
        "Lan",
        "刚开始吗？",
        request_id="voice-grace",
        game_type="soccer",
        session_id="match_1",
    )

    assert handled is True
    assert state["pending_outputs"][0]["meta"]["hasUserSpeech"] is True
    assert "skipOrdinaryMemory" not in state["pending_outputs"][0]["meta"]
    assert state["pending_outputs"][1]["meta"]["hasUserSpeech"] is True
    assert "skipOrdinaryMemory" not in state["pending_outputs"][1]["meta"]

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({
            "session_id": "match_1",
            "lanlan_name": "Lan",
            "reason": "manual_return_to_start",
            "gameStarted": True,
            "gameStartedElapsedMs": 9_000,
        }),
    )

    assert result["archive_memory"]["status"] == "skipped"
    assert result["archive_memory"]["reason"] == "started_under_10s"
    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_injects_postgame_context_into_active_realtime(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="qwen-realtime", delivered=True)
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    monkeypatch.setattr(game_router, "_POSTGAME_REALTIME_NUDGE_DELAYS", (0.0,))
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 1, "ai": 3}}
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_voice_route",
        "text": "我是不是不适合玩这个？",
    })
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "别认输嘛，再来一脚。",
        "control": {"mood": "relaxed"},
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "realtime"
    assert result["postgame"]["context_injected"] is True
    assert result["postgame"]["nudge_scheduled"] is True
    await asyncio.wait_for(mgr.voice_nudge_event.wait(), timeout=1.0)
    assert mgr.voice_nudge_calls == 1
    assert mgr.voice_nudge_kwargs[0]["qwen_manual_commit"] is True
    assert "游戏模块赛后主动搭话" in mgr.voice_nudge_kwargs[0]["instruction"]
    assert "不要继续扮演游戏仍在进行" in mgr.voice_nudge_kwargs[0]["instruction"]
    assert session.prime_context_calls
    context_text, skipped = session.prime_context_calls[0]
    assert skipped is True
    assert "游戏模块赛后上下文" in context_text
    assert "我是不是不适合玩这个？" in context_text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_uses_direct_response_for_gemini_postgame(monkeypatch, _fake_realtime):
    session = _fake_realtime(model_lower="gemini-2.5-flash-native-audio-preview", delivered=True)
    mgr = _FakeRealtimeManager(session)
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 3, "ai": 14}}
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_voice_route",
        "text": "哇,你是笨蛋。",
    })
    game_router._append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": "十二比三，帅的是我。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "realtime"
    assert result["postgame"]["action"] == "direct_response"
    assert result["postgame"]["reason"] == "gemini_direct_response"
    assert session.prime_context_calls == []
    assert session.prompt_calls == []
    assert mgr.voice_nudge_calls == 0
    assert len(session.create_response_calls) == 1
    assert "游戏模块赛后上下文" in session.create_response_calls[0]
    assert "游戏模块赛后主动搭话" in session.create_response_calls[0]
    assert "不要继续扮演游戏仍在进行" in session.create_response_calls[0]


class _FakePostgameState:
    def __init__(self):
        self.events = []

    async def fire(self, event, **kwargs):
        self.events.append((event, kwargs))


class _FakePostgameTextManager:
    def __init__(self):
        self.is_active = False
        self.session = None
        self.current_speech_id = "postgame-sid"
        self.state = _FakePostgameState()
        self.prepare_calls = []
        self.feed_tts_calls = []
        self.finish_calls = []

    async def prepare_proactive_delivery(self, **kwargs):
        self.prepare_calls.append(kwargs)
        return True

    async def finish_proactive_delivery(self, text, **kwargs):
        self.finish_calls.append((text, kwargs))
        return True

    async def feed_tts_chunk(self, text, **kwargs):
        self.feed_tts_calls.append((text, kwargs))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_delivers_one_shot_postgame_text_bubble(monkeypatch):
    mgr = _FakePostgameTextManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 2, "ai": 4}}
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "我好像踢不进去。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "postgame"
        assert event["lastUserText"] == "我好像踢不进去。"
        assert event["scoreText"] == "玩家 2 : 4 Lan"
        return {
            "line": "刚才那局不算，我下次慢点陪你踢。",
            "llm_source": {"provider": "fake"},
        }

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)
    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual"}),
    )

    assert result["postgame"]["mode"] == "text"
    assert result["postgame"]["action"] == "chat"
    assert result["postgame"]["line"] == "刚才那局不算，我下次慢点陪你踢。"
    assert result["postgame"]["tts_fed"] is True
    assert mgr.prepare_calls == [{"min_idle_secs": 0.0}]
    assert mgr.feed_tts_calls == [("刚才那局不算，我下次慢点陪你踢。", {
        "expected_speech_id": "postgame-sid",
    })]
    assert mgr.finish_calls == [("刚才那局不算，我下次慢点陪你踢。", {
        "expected_speech_id": "postgame-sid",
    })]
    assert any(getattr(event, "name", "") == "PROACTIVE_PHASE2" for event, _ in mgr.state.events)
    assert any(getattr(event, "name", "") == "PROACTIVE_DONE" for event, _ in mgr.state.events)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_route_end_uses_full_game_end_contract(monkeypatch):
    mgr = _FakePostgameTextManager()
    fake_session = type("FakeSession", (), {"close": AsyncMock()})()
    game_router._game_sessions[game_router._game_session_key("Lan", "soccer", "match_1")] = {
        "session": fake_session,
        "reply_chunks": [],
        "last_activity": game_router.time.time(),
        "lock": None,
    }
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _set_soccer_game_memory_policy(state, enabled=True)
    _mark_game_started(state)
    state["last_state"] = {"score": {"player": 1, "ai": 2}}
    game_router._append_game_dialog(state, {
        "type": "user",
        "source": "external_text_route",
        "text": "再来一球就追上了。",
    })

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(game_type, session_id, event):
        assert game_type == "soccer"
        assert session_id == "match_1"
        assert event["kind"] == "postgame"
        assert event["lastUserText"] == "再来一球就追上了。"
        return {"line": "刚才那脚挺像样的。", "llm_source": {"provider": "fake"}}

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)
    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    result = await game_router.game_route_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan"}),
    )

    assert result["ok"] is True
    assert result["closed"] is True
    assert result["route_closed"] is True
    assert result["archive"]["exit_reason"] == "route_end"
    assert result["archive_memory"] == {"ok": True, "status": "cached", "count": 1}
    assert result["postgame"]["mode"] == "text"
    assert result["postgame"]["action"] == "chat"
    assert result["postgame"]["line"] == "刚才那脚挺像样的。"
    assert mgr.finish_calls == [("刚才那脚挺像样的。", {
        "expected_speech_id": "postgame-sid",
    })]
    fake_session.close.assert_awaited_once()
    assert state["game_route_active"] is False
    assert state["exit_reason"] == "route_end"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_postgame_on_heartbeat_timeout(monkeypatch):
    mgr = _FakePostgameTextManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _mark_game_started(state)

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("postgame should not run during heartbeat timeout")

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)
    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "heartbeat_timeout"}),
    )

    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}
    assert mgr.prepare_calls == []
    assert state["exit_reason"] == "heartbeat_timeout"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_game_end_skips_postgame_on_manual_return_to_start(monkeypatch):
    mgr = _FakePostgameTextManager()
    monkeypatch.setattr(game_router, "get_session_manager", lambda: {"Lan": mgr})
    state = game_router._activate_game_route("soccer", "match_1", "Lan")
    _mark_game_started(state)

    async def fake_submit(archive):
        return {"ok": True, "status": "cached", "count": 1}

    async def fake_run_game_chat(*_args, **_kwargs):
        raise AssertionError("return-to-start should only archive, not deliver postgame")

    monkeypatch.setattr(game_router, "_submit_game_archive_to_memory", fake_submit)
    monkeypatch.setattr(game_router, "_run_game_chat", fake_run_game_chat)

    result = await game_router.game_end(
        "soccer",
        _FakeRequest({"session_id": "match_1", "lanlan_name": "Lan", "reason": "manual_return_to_start"}),
    )

    assert result["postgame"] == {"ok": True, "action": "skip", "reason": "disabled"}
    assert mgr.prepare_calls == []
    assert state["exit_reason"] == "manual_return_to_start"
