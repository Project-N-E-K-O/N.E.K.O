"""Keep authored prohibition polarity through public prompt projection without changing narrow checks."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_actor as actor, numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_context import contract_boundary_items, project_contract_boundaries
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_prompt_contract import _session


def _data(messages):
    content = messages[1].content
    return prompt_payload(content)


@pytest.mark.parametrize("rule", ["玩家帮助寻找新店面", "不得让玩家帮助寻找新店面"])
def test_public_projection_labels_prohibition_source_without_rewriting_text(rule):
    beat = {
        "must_not_happen": [rule],
        "acting_contract": {"forbidden_behaviors": ["接受经济援助", "不得请求安慰"]},
    }
    assert project_contract_boundaries(beat, explicit_prohibitions=True) == (
        "演出角色禁止行为：接受经济援助",
        "演出角色禁止行为：不得请求安慰",
        f"禁止发生的剧情：{rule}",
    )


def test_cross_source_duplicate_retains_first_position_and_prohibition_polarity():
    beat = {
        "opening_only_boundaries": ["不得透露秘密", "重复条款"],
        "character_state": {"scene_boundaries": [" 场景先出现的条款 ", "场景独有条款"]},
        "acting_contract": {"forbidden_behaviors": ["行为独有条款", "重复条款"]},
        "must_not_happen": ["场景先出现的条款", "重复条款", "剧情独有条款"],
    }
    before = deepcopy(beat)
    assert project_contract_boundaries(
        beat, include_opening_only=True, explicit_prohibitions=True,
    ) == (
        "不得透露秘密",
        "演出角色禁止行为：重复条款",
        "禁止发生的剧情：场景先出现的条款",
        "场景独有条款",
        "演出角色禁止行为：行为独有条款",
        "禁止发生的剧情：剧情独有条款",
    )
    assert beat == before


def test_full_conditions_and_exceptions_survive_explicit_projection():
    rule = "公开展示时不得移动晶片；" + "保管人仍在核对登记记录。" * 40 + "保管人明确要求归还时允许交还。"
    beat = {"must_not_happen": [rule]}
    assert project_contract_boundaries(beat, explicit_prohibitions=True) == (f"禁止发生的剧情：{rule}",)


def test_default_and_narrow_fact_projection_preserve_original_text_order_and_filter():
    beat = {
        "opening_only_boundaries": ["开场临时边界"],
        "character_state": {"scene_boundaries": ["不得移动晶片", "不得交换主体"]},
        "acting_contract": {"forbidden_behaviors": ["接受经济援助"]},
        "must_not_happen": ["不得移动晶片", "不得替玩家作决定"],
    }
    assert project_contract_boundaries(beat, include_opening_only=True) == (
        "开场临时边界", "不得移动晶片", "不得交换主体", "接受经济援助", "不得替玩家作决定",
    )
    for explicit in (False, True):
        assert project_contract_boundaries(
            beat, include_opening_only=True, fact_only=True, explicit_prohibitions=explicit,
        ) == ("不得移动晶片", "不得交换主体", "开场临时边界")
    assert contract_boundary_items({"story_beat": beat}) == ("不得移动晶片", "不得交换主体")


@pytest.mark.parametrize("include_opening", [False, True])
def test_actor_and_evaluator_share_explicit_public_projection(include_opening):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    cast = NumericV2CastProjection.from_story(engine.story, catgirl_name="测试猫娘", player_name="哥哥")
    beat = {
        "opening_only_boundaries": ["不得提前公开秘密"],
        "must_not_happen": ["玩家帮助寻找新店面"],
        "acting_contract": {"forbidden_behaviors": ["接受经济援助"]},
    }
    expected = (["不得提前公开秘密"] if include_opening else []) + [
        "演出角色禁止行为：接受经济援助", "禁止发生的剧情：玩家帮助寻找新店面",
    ]
    assert actor._suggestion_hard_boundaries(cast, beat, include_opening_only=include_opening) == expected
    assert evaluator._actor_fact_boundaries(beat, include_opening_only=include_opening) == expected


@pytest.mark.parametrize("formal", [False, True])
def test_ordinary_formal_and_fill_prompts_preserve_explicit_prohibitions(formal):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    source = engine.nodes["start"]["story_beat"]
    source["must_not_happen"] = ["玩家替店主偿还租金"]
    target = engine.nodes["ending_leave"]["story_beat"]
    target["must_not_happen"] = ["玩家帮助寻找新店面"]
    target["acting_contract"] = {"forbidden_behaviors": ["接受经济援助"]}
    session = _session(engine)
    player_input = "谢谢你，我记住了。"
    outcome = engine.resolve_turn(
        session, TurnRequestV2("polarity", 0, player_input), (),
        scene_complete=formal, natural_ending_ready=formal,
    )
    messages = actor._turn_messages(engine, session, outcome, player_input, "温和", "测试猫娘", "哥哥")
    data = _data(messages)
    source_rule = "禁止发生的剧情：玩家替店主偿还租金"
    target_rule = "禁止发生的剧情：玩家帮助寻找新店面"
    assert source_rule in messages[0].content
    if formal:
        # Both complete, phase-labelled prohibitions remain authoritative in System;
        # a second full copy in Human used to crowd out a valid transition's ledger.
        assert f"来源幕：{source_rule}" in messages[0].content
        assert f"目标幕：{target_rule}" in messages[0].content
        assert "boundaries" not in data["transition"]["source_scene"]
        assert "boundaries" not in data["transition"]["target_scene"]
        assert "shared_boundaries" not in data["transition"]
        assert "演出角色禁止行为：接受经济援助" in messages[0].content
        current_beat = target
    else:
        assert "transition" not in data
        assert target_rule not in messages[0].content
        current_beat = source
    cast = NumericV2CastProjection.from_story(engine.story, catgirl_name="测试猫娘", player_name="哥哥")
    boundaries = actor._suggestion_hard_boundaries(cast, current_beat, include_opening_only=formal)
    fill = actor._suggestion_fill_messages(
        catgirl_name="测试猫娘", performance={"performance": "那就先这样。"},
        player_input=player_input, after_scene_change=formal, max_tokens=10000,
        hard_boundaries=boundaries,
    )
    assert _data(fill)["hard_boundaries"] == boundaries
    assert (target_rule if formal else source_rule) in _data(fill)["hard_boundaries"]


def test_formal_review_deduplicates_raw_role_boundaries_after_explicit_projection():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    target = engine.nodes["ending_leave"]["story_beat"]
    rule = "接受经济援助；仅约束以金钱解决店铺租金，玩家归还店主垫付的零钱除外。"
    target["must_not_happen"] = ["玩家帮助寻找新店面"]
    target["character_state"] = {"scene_boundaries": ["不得移动晶片"], "catgirl_state": "仍在柜台边。"}
    target["acting_contract"] = {"forbidden_behaviors": [rule], "allowed_behaviors": ["自然道谢"]}
    before = deepcopy(target)
    session = _session(engine)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("review_polarity", 0, "谢谢你。"), (),
        scene_complete=True, natural_ending_ready=True,
    )
    messages, _ = evaluator._build_transition_judge_messages(
        engine, session, actor_performance={"performance": "（点头）谢谢。"},
        player_input="谢谢你。", route_changed=True, transition_outcome=outcome,
    )
    data = _data(messages)["target_scene"]
    assert data["hard_boundaries"] == [
        "不得移动晶片", f"演出角色禁止行为：{rule}", "禁止发生的剧情：玩家帮助寻找新店面",
    ]
    assert "scene_boundaries" not in data["author_entry_plan"]
    assert "forbidden_behaviors" not in data["acting_contract"]
    assert data["author_entry_plan"]["catgirl_state"] == "仍在柜台边。"
    assert data["acting_contract"]["allowed_behaviors"] == ["自然道谢"]
    assert messages[1].content.count(rule) == 1
    assert target == before
