# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import importlib
import sys
from unittest.mock import MagicMock, patch


CHARACTER_DATA = {
    "主人": {"昵称": "主人"},
    "猫娘": {"测试猫娘": {"昵称": "猫娘"}},
    "当前猫娘": "测试猫娘",
}


def _build_mock_config_manager(tmpdir: str):
    mock = MagicMock()
    mock.memory_dir = tmpdir
    mock.get_character_data.return_value = (
        "主人", "测试猫娘",
        CHARACTER_DATA["主人"],
        CHARACTER_DATA["猫娘"],
        {"human": "主人", "system": "SYSTEM_MESSAGE"},
        {}, {}, {}, {},
    )
    return mock


def test_negative_signal_first_soft_then_hard() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            first = pm.register_negative_signal("测试猫娘", "工作这个话题真的好烦")
            assert first["matched"] is True
            assert first["topic"] == "工作"
            assert first["policy"] == "de_emphasize"
            assert "减少对这个话题的提及" in first["response_instruction"]

            second = pm.register_negative_signal("测试猫娘", "工作真的烦死了")
            assert second["policy"] == "avoid"
            assert "不要继续展开这个话题" in second["response_instruction"]

            fresh_pm = PersonaManager()
            fresh_pm._config_manager = mock_cm
            persona = fresh_pm.get_persona("测试猫娘")
            guidance = persona["_topic_guidance"]
            assert guidance["soft_avoid"] == []
            assert guidance["hard_avoid"][0]["topic"] == "工作"


def test_negative_signal_explicit_avoid_immediately_hard() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "别提考试了，我现在很难受")
            assert result["matched"] is True
            assert result["topic"] == "考试"
            assert result["policy"] == "avoid"

            fresh_pm = PersonaManager()
            fresh_pm._config_manager = mock_cm
            md = fresh_pm.render_persona_markdown("测试猫娘")
            assert "不要主动提及的话题" in md
            assert "考试" in md


def test_negative_signal_topicless_emotion_falls_back_to_tone_only() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "我好焦虑")
            assert result["matched"] is True
            assert result["topic"] == ""
            assert result["policy"] == "tone_only"

            persona = pm.get_persona("测试猫娘")
            guidance = persona.get("_topic_guidance", {})
            assert guidance.get("soft_avoid", []) == []
            assert guidance.get("hard_avoid", []) == []


def test_contains_negative_signal_keyword_gate() -> None:
    from memory.persona import contains_negative_signal

    assert contains_negative_signal("讲道理，你知道我不喜欢就别提及了嘛") is True
    assert contains_negative_signal("我不喜欢昆虫食品") is True
    assert contains_negative_signal("你记住了，不要日本动漫") is True
    assert contains_negative_signal("今天吃什么好呀") is False


def test_skip_recent_ai_message_if_user_immediately_rejects_it() -> None:
    mock_cm = _build_mock_config_manager("/tmp")
    with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
         patch("utils.config_manager._config_manager", mock_cm):
        sys.modules.pop("memory_server", None)
        memory_server = importlib.import_module("memory_server")

        brackets_pattern = memory_server.re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
        messages = [
            type("Msg", (), {"type": "ai", "content": "[20260408 Wed 09:58]中餐、日料、意大利面都可以试试"})(),
            type("Msg", (), {"type": "human", "content": "不是跟你说过我不喜欢日本菜么"})(),
            type("Msg", (), {"type": "ai", "content": "[20260408 Wed 09:59]那意大利菜可以考虑"})(),
        ]

        skip_indexes = memory_server._get_recent_prompt_skip_indexes(messages, ["日本菜"], brackets_pattern)
        assert 0 in skip_indexes
        assert 2 not in skip_indexes


