"""Persona projection preserves character differences without importing the real world."""

import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.llm_context import _load_character_profile


def _config(tmp_path, profile):
    """Use only a bound synthetic card; no test reads the user's actual persona."""
    return SimpleNamespace(app_docs_dir=tmp_path, load_characters=lambda: {
        "当前猫娘": "测试猫娘", "猫娘": {"测试猫娘": profile, "其他猫娘": {"性格": "急躁"}},
    })


def test_current_card_persona_does_not_wait_for_background_memory_sync(tmp_path):
    config = _config(tmp_path, {"性格": "谨慎，习惯先核对信息", "口癖": "让我想一想",
                                "特殊能力": "读取未公开的记录", "关系": "已经是恋人"})
    profile = _load_character_profile(config, "测试猫娘")
    assert "性格: 谨慎，习惯先核对信息" in profile
    assert "口癖: 让我想一想" in profile
    assert "未公开的记录" not in profile
    assert "已经是恋人" not in profile


def test_current_card_wins_over_stale_memory_for_the_same_persona_field(tmp_path):
    directory = tmp_path / "memory" / "测试猫娘"
    directory.mkdir(parents=True)
    (directory / "persona.json").write_text(json.dumps({"neko": {"facts": [
        {"text": "性格: 冲动"}, {"text": "核心特质: 对细节敏感"},
        {"text": "特殊能力: 看穿一切秘密"},
    ]}, "relationship": {"facts": [{"text": "关系: 用户是恋人"}]}}, ensure_ascii=False), encoding="utf-8")
    profile = _load_character_profile(_config(tmp_path, {"性格": "谨慎"}), "测试猫娘")
    assert "性格: 谨慎" in profile
    assert "性格: 冲动" not in profile
    assert "核心特质: 对细节敏感" in profile
    assert "看穿一切秘密" not in profile
    assert "用户是恋人" not in actor.actor_visible_profile(profile)


@pytest.mark.parametrize("name", ["其他猫娘", "../测试猫娘", "", "."])
def test_persona_fallback_cannot_read_another_or_unsafe_character(tmp_path, name):
    assert _load_character_profile(_config(tmp_path, {"性格": "谨慎"}), name) == ""


def test_full_persona_influences_judgment_but_does_not_grant_story_authority():
    profile = actor._profile_for_acting_contract(
        "性格: 谨慎\n行为特征: 先检查再决定\n爱好: 整理线索\n特殊能力: 直接读取秘密\n关系: 已经相爱",
        {"persona_scope": "full"},
    )
    assert "先检查再决定" in profile
    assert "整理线索" in profile
    assert "关注、判断与自主反应" in profile
    assert "不授权新增能力、知识、玩家行动或既有关系" in profile
    assert "直接读取秘密" not in profile
    assert "已经相爱" not in profile


def test_style_only_remains_expressive_and_does_not_gain_behavior_preferences():
    profile = actor._profile_for_acting_contract(
        "自称: 人家\n性格: 温柔但有主见\n行为特征: 主动接手处理\n爱好: 解谜",
        {"persona_scope": "style_only", "self_reference_mode": "system_neutral"},
    )
    assert "语言氛围参考（只影响措辞" in profile
    assert "温柔但有主见" in profile
    assert "主动接手处理" not in profile
    assert "解谜" not in profile
    assert "自称" not in profile


def test_widest_consumed_persona_detects_behavior_updates_for_commit_validation():
    assert actor.actor_visible_profile("性格: 谨慎\n行为特征: 先观察") != actor.actor_visible_profile(
        "性格: 谨慎\n行为特征: 先询问")
    assert actor.actor_visible_profile("性格: 谨慎\n关系: 已经相爱") == actor.actor_visible_profile(
        "性格: 谨慎\n关系: 刚认识")


def test_structured_card_keeps_unlabelled_memory_as_expressive_supplement(tmp_path):
    directory = tmp_path / "memory" / "测试猫娘"
    directory.mkdir(parents=True)
    (directory / "persona.json").write_text(json.dumps({"neko": {"facts": [
        {"text": "说话时习惯先想一想再回应。"},
        {"text": "特殊能力: 直接读取秘密\n可以看穿未公开的内容。"},
    ]}}), encoding="utf-8")
    profile = _load_character_profile(_config(tmp_path, {"性格": "谨慎"}), "测试猫娘")
    visible = actor._profile_for_acting_contract(profile, {"persona_scope": "full"})
    assert "性格: 谨慎" in visible
    assert "语言氛围参考（只影响措辞" in visible
    assert "说话时习惯先想一想再回应。" in visible
    assert "直接读取秘密" not in visible
    assert "可以看穿未公开的内容" not in visible


@pytest.mark.parametrize("scope", ["full", "style_only"])
def test_multiline_card_trait_keeps_complete_body_under_its_original_scope(tmp_path, scope):
    raw = _load_character_profile(_config(tmp_path, {
        "性格": "谨慎\n遇到争执时会先冷静下来。",
        "行为特征": "先观察\n需要时会提出自己的不同意见。",
    }), "测试猫娘")
    visible = actor._profile_for_acting_contract(raw, {"persona_scope": scope})
    assert "谨慎\n遇到争执时会先冷静下来。" in visible
    assert ("需要时会提出自己的不同意见。" in visible) is (scope == "full")


def test_unknown_field_continuation_cannot_become_an_unlabelled_trait():
    visible = actor._profile_for_acting_contract(
        "性格: 谨慎\n\n特殊能力: 读取秘密\n还能知道未公开安排。\n\n说话时会留一点停顿。",
        {"persona_scope": "full"},
    )
    assert "性格: 谨慎" in visible
    assert "读取秘密" not in visible
    assert "未公开安排" not in visible
    assert "语言氛围参考（只影响措辞" in visible
    assert "说话时会留一点停顿。" in visible
