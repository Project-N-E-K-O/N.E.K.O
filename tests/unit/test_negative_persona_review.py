# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
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


def test_negative_signal_english_topic_detection() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            first = pm.register_negative_signal("测试猫娘", "work is annoying")
            assert first["matched"] is True
            assert first["topic"] == "work"
            assert first["policy"] == "de_emphasize"

            second = pm.register_negative_signal("测试猫娘", "don't mention work anymore")
            assert second["matched"] is True
            assert second["topic"] == "work"
            assert second["policy"] == "avoid"
