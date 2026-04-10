# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib
import json
import sys
import tempfile
import types
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


@contextmanager
def _import_memory_server_with_tempdir():
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            original_module = sys.modules.get("memory_server")
            sys.modules.pop("memory_server", None)
            memory_server = importlib.import_module("memory_server")
            try:
                yield memory_server, mock_cm
            finally:
                if original_module is None:
                    sys.modules.pop("memory_server", None)
                else:
                    sys.modules["memory_server"] = original_module


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
    assert contains_negative_signal("this sucks") is True
    assert contains_negative_signal("that feature is horrible") is True
    assert contains_negative_signal("I am so frustratingly tired") is False
    assert contains_negative_signal("今天吃什么好呀") is False


def test_skip_recent_ai_message_if_user_immediately_rejects_it() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        brackets_pattern = memory_server.re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
        messages = [
            type("Msg", (), {"type": "ai", "content": "[20260408 Wed 09:58]中餐、日料、意大利面都可以试试"})(),
            type("Msg", (), {"type": "human", "content": "不是跟你说过别再提日本菜么"})(),
            type("Msg", (), {"type": "ai", "content": "[20260408 Wed 09:59]那意大利菜可以考虑"})(),
        ]

        skip_indexes = memory_server._get_recent_prompt_skip_indexes(messages, ["日本菜"], brackets_pattern)
        assert 0 in skip_indexes
        assert 2 not in skip_indexes


def test_skip_recent_ai_message_not_filtered_by_tone_only_negative_signal() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        brackets_pattern = memory_server.re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
        messages = [
            type("Msg", (), {"type": "ai", "content": "[20260408 Wed 10:01]要不要继续聊工作安排"})(),
            type("Msg", (), {"type": "human", "content": "我好焦虑"})(),
        ]

        skip_indexes = memory_server._get_recent_prompt_skip_indexes(messages, [], brackets_pattern)
        assert 0 not in skip_indexes


def test_skip_recent_ai_message_requires_same_topic_refusal() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        brackets_pattern = memory_server.re.compile(r'(\[.*?\]|\(.*?\)|（.*?）|【.*?】|\{.*?\}|<.*?>)')
        messages = [
            type("Msg", (), {"type": "ai", "content": "Let's talk about work tomorrow."})(),
            type("Msg", (), {"type": "human", "content": "don't mention art anymore"})(),
        ]

        skip_indexes = memory_server._get_recent_prompt_skip_indexes(messages, [], brackets_pattern)
        assert 0 not in skip_indexes


def test_skip_recent_ai_message_matches_hard_topic_case_insensitively() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        message = type("Msg", (), {"type": "ai", "content": "Let's talk about Work tomorrow."})()
        assert memory_server._should_skip_recent_message_for_prompt(message, ["work"], "Let's talk about Work tomorrow.") is True


def test_skip_recent_ai_message_does_not_match_latin_substring_false_positive() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        message = type("Msg", (), {"type": "ai", "content": "We can talk about party plans later."})()
        assert memory_server._should_skip_recent_message_for_prompt(message, ["art"], "We can talk about party plans later.") is False


def test_negative_review_runs_after_review_history_with_raw_messages() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
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


def test_negative_review_uses_recent_history_when_increment_is_empty() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        events: list[tuple] = []
        fallback_messages = [type("Msg", (), {"type": "human", "content": "不要日本动漫"})()]

        async def fake_review_history(name, cancel_event):
            events.append(("review_history", name, cancel_event.is_set()))

        async def fake_review_negative_preferences(messages, name):
            events.append(("negative_review", name, messages))
            return 1

        with patch.object(memory_server.recent_history_manager, "review_history", side_effect=fake_review_history), \
             patch.object(memory_server.recent_history_manager, "get_recent_history", return_value=fallback_messages), \
             patch.object(memory_server, "_review_and_apply_negative_preferences", side_effect=fake_review_negative_preferences):
            asyncio.run(memory_server._run_review_in_background("测试猫娘", []))

        assert events[0] == ("review_history", "测试猫娘", False)
        assert events[1] == ("negative_review", "测试猫娘", fallback_messages)