def test_negative_review_runs_after_review_history_with_raw_messages() -> None:
    mock_cm = _build_mock_config_manager("/tmp")
    with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
         patch("utils.config_manager._config_manager", mock_cm):
        sys.modules.pop("memory_server", None)
        memory_server = importlib.import_module("memory_server")

        events: list[tuple] = []
        review_messages = [type("Msg", (), {"type": "human", "content": "不要日本动漫"})()]

        async def fake_review_history(name, cancel_event):
            events.append(("review_history", name, cancel_event.is_set()))

        async def fake_review_negative_preferences(messages, name):
            events.append(("negative_review", name, messages))
            return 1

        with patch.object(memory_server.recent_history_manager, "review_history", side_effect=fake_review_history), \
             patch.object(memory_server, "_review_and_apply_negative_preferences", side_effect=fake_review_negative_preferences):
            asyncio.run(memory_server._run_review_in_background("测试猫娘", review_messages))

        assert events[0] == ("review_history", "测试猫娘", False)
        assert events[1] == ("negative_review", "测试猫娘", review_messages)


def test_negative_signal_explicit_avoid_uses_referenced_topic() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal(
                "测试猫娘",
                "别提了",
                referenced_topic="那我们继续聊工作上的安排吧",
            )
            assert result["matched"] is True
            assert result["topic"] == "工作上的安排"
            assert result["policy"] == "avoid"


def test_negative_signal_explicit_avoid_uses_english_referenced_topic() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal(
                "测试猫娘",
                "别提了",
                referenced_topic="Let's talk about Work later.",
            )
            assert result["matched"] is True
            assert result["topic"] == "work"
            assert result["policy"] == "avoid"


def test_negative_signal_explicit_avoid_targets_negative_clause_in_reference() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal(
                "测试猫娘",
                "讲道理，你知道我不喜欢就别提及了嘛",
                referenced_topic="寿司、生鱼片、家常菜、甜点。你喜欢哪种？不过昆虫食品你应该不会喜欢。",
            )
            assert result["matched"] is True
            assert result["topic"] == "昆虫食品"
            assert result["policy"] == "avoid"


def test_negative_signal_direct_dislike_immediately_hard() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "我不喜欢昆虫食品")
            assert result["matched"] is True
            assert result["topic"] == "昆虫食品"
            assert result["policy"] == "avoid"

            fresh_pm = PersonaManager()
            fresh_pm._config_manager = mock_cm
            persona = fresh_pm.get_persona("测试猫娘")
            guidance = persona["_topic_guidance"]
            assert guidance["soft_avoid"] == []
            assert guidance["hard_avoid"][0]["topic"] == "昆虫食品"


def test_negative_signal_direct_generic_avoid_phrase() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "不要日本动漫")
            assert result["matched"] is True
            assert result["topic"] == "日本动漫"
            assert result["policy"] == "avoid"


def test_apply_negative_preference_review_persists_topic_guidance() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.apply_negative_preference_review(
                "测试猫娘",
                topic="昆虫食品",
                policy="avoid",
            )
            assert result["matched"] is True
            assert result["topic"] == "昆虫食品"
            assert result["policy"] == "avoid"

            fresh_pm = PersonaManager()
            fresh_pm._config_manager = mock_cm
            persona = fresh_pm.get_persona("测试猫娘")
            guidance = persona["_topic_guidance"]
            assert guidance["soft_avoid"] == []
            assert guidance["hard_avoid"][0]["topic"] == "昆虫食品"


def test_negative_signal_english_topic_detection() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            first = pm.register_negative_signal("测试猫娘", "Work is annoying")
            assert first["matched"] is True
            assert first["topic"] == "work"
            assert first["policy"] == "de_emphasize"

            second = pm.register_negative_signal("测试猫娘", "don't mention work anymore")
            assert second["matched"] is True
            assert second["topic"] == "work"
            assert second["policy"] == "avoid"

            fresh_pm = PersonaManager()
            fresh_pm._config_manager = mock_cm
            persona = fresh_pm.get_persona("测试猫娘")
            guidance = persona["_topic_guidance"]
            assert guidance["soft_avoid"] == []
            assert guidance["hard_avoid"][0]["topic"] == "work"