def test_negative_review_deduplicates_same_topic_in_one_round() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        reviewed = [
            {"topic": "Work", "policy": "de_emphasize", "confidence": 0.91},
            {"topic": "work", "policy": "avoid", "confidence": 0.93},
            {"topic": "WORK", "policy": "avoid", "confidence": 0.90},
        ]
        applied_calls = []

        async def fake_review_negative_preferences(messages, name):
            return reviewed

        def fake_apply(name, *, topic, policy, source="negative_review"):
            applied_calls.append((name, topic, policy, source))
            return {
                "matched": True,
                "topic": topic.casefold(),
                "policy": policy,
                "response_instruction": "",
            }

        with patch.object(memory_server, "_get_negative_signal_user_messages", return_value=["this is annoying"]), \
             patch.object(memory_server, "_review_negative_preferences", side_effect=fake_review_negative_preferences), \
             patch.object(memory_server, "_validate_negative_topic_candidate", return_value={
                 "accepted": True,
                 "normalized_topic": "work",
                 "confidence": 0.98,
                 "reason": "ok",
             }), \
             patch.object(memory_server.persona_manager, "apply_negative_preference_review", side_effect=fake_apply):
            applied = asyncio.run(memory_server._review_and_apply_negative_preferences(reviewed, "测试猫娘"))

        assert applied == 1
        assert applied_calls == [("测试猫娘", "work", "avoid", "negative_review")]


def test_negative_review_does_not_infer_already_persisted_topic_from_raw_signal() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        reviewed = [
            {"topic": "Work", "policy": "avoid", "confidence": 0.95},
        ]

        async def fake_review_negative_preferences(messages, name):
            return reviewed

        with patch.object(memory_server, "_get_negative_signal_user_messages", return_value=["don't mention work anymore"]), \
             patch.object(memory_server, "_review_negative_preferences", side_effect=fake_review_negative_preferences), \
             patch.object(memory_server, "_validate_negative_topic_candidate", return_value={
                 "accepted": True,
                 "normalized_topic": "work",
                 "confidence": 0.99,
                 "reason": "ok",
             }), \
             patch.object(memory_server.persona_manager, "apply_negative_preference_review", return_value={
                 "matched": True,
                 "topic": "work",
                 "policy": "avoid",
                 "response_instruction": "",
             }) as apply_mock:
            applied = asyncio.run(memory_server._review_and_apply_negative_preferences(reviewed, "测试猫娘"))

        assert applied == 1
        apply_mock.assert_called_once()


def test_negative_review_skips_invalid_topic_candidate_before_persist() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        reviewed = [
            {"topic": "那吃不了一点，更", "policy": "de_emphasize", "confidence": 0.96, "user_evidence": "那吃不了一点，更", "assistant_evidence": "推荐了几个吃的选项"},
        ]

        async def fake_review_negative_preferences(messages, name):
            return reviewed

        with patch.object(memory_server, "_get_negative_signal_user_messages", return_value=["那吃不了一点，更"]), \
             patch.object(memory_server, "_review_negative_preferences", side_effect=fake_review_negative_preferences), \
             patch.object(memory_server, "_validate_negative_topic_candidate", return_value={
                 "accepted": False,
                 "normalized_topic": "",
                 "confidence": 0.11,
                 "reason": "fragment",
             }), \
             patch.object(memory_server.persona_manager, "apply_negative_preference_review") as apply_mock:
            applied = asyncio.run(memory_server._review_and_apply_negative_preferences(reviewed, "测试猫娘"))

        assert applied == 0
        apply_mock.assert_not_called()


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


def test_negative_signal_explicit_variant_without_topic_uses_referenced_topic() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal(
                "测试猫娘",
                "不想提了",
                referenced_topic="那我们继续聊工作问题吧",
            )
            assert result["matched"] is True
            assert result["topic"] == "工作问题"
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


def test_negative_signal_direct_dislike_first_soft_then_hard() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            first = pm.register_negative_signal("测试猫娘", "我不喜欢昆虫食品")
            assert first["matched"] is True
            assert first["topic"] == "昆虫食品"
            assert first["policy"] == "de_emphasize"

            second = pm.register_negative_signal("测试猫娘", "我不喜欢昆虫食品")
            assert second["matched"] is True
            assert second["topic"] == "昆虫食品"
            assert second["policy"] == "avoid"

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


def test_negative_signal_placeholder_topic_falls_back_to_tone_only() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "don't mention it anymore")
            assert result["matched"] is True
            assert result["topic"] == ""
            assert result["policy"] == "tone_only"


def test_negative_signal_capitalized_dont_mention_placeholder_stays_tone_only() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "Don't mention it")
            assert result["matched"] is True
            assert result["topic"] == ""
            assert result["policy"] == "tone_only"


def test_contains_negative_signal_avoids_ascii_substring_false_positive() -> None:
    from memory.persona import contains_negative_signal

    assert contains_negative_signal("whatever") is False


def test_negative_signal_chinese_placeholder_topic_falls_back_to_tone_only() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "不要这样子了")
            assert result["matched"] is True
            assert result["topic"] == ""
            assert result["policy"] == "tone_only"


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


def test_apply_negative_preference_review_does_not_increment_existing_soft_avoid() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm
            pm.register_negative_signal("测试猫娘", "工作这个话题真的好烦")

            result = pm.apply_negative_preference_review(
                "测试猫娘",
                topic="工作",
                policy="de_emphasize",
            )

            assert result["matched"] is True
            assert result["policy"] == "de_emphasize"

            fresh_pm = PersonaManager()
            fresh_pm._config_manager = mock_cm
            guidance = fresh_pm.get_persona("测试猫娘")["_topic_guidance"]
            assert guidance["hard_avoid"] == []
            assert guidance["soft_avoid"][0]["topic"] == "工作"
            assert guidance["soft_avoid"][0]["trigger_count"] == 1


def test_apply_negative_preference_review_promotes_policy_without_trigger_escalation() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm
            pm.register_negative_signal("测试猫娘", "工作这个话题真的好烦")

            result = pm.apply_negative_preference_review(
                "测试猫娘",
                topic="工作",
                policy="avoid",
            )

            assert result["matched"] is True
            assert result["policy"] == "avoid"

            fresh_pm = PersonaManager()
            fresh_pm._config_manager = mock_cm
            guidance = fresh_pm.get_persona("测试猫娘")["_topic_guidance"]
            assert guidance["soft_avoid"] == []
            assert guidance["hard_avoid"][0]["topic"] == "工作"
            assert guidance["hard_avoid"][0]["trigger_count"] == 1


def test_handle_negative_signal_returns_server_error_on_exception() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        request = memory_server.NegativeSignalRequest(message="别提了", referenced_topic="工作")

        with patch.object(memory_server.persona_manager, "analyze_negative_signal", side_effect=RuntimeError("boom")):
            response = asyncio.run(memory_server.handle_negative_signal(request, "测试猫娘"))

        assert response.status_code == 500
        payload = json.loads(response.body)
        assert payload["matched"] is False
        assert payload["error"] == "boom"


def test_handle_negative_signal_only_returns_tone_only_without_persisting_persona() -> None:
    with _import_memory_server_with_tempdir() as (memory_server, _mock_cm):
        request = memory_server.NegativeSignalRequest(
            message="那吃不了一点，更",
            referenced_topic="推荐了几个吃的选项",
        )

        with patch.object(memory_server.persona_manager, "analyze_negative_signal", return_value={
            "matched": True,
            "topic": "那吃不了一点，更",
            "policy": "de_emphasize",
            "response_instruction": "topic-specific",
            "explicit_avoid": False,
        }), \
             patch.object(memory_server, "_validate_negative_topic_candidate") as validate_mock, \
             patch.object(memory_server.persona_manager, "commit_negative_signal") as commit_mock:
            response = asyncio.run(memory_server.handle_negative_signal(request, "测试猫娘"))

        assert response.status_code == 200
        payload = json.loads(response.body)
        assert payload["matched"] is True
        assert payload["topic"] == ""
        assert payload["policy"] == "tone_only"
        assert "先共情安抚" in payload["response_instruction"]
        validate_mock.assert_not_called()
        commit_mock.assert_not_called()


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


def test_negative_signal_english_topic_detection_with_new_negative_adjectives() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "Work is horrible")
            assert result["matched"] is True
            assert result["topic"] == "work"
            assert result["policy"] == "de_emphasize"


def test_negative_signal_english_tone_only_for_non_topic_complaint() -> None:
    with tempfile.TemporaryDirectory(prefix="negative_persona_") as tmpdir:
        mock_cm = _build_mock_config_manager(tmpdir)
        with patch("utils.config_manager.get_config_manager", return_value=mock_cm), \
             patch("utils.config_manager._config_manager", mock_cm):
            from memory.persona import PersonaManager

            pm = PersonaManager()
            pm._config_manager = mock_cm

            result = pm.register_negative_signal("测试猫娘", "This sucks")
            assert result["matched"] is True
            assert result["topic"] == ""
            assert result["policy"] == "tone_only"


def test_topics_match_preserves_temporal_variant_for_latin_topics() -> None:
    from memory.persona import _topics_match

    assert _topics_match("work", "work tomorrow") is True


def test_topics_match_avoids_partial_latin_false_positive() -> None:
    from memory.persona import _topics_match

    assert _topics_match("art", "martial arts") is False


def test_topics_match_merges_simple_latin_inflection_variants() -> None:
    from memory.persona import _topics_match

    assert _topics_match("work issue", "work issues") is True


def test_topics_match_rejects_short_latin_substring_false_positive() -> None:
    from memory.persona import _topics_match

    assert _topics_match("ai", "maid") is False


def test_normalize_topic_tokens_preserves_double_s_words() -> None:
    from memory.persona import _normalize_topic_tokens

    tokens = _normalize_topic_tokens("class classes boss grass")
    assert "class" in tokens
    assert "boss" in tokens
    assert "grass" in tokens
    assert "bos" not in tokens
    assert "gras" not in tokens


def test_negative_keyword_fallback_keeps_inner_word_content() -> None:
    from memory.persona import _extract_negative_topic

    topic, explicit = _extract_negative_topic("Don't mention whatever")
    assert explicit is True
    assert topic == "whatever"


def test_import_memory_server_context_restores_original_module() -> None:
    original_module = sys.modules.get("memory_server")
    sentinel = types.ModuleType("memory_server")
    sys.modules["memory_server"] = sentinel

    try:
        with _import_memory_server_with_tempdir() as (memory_server, _):
            assert memory_server is not sentinel
        assert sys.modules.get("memory_server") is sentinel
    finally:
        if original_module is None:
            sys.modules.pop("memory_server", None)
        else:
            sys.modules["memory_server"] = original_module


def test_prompt_language_normalizer_handles_locale_variants() -> None:
    from config.prompts_memory import _normalize_language

    assert _normalize_language("zh-CN") == "zh"
    assert _normalize_language("ja_JP") == "ja"
    assert _normalize_language("") == "zh"


def test_negative_preference_prompt_normalizes_full_locale_code() -> None:
    from config.prompts_memory import get_negative_preference_review_prompt

    assert get_negative_preference_review_prompt("zh-CN") == get_negative_preference_review_prompt("zh")
    assert get_negative_preference_review_prompt("ja-JP") == get_negative_preference_review_prompt("ja")


def test_negative_preference_prompt_formatting_is_safe() -> None:
    from config.prompts_memory import get_negative_preference_review_prompt

    rendered = get_negative_preference_review_prompt("en")
    rendered = rendered.replace("{CURRENT_GUIDANCE}", "[]")
    rendered = rendered.replace("{CONVERSATION}", "user: don't mention insect food again")
    assert '"topic": "insect food"' in rendered


def test_negative_topic_validation_prompt_normalizes_full_locale_code() -> None:
    from config.prompts_memory import get_negative_topic_validation_prompt

    assert get_negative_topic_validation_prompt("zh-CN") == get_negative_topic_validation_prompt("zh")
    assert get_negative_topic_validation_prompt("ja-JP") == get_negative_topic_validation_prompt("ja")
    assert get_negative_topic_validation_prompt("ko-KR") == get_negative_topic_validation_prompt("ko")
    assert get_negative_topic_validation_prompt("ru-RU") == get_negative_topic_validation_prompt("ru")


def test_negative_topic_validation_prompt_formatting_is_safe() -> None:
    from config.prompts_memory import get_negative_topic_validation_prompt

    rendered = get_negative_topic_validation_prompt("zh")
    rendered = rendered.replace("{USER_MESSAGE}", "别提昆虫食品了")
    rendered = rendered.replace("{REFERENCED_TOPIC}", "昆虫食品")
    rendered = rendered.replace("{CANDIDATE_TOPIC}", "昆虫食品")
    assert '"accepted": true' in rendered


def test_negative_topic_validation_prompt_has_localized_entries_for_ja_ko_ru() -> None:
    from config.prompts_memory import get_negative_topic_validation_prompt

    ja_prompt = get_negative_topic_validation_prompt("ja")
    ko_prompt = get_negative_topic_validation_prompt("ko")
    ru_prompt = get_negative_topic_validation_prompt("ru")
    zh_prompt = get_negative_topic_validation_prompt("zh")

    assert ja_prompt != zh_prompt
    assert ko_prompt != zh_prompt
    assert ru_prompt != zh_prompt

    for prompt in (ja_prompt, ko_prompt, ru_prompt):
        rendered = prompt
        rendered = rendered.replace("{USER_MESSAGE}", "don't mention insect food again")
        rendered = rendered.replace("{REFERENCED_TOPIC}", "insect food")
        rendered = rendered.replace("{CANDIDATE_TOPIC}", "insect food")
        assert '"accepted": true' in rendered
