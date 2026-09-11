"""Numeric v2 包合同、复验和独立安装目录测试。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from services.theater import numeric_v2_registry
from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from services.theater.numeric_v2_registry import (
    NumericV2PackageExistsError,
    NumericV2PackageError,
    NumericV2PackageRegistry,
)


def numeric_v2_story(*, player_address_known: bool = True) -> dict:
    """构造一个包含两条数值路线和两个结局的最小合法包。"""  # noqa: DOCSTRING_CJK

    metric = {
        "name": "信任度",
        "description": "猫娘愿意相信玩家承诺的程度。",
        "relationship_effect": "positive",
        "min": 0,
        "max": 100,
        "initial": 20,
        "visibility": "hidden",
        "per_turn_limit": {"increase": 5, "decrease": 5},
        "increase_criteria": ["玩家兑现承诺"],
        "decrease_criteria": ["玩家故意说谎"],
        "bands": [
            {"min": 0, "max": 29, "label": "戒备"},
            {"min": 30, "max": 69, "label": "试探"},
            {"min": 70, "max": 100, "label": "信赖"},
        ],
    }

    def beat(summary: str) -> dict:
        return {
            "summary": summary,
            "must_happen": [summary],
            "must_not_happen": [],
            "catgirl_situation": "她在观察玩家是否可信。",
            "transition_goal": "围绕承诺和离开继续发展。",
        }

    def gate(gate_id: str, target: str, op: str, value: int, priority: int) -> dict:
        return {
            "id": gate_id,
            "target_node_id": target,
            "priority": priority,
            "conditions": {
                "all": [
                    {
                        "type": "metric_compare",
                        "metric": "trust",
                        "op": op,
                        "value": value,
                    }
                ]
            },
            "transition_contract": {
                "reason": "当前信任度满足作者路线条件。",
                "must_deliver": ["平滑交付目标剧情"],
                "must_preserve": ["不覆盖此前已经发生的内容"],
                "tone": "克制",
            },
        }

    return {
        "schema": "neko.story.numeric.v2",
        "meta": {
            "story_id": "numeric_v2_contract",
            "title": "Numeric v2 合同测试",
            "author": "test",
            "revision": "r1",
            "language": "zh-CN",
            # 默认测试包代表当前唯一可运行的 v2.2 合同；旧包测试显式改写为 v2.1。
            "contract_version": "v2.2",
        },
        "intro": {
            "background": "玩家多年后回到小镇，在花店遇到旧友。",
            "player_identity": "林舟，回乡整理旧屋的年轻男性。",
            "catgirl_identity": "小岚，经营花店、保留旧信的年轻女性。",
        },
        "characters": {},
        "catgirl_binding": {
            "source": "runtime.current_catgirl",
            "role_overlay": "她既期待重逢，又担心玩家再次离开。",
        },
        "metric_schema": {"trust": metric},
        "initial_state": {
            "metrics": {"trust": 20},
            "player_address_known": player_address_known,
        },
        "start_node_id": "start",
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "chapter": "重逢",
                "min_turns": 2,
                "recommended_turns": 4,
                "story_beat": beat("雨后的花店门铃轻轻响起。"),
                "route_gates": [
                    gate("to_stay", "ending_stay", ">=", 70, 20),
                    gate("to_leave", "ending_leave", "<", 70, 10),
                ],
            },
            {
                "id": "ending_stay",
                "type": "ending",
                "chapter": "决定",
                "story_beat": beat("旧信与备用钥匙并排放在桌面上。"),
                "route_gates": [],
                "terminal": True,
                "ending_id": "stay",
            },
            {
                "id": "ending_leave",
                "type": "ending",
                "chapter": "决定",
                "story_beat": beat("雨停后的长街恢复了安静。"),
                "route_gates": [],
                "terminal": True,
                "ending_id": "leave",
            },
        ],
        "endings": [
            {"id": "stay", "title": "留下", "summary": "玩家留下。", "terminal": True},
            {"id": "leave", "title": "离开", "summary": "玩家离开。", "terminal": True},
        ],
    }


def numeric_v2_1_story() -> dict:
    """把基础包升级为带事实来源与作者桥段的严格 v2.1 测试包。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["meta"]["contract_version"] = "v2.1"
    for node in story["nodes"]:
        beat = node["story_beat"]
        summary = beat["summary"]
        beat["opening_scene"] = summary
        beat.pop("must_happen")
        beat["goals"] = [{
            "id": f"{node['id']}.opening_fact",
            "owner": "environment",
            "description": "环境明确交付当前场景事实。",
            "evidence": {"mode": "exact", "anchors": [summary]},
            "delivery": {
                "type": "environment_fact",
                "output_field": "scene_update",
                "source_ids": [f"opening.{node['id']}"],
            },
        }]
        for route in node["route_gates"]:
            route["transition_contract"]["bridge_scene_narration"] = (
                "雨声停下，花店外的长街重新亮起路灯。"
            )
            route["transition_contract"]["source_ids"] = [
                f"goal.{node['id']}.opening_fact"
            ]
    return story


def test_numeric_v2_compiles_canonical_package():
    compiled = NumericV2Compiler().compile(numeric_v2_story())

    assert compiled.story["schema"] == "neko.story.numeric.v2"
    assert compiled.package_hash.startswith("sha256:")
    assert json.loads(compiled.json_bytes)["meta"]["story_id"] == "numeric_v2_contract"


def test_numeric_v2_old_compile_entry_requires_upgrade():
    """旧版编译入口只返回升级错误，不再生成可运行包。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_1_story()
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_1(story)
    assert {issue.code for issue in caught.value.issues} == {"numeric_v2_upgrade_required"}


def test_numeric_v2_declared_v2_1_cannot_bypass_registry_strict_gate(tmp_path):
    registry = NumericV2PackageRegistry(tmp_path / "packages")
    legacy = numeric_v2_story()
    legacy["meta"]["contract_version"] = "v2.1"

    with pytest.raises(NumericV2CompileError) as direct_compile_error:
        NumericV2Compiler().compile_v2_2(legacy)
    assert {issue.code for issue in direct_compile_error.value.issues} == {
        "numeric_v2_upgrade_required"
    }

    with pytest.raises(NumericV2PackageError) as caught:
        registry.import_package(legacy)

    assert str(caught.value) == "numeric_v2_upgrade_required"

    strict_story = numeric_v2_1_story()
    with pytest.raises(NumericV2PackageError, match="numeric_v2_upgrade_required"):
        registry.import_package(strict_story)


def test_numeric_v2_registry_hides_and_rejects_legacy_package_on_load(tmp_path):
    """磁盘上的旧包只供作者升级，不能出现在运行列表或直接加载。"""  # noqa: DOCSTRING_CJK

    package_root = tmp_path / "packages"
    package_root.mkdir()
    registry = NumericV2PackageRegistry(package_root)
    legacy = numeric_v2_story()
    legacy["meta"].update({
        "story_id": "legacy_on_disk",
        "contract_version": "v2.1",
    })
    (package_root / "legacy_on_disk.json").write_text(
        json.dumps(legacy, ensure_ascii=False),
        encoding="utf-8",
    )

    # 旧包不进入可运行列表；即使知道 story_id，也必须先升级到 v2.2。
    assert registry.list_packages() == []
    with pytest.raises(NumericV2PackageError, match="numeric_v2_upgrade_required"):
        registry.load_engine("legacy_on_disk")


def test_numeric_v2_v22_uses_strict_limit_gate_and_registry_dispatch(tmp_path):
    """新生成包必须显式声明 v2.2，并把单回合变化限制在 1—5。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_1_story()
    story["meta"]["contract_version"] = "v2.2"
    compiled = NumericV2Compiler().compile_v2_2(story)
    assert compiled.story["meta"]["contract_version"] == "v2.2"

    registry = NumericV2PackageRegistry(tmp_path / "packages")
    imported = registry.import_package(story)
    assert imported["contract_version"] == "v2.2"

    story["metric_schema"]["trust"]["per_turn_limit"]["increase"] = 6
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert any(
        issue.code == "v2_2_turn_limit_out_of_range"
        for issue in caught.value.issues
    )


def test_numeric_v2_v22_does_not_require_legacy_typed_goal_contract():
    """v2.2 目标是创作素材，不再因缺少旧 typed evidence/delivery 被编译器阻断。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["meta"]["contract_version"] = "v2.2"
    compiled = NumericV2Compiler().compile_v2_2(story)

    assert compiled.story["meta"]["contract_version"] == "v2.2"


def test_numeric_v2_rejects_actor_prompt_field_over_budget():
    """导入时必须拒绝必然挤占 Actor 固定上下文的超长作者字段。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["intro"]["background"] = "无法用于开场的超长背景。" * 500

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "actor_prompt_field_too_large"
        and issue.path == "intro.background"
        for issue in caught.value.issues
    )


def test_numeric_v2_requires_structured_initial_player_address_state():
    story = numeric_v2_story()
    story["initial_state"]["player_address_known"] = "称呼未知"

    with pytest.raises(NumericV2CompileError) as error:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "invalid_initial_player_address_known"
        and issue.path == "initial_state.player_address_known"
        for issue in error.value.issues
    )


def test_numeric_v2_validates_optional_acting_contract():
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["acting_contract"] = {
        "cognition_state": "fresh_boot",
        "memory_state": "empty",
        "self_reference_mode": "system_neutral",
        "persona_scope": "style_only",
        "assertable_self_facts": ["视觉校准完成", "主存储区为空"],
        "allowed_behaviors": ["自检", "观察", "确认环境"],
        "forbidden_behaviors": ["使用角色卡自称", "虚构旧记忆"],
    }

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["nodes"][0]["story_beat"]["acting_contract"]["cognition_state"] == "fresh_boot"
    assert compiled.story["nodes"][0]["story_beat"]["acting_contract"]["assertable_self_facts"] == [
        "视觉校准完成",
        "主存储区为空",
    ]

    story["nodes"][0]["story_beat"]["acting_contract"]["self_reference_mode"] = "invalid"
    with pytest.raises(NumericV2CompileError) as error:
        NumericV2Compiler().compile(story)
    assert any(
        issue.code == "invalid_acting_contract_value"
        and issue.path.endswith("acting_contract.self_reference_mode")
        for issue in error.value.issues
    )

    story["nodes"][0]["story_beat"]["acting_contract"]["self_reference_mode"] = "system_neutral"
    story["nodes"][0]["story_beat"]["acting_contract"]["assertable_self_facts"] = []
    with pytest.raises(NumericV2CompileError) as error:
        NumericV2Compiler().compile(story)
    assert any(
        issue.path.endswith("acting_contract.assertable_self_facts")
        for issue in error.value.issues
    )


def test_numeric_v2_validates_opening_only_boundaries():
    """临时开场边界是显式作者字段，旧边界字段仍保持原语义。"""

    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat["opening_only_boundaries"] = ["不得在公开开场披露后续身份。"]

    compiled = NumericV2Compiler().compile(story)
    assert compiled.story["nodes"][0]["story_beat"]["opening_only_boundaries"] == [
        "不得在公开开场披露后续身份。"
    ]

    beat["opening_only_boundaries"] = ["开场保持克制。"]
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)
    assert any(
        issue.code == "opening_only_boundary_polarity_invalid"
        for issue in caught.value.issues
    )

    beat["opening_only_boundaries"] = [f"不得提前披露第 {index} 项。" for index in range(5)]
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)
    assert any(
        issue.code == "too_many_opening_only_boundaries"
        for issue in caught.value.issues
    )


def test_numeric_v2_accepts_structured_story_beat_contract_without_rewriting_legacy_beats():
    """新包可声明明确开场、关系上限和目标证据，未迁移节点仍保持原文与哈希输入。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat["summary"] = "你最终决定是否留下，这是本幕的作者计划。"
    beat["opening_scene"] = "雨水沿着花店玻璃缓慢滑落。"
    beat["relationship_ceiling"] = "guarded"
    beat["goals"] = [
        {
            "id": "confirm_old_letter",
            "owner": "catgirl",
            "description": "女主说明旧信一直由她保管。",
            "evidence": {
                "mode": "exact",
                "anchors": ["旧信一直由我保管"],
            },
        }
    ]
    beat.pop("must_happen")

    compiled = NumericV2Compiler().compile(story)
    compiled_beat = compiled.story["nodes"][0]["story_beat"]

    assert compiled_beat["opening_scene"] == "雨水沿着花店玻璃缓慢滑落。"
    assert compiled_beat["relationship_ceiling"] == "guarded"
    assert compiled_beat["goals"][0]["id"] == "confirm_old_letter"
    assert "must_happen" not in compiled_beat
    # 兼容节点不能被编译器静默补入新字段，否则旧安装包哈希和 Session 会失效。
    assert "opening_scene" not in compiled.story["nodes"][1]["story_beat"]
    assert "relationship_ceiling" not in compiled.story["nodes"][1]["story_beat"]
    assert "goals" not in compiled.story["nodes"][1]["story_beat"]


def test_numeric_v2_accepts_typed_goal_delivery_contract():
    """v2.1 交付类型必须原样进入 canonical 包，供 Runtime 确定性消费。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat.pop("must_happen")
    beat["goals"] = [{
        "id": "confirm_old_letter",
        "owner": "catgirl",
        "description": "女主明确说明旧信由她保管。",
        "evidence": {"mode": "exact", "anchors": ["旧信由我保管"]},
        "delivery": {
            "type": "catgirl_dialogue",
            "output_field": "performance_dialogue",
            "source_ids": ["opening.old_letter"],
        },
    }]

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["nodes"][0]["story_beat"]["goals"][0]["delivery"] == {
        "type": "catgirl_dialogue",
        "output_field": "performance_dialogue",
        "source_ids": ["opening.old_letter"],
    }


def test_numeric_v2_rejects_third_person_exact_anchor_for_catgirl_action():
    """动作锚点会原样进入括号，不能把第三人称旁白交给 TTS。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat.pop("must_happen")
    beat["goals"] = [{
        "id": "disconnect_leg",
        "owner": "catgirl",
        "description": "女主切断右腿运动模块。",
        "evidence": {"mode": "exact", "anchors": ["她切断右腿运动模块"]},
        "delivery": {
            "type": "catgirl_action",
            "output_field": "performance_action",
            "source_ids": ["opening.old_letter"],
        },
    }]

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert "catgirl_action_anchor_third_person" in {
        issue.code for issue in caught.value.issues
    }


@pytest.mark.parametrize(
    ("delivery", "owner", "evidence", "issue_code"),
    [
        (
            {"type": "environment_fact", "output_field": "scene_update"},
            "catgirl",
            {"mode": "exact", "anchors": ["雨停了"]},
            "goal_delivery_owner_mismatch",
        ),
        (
            {"type": "catgirl_dialogue", "output_field": "scene_update"},
            "catgirl",
            {"mode": "exact", "anchors": ["由我保管"]},
            "goal_delivery_output_mismatch",
        ),
        (
            {"type": "semantic_state", "output_field": "evaluator"},
            "catgirl",
            {"mode": "exact", "anchors": ["愿意信任"]},
            "semantic_delivery_requires_semantic_evidence",
        ),
    ],
)
def test_numeric_v2_rejects_invalid_typed_goal_delivery(
    delivery,
    owner,
    evidence,
    issue_code,
):
    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat.pop("must_happen")
    beat["goals"] = [{
        "id": "typed_goal",
        "owner": owner,
        "description": "用于验证 typed delivery 的目标。",
        "evidence": evidence,
        "delivery": delivery,
    }]

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(issue.code == issue_code for issue in caught.value.issues)


@pytest.mark.parametrize(
    ("delivery_type", "owner", "output_field"),
    [
        ("catgirl_dialogue", "catgirl", "performance_dialogue"),
        ("catgirl_action", "catgirl", "performance_action"),
        ("environment_fact", "environment", "scene_update"),
        ("player_action", "player", "player_input"),
        ("shared_agreement", "shared", "shared"),
    ],
)
def test_numeric_v2_accepts_natural_semantic_typed_delivery(
    delivery_type,
    owner,
    output_field,
):
    """输出位置不等于逐字合同；自然动作与对白由 Evaluator 做语义取证。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    beat = story["nodes"][0]["story_beat"]
    beat.pop("must_happen")
    beat["goals"] = [{
        "id": "semantic_typed_goal",
        "owner": owner,
        "description": "用自然、可观察的方式推进当前目标。",
        "evidence": {"mode": "semantic", "anchors": []},
        "delivery": {
            "type": delivery_type,
            "output_field": output_field,
        },
    }]

    NumericV2Compiler().compile(story)


@pytest.mark.parametrize(
    ("mutate", "issue_code"),
    [
        (
            lambda beat: beat.update({
                "goals": [{
                    "id": "duplicate_source",
                    "owner": "catgirl",
                    "description": "女主说明旧信来历。",
                    "evidence": {"mode": "semantic", "anchors": []},
                }],
            }),
            "conflicting_story_goal_contracts",
        ),
        (
            lambda beat: beat.update({"relationship_ceiling": "very_close"}),
            "invalid_relationship_ceiling",
        ),
        (
            lambda beat: (
                beat.pop("must_happen"),
                beat.update({
                    "goals": [{
                        "id": "missing_anchor",
                        "owner": "catgirl",
                        "description": "女主明确说出旧信来历。",
                        "evidence": {"mode": "exact", "anchors": []},
                    }],
                }),
            ),
            "goal_evidence_anchors_required",
        ),
    ],
)
def test_numeric_v2_rejects_invalid_structured_story_beat_contract(mutate, issue_code):
    """结构化字段必须在编译边界失败，不能把不完整合同留给模型猜测。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    mutate(story["nodes"][0]["story_beat"])

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(issue.code == issue_code for issue in caught.value.issues)


def test_numeric_v2_soft_pacing_budget_is_optional_and_validated():
    story = numeric_v2_story()
    story["nodes"][0].pop("recommended_turns")

    assert "recommended_turns" not in NumericV2Compiler().compile(story).story["nodes"][0]

    story["nodes"][0]["recommended_turns"] = 1
    with pytest.raises(NumericV2CompileError) as below_minimum:
        NumericV2Compiler().compile(story)
    assert any(issue.code == "invalid_node_recommended_turns" for issue in below_minimum.value.issues)

    story["nodes"][0]["recommended_turns"] = 41
    with pytest.raises(NumericV2CompileError) as over_limit:
        NumericV2Compiler().compile(story)
    assert any(issue.code == "invalid_node_recommended_turns" for issue in over_limit.value.issues)


def test_numeric_v2_rejects_unknown_relationship_effect():
    story = numeric_v2_story()
    story["metric_schema"]["trust"]["relationship_effect"] = "guess"

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "invalid_metric_relationship_effect"
        for issue in caught.value.issues
    )


def test_numeric_v2_accepts_legacy_metric_without_relationship_effect():
    story = numeric_v2_story()
    story["metric_schema"]["trust"].pop("relationship_effect")

    compiled = NumericV2Compiler().compile(story)

    assert "relationship_effect" not in compiled.story["metric_schema"]["trust"]


def test_numeric_v2_rejects_conflicting_identity_source_names():
    story = numeric_v2_story()
    story["intro"]["player_identity"] = "同名，玩家身份。"
    story["intro"]["catgirl_identity"] = "同名，猫娘身份。"

    with pytest.raises(NumericV2CompileError) as error:
        NumericV2Compiler().compile(story)

    assert any(issue.code == "intro_identity_names_conflict" for issue in error.value.issues)


@pytest.mark.parametrize(
    ("field", "identity"),
    [
        ("catgirl_identity", "小葵是守着花店和旧信的店主。"),
        ("player_identity", "这是一段超过二十四个字符且不能作为独立角色姓名的首段，回乡故人。"),
    ],
)
def test_numeric_v2_rejects_identity_without_bounded_source_name(field, identity):
    """作者身份必须显式分出短姓名，运行时不能从整句职责里猜名字。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["intro"][field] = identity

    with pytest.raises(NumericV2CompileError) as error:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "intro_identity_name_segment_required"
        and issue.path == f"intro.{field}"
        for issue in error.value.issues
    )


@pytest.mark.parametrize(
    "summary",
    [
        "周末，男主开始清理堆积如山的快递纸箱。",
        "连续几晚，你都在整理堆积如山的遗失物。",
        "在决定离开后，两人登上了前往城市的列车。",
    ],
)
def test_numeric_v2_does_not_validate_opening_natural_language_with_word_lists(summary):
    story = numeric_v2_story()
    story["nodes"][1]["story_beat"]["summary"] = summary

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["nodes"][1]["story_beat"]["summary"] == summary


def test_numeric_v2_accepts_target_opening_with_causal_player_injury():
    story = numeric_v2_story()
    story["nodes"][1]["story_beat"]["summary"] = (
        "爆炸震裂舱壁，你被冲击波掀倒，手臂被碎片划伤。"
    )

    compiled = NumericV2Compiler().compile(story)

    assert "你被冲击波掀倒" in compiled.story["nodes"][1]["story_beat"]["summary"]


def test_numeric_v2_does_not_validate_transition_natural_language_with_word_lists():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["transition_contract"]["must_deliver"] = [
        "男主在打烊后展示一份商业改革计划书。",
    ]

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["nodes"][0]["route_gates"][0]["transition_contract"][
        "must_deliver"
    ] == ["男主在打烊后展示一份商业改革计划书。"]


def test_numeric_v2_accepts_author_transition_bridge_without_semantic_validation():
    story = numeric_v2_story()
    contract = story["nodes"][0]["route_gates"][0]["transition_contract"]
    contract["bridge_scene_narration"] = "雨声停下，花店外的长街重新亮起路灯。"

    compiled = NumericV2Compiler().compile(story)
    assert compiled.story["nodes"][0]["route_gates"][0]["transition_contract"][
        "bridge_scene_narration"
    ] == "雨声停下，花店外的长街重新亮起路灯。"

    contract["bridge_scene_narration"] = "男主走出花店并替两人作出决定。"
    compiled = NumericV2Compiler().compile(story)
    assert compiled.story["nodes"][0]["route_gates"][0]["transition_contract"][
        "bridge_scene_narration"
    ] == contract["bridge_scene_narration"]


def test_numeric_v2_accepts_author_transition_bridge_with_causal_player_result():
    story = numeric_v2_story()
    contract = story["nodes"][0]["route_gates"][0]["transition_contract"]
    contract["bridge_scene_narration"] = "爆炸掀起冲击波，你被气浪推入门后的掩体。"

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["nodes"][0]["route_gates"][0]["transition_contract"][
        "bridge_scene_narration"
    ] == "爆炸掀起冲击波，你被气浪推入门后的掩体。"


def test_numeric_v2_does_not_validate_legacy_goal_natural_language_with_word_lists():
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["must_happen"] = [
        "男主展示合同并承诺留下。",
    ]

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["nodes"][0]["story_beat"]["must_happen"] == [
        "男主展示合同并承诺留下。"
    ]


def test_numeric_v2_does_not_guess_forced_action_from_legacy_goal_text():
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["must_happen"] = [
        "女主强迫男主在契约上签字。",
    ]

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["nodes"][0]["story_beat"]["must_happen"] == [
        "女主强迫男主在契约上签字。"
    ]


def test_numeric_v2_rejects_legacy_interaction_fields_and_band_gaps():
    story = numeric_v2_story()
    story["interaction_rules"] = []
    story["metric_schema"]["trust"]["bands"][1]["min"] = 31

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    codes = {issue.code for issue in caught.value.issues}
    assert "legacy_field_forbidden" in codes
    assert "metric_bands_not_contiguous" in codes


def test_numeric_v2_rejects_route_threshold_outside_metric_range():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["conditions"]["all"][0]["value"] = 101

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "route_threshold_out_of_range"
        and issue.path == "nodes[0].route_gates[0].conditions.all[0].value"
        for issue in caught.value.issues
    )


def test_numeric_v2_rejects_impossible_route_condition():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["conditions"]["all"][0].update({"op": ">", "value": 100})

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(issue.code == "route_condition_impossible" for issue in caught.value.issues)


def test_numeric_v2_rejects_unsatisfiable_compound_route_condition():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["conditions"]["all"].append({
        "type": "metric_compare",
        "metric": "trust",
        "op": "<",
        "value": 30,
    })

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "route_condition_impossible"
        and issue.path == "nodes[0].route_gates[0].conditions"
        for issue in caught.value.issues
    )


def test_numeric_v2_rejects_empty_any_route_condition():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["conditions"] = {"any": []}

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(issue.code == "route_condition_required" for issue in caught.value.issues)


def test_numeric_v2_rejects_mismatched_metric_initial_state():
    story = numeric_v2_story()
    story["initial_state"]["metrics"]["trust"] = 21

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(issue.code == "initial_metric_value_mismatch" for issue in caught.value.issues)


def test_numeric_v2_rejects_overlapping_equal_priority_routes():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["priority"] = 10
    story["nodes"][0]["route_gates"][1]["priority"] = 10
    story["nodes"][0]["route_gates"][1]["conditions"]["all"][0]["op"] = ">="
    story["nodes"][0]["route_gates"][1]["conditions"]["all"][0]["value"] = 60

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(issue.code == "overlapping_route_priority" for issue in caught.value.issues)


def test_numeric_v2_rejects_player_visible_metric():
    story = numeric_v2_story()
    story["metric_schema"]["trust"]["visibility"] = "public"

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(issue.code == "invalid_metric_visibility" for issue in caught.value.issues)


def test_numeric_v2_accepts_metric_free_single_route_mainline():
    """纯主线只有一个出口时，不要求作者为了编译虚构数值条件。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["metric_schema"] = {}
    story["initial_state"] = {
        "metrics": {},
        "player_address_known": True,
    }
    story["nodes"] = [
        {
            "id": "start",
            "type": "start",
            "chapter": "重逢",
            "min_turns": 2,
            "story_beat": story["nodes"][0]["story_beat"],
            "route_gates": [
                {
                    "id": "to_scene",
                    "target_node_id": "scene",
                    "priority": 10,
                    "conditions": {"all": []},
                    "transition_contract": story["nodes"][0]["route_gates"][0]["transition_contract"],
                }
            ],
        },
        {
            "id": "scene",
            "type": "scene",
            "chapter": "坦白",
            "min_turns": 2,
            "story_beat": story["nodes"][0]["story_beat"],
            "route_gates": [
                {
                    "id": "to_normal",
                    "target_node_id": "ending_normal",
                    "priority": 10,
                    "conditions": {"all": []},
                    "transition_contract": story["nodes"][0]["route_gates"][0]["transition_contract"],
                }
            ],
        },
        {
            "id": "ending_normal",
            "type": "ending",
            "chapter": "带着理解分别",
            "story_beat": story["nodes"][1]["story_beat"],
            "route_gates": [],
            "terminal": True,
            "ending_id": "normal",
        },
    ]
    story["endings"] = [
        {
            "id": "normal",
            "title": "带着理解分别",
            "summary": "两人解开误会，并接受暂时分别。",
            "terminal": True,
        }
    ]

    compiled = NumericV2Compiler().compile(story)

    assert compiled.story["metric_schema"] == {}
    assert compiled.story["nodes"][0]["route_gates"][0]["conditions"] == {"all": []}


def test_numeric_v2_requires_conditions_when_one_node_has_multiple_routes():
    story = numeric_v2_story()
    for route in story["nodes"][0]["route_gates"]:
        route["conditions"] = {"all": []}

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert {issue.code for issue in caught.value.issues} == {"route_condition_required"}


def test_numeric_v2_rejects_reachable_scene_that_cannot_reach_an_ending():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"] = [story["nodes"][0]["route_gates"][0]]
    story["nodes"][0]["route_gates"][0]["target_node_id"] = "scene_loop"
    story["nodes"].insert(
        1,
        {
            "id": "scene_loop",
            "type": "scene",
            "chapter": "无法收束的支线",
            "min_turns": 2,
            "story_beat": story["nodes"][0]["story_beat"],
            "route_gates": [
                {
                    **story["nodes"][0]["route_gates"][0],
                    "id": "loop_forever",
                    "target_node_id": "scene_loop",
                }
            ],
        },
    )

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "node_cannot_reach_ending" and issue.path == "nodes.scene_loop"
        for issue in caught.value.issues
    )


def test_numeric_v2_rejects_direct_self_loop_even_with_an_ending_route():
    """同节点转场无法由现有 Ledger 表达，必须在编译期拒绝。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["target_node_id"] = "start"

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "route_self_loop_forbidden"
        and issue.path == "nodes[0].route_gates[0].target_node_id"
        for issue in caught.value.issues
    )


def test_numeric_v2_rejects_terminal_start_node():
    """开场即结局无法进入正常演绎流程，必须在导入时拒绝。"""  # noqa: DOCSTRING_CJK

    story = numeric_v2_story()
    story["nodes"][0]["terminal"] = True
    story["nodes"][0]["ending_id"] = "stay"
    story["nodes"][0]["route_gates"] = []

    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)

    assert any(
        issue.code == "terminal_start_forbidden"
        and issue.path == "nodes[0].terminal"
        for issue in caught.value.issues
    )


def test_numeric_v2_registry_imports_once_without_touching_sessions(tmp_path):
    package_root = tmp_path / "theater" / "numeric_v2" / "packages"
    registry = NumericV2PackageRegistry(package_root)

    result = registry.import_package(numeric_v2_story())

    assert result["story_id"] == "numeric_v2_contract"
    assert (package_root / "numeric_v2_contract.json").is_file()
    assert not (tmp_path / "theater" / "numeric_v2" / "sessions").exists()
    with pytest.raises(NumericV2PackageExistsError):
        registry.import_package(numeric_v2_story())


def test_numeric_v2_registry_waits_for_future_bundled_story_before_marking(tmp_path):
    package_root = tmp_path / "theater" / "numeric_v2" / "packages"
    registry = NumericV2PackageRegistry(package_root)

    registry.ensure_default_packages()

    assert registry.list_packages() == []
    assert not (package_root / ".defaults_initialized").exists()

    registry.ensure_default_packages()

    assert registry.list_packages() == []
    assert not (package_root / ".defaults_initialized").exists()


def test_numeric_v2_registry_installs_missing_defaults_beside_user_packages(
    tmp_path,
    monkeypatch,
):
    """用户已有剧本不能阻止首次安装其他内置剧本。"""  # noqa: DOCSTRING_CJK

    package_root = tmp_path / "theater" / "numeric_v2" / "packages"
    bundled_root = tmp_path / "bundled"
    bundled_root.mkdir()
    (bundled_root / "default.json").write_text(
        json.dumps(numeric_v2_story(), ensure_ascii=False),
        encoding="utf-8",
    )
    registry = NumericV2PackageRegistry(package_root)
    user_story = numeric_v2_story()
    user_story["meta"]["story_id"] = "user_numeric_story"
    registry.import_package(user_story)
    monkeypatch.setattr(numeric_v2_registry, "_DEFAULT_PACKAGE_ROOT", bundled_root)

    registry.ensure_default_packages()

    assert {item["story_id"] for item in registry.list_packages()} == {
        "numeric_v2_contract",
        "user_numeric_story",
    }
    assert (package_root / ".defaults_initialized").is_file()


def test_numeric_v2_registry_reconciles_new_defaults_without_restoring_deleted_ones(
    tmp_path,
    monkeypatch,
):
    """升级只补装新增内置剧本，用户主动删除的旧内置剧本保持删除。"""  # noqa: DOCSTRING_CJK

    package_root = tmp_path / "theater" / "numeric_v2" / "packages"
    bundled_root = tmp_path / "bundled"
    bundled_root.mkdir()
    first_story = numeric_v2_story()
    (bundled_root / "first.json").write_text(
        json.dumps(first_story, ensure_ascii=False),
        encoding="utf-8",
    )
    registry = NumericV2PackageRegistry(package_root)
    monkeypatch.setattr(numeric_v2_registry, "_DEFAULT_PACKAGE_ROOT", bundled_root)
    registry.ensure_default_packages()
    registry.delete_package(first_story["meta"]["story_id"])

    later_story = numeric_v2_story()
    later_story["meta"]["story_id"] = "numeric_v2_later_default"
    (bundled_root / "later.json").write_text(
        json.dumps(later_story, ensure_ascii=False),
        encoding="utf-8",
    )
    registry.ensure_default_packages()

    assert {item["story_id"] for item in registry.list_packages()} == {
        "numeric_v2_later_default",
    }


def test_numeric_v2_registry_treats_concurrent_default_install_as_success(
    tmp_path,
    monkeypatch,
):
    """另一进程抢先安装默认包时，本进程仍应完成初始化标记。"""  # noqa: DOCSTRING_CJK
    package_root = tmp_path / "theater" / "numeric_v2" / "packages"
    bundled_root = tmp_path / "bundled"
    bundled_root.mkdir()
    story = numeric_v2_story()
    (bundled_root / "default.json").write_text(
        json.dumps(story, ensure_ascii=False),
        encoding="utf-8",
    )
    registry = NumericV2PackageRegistry(package_root)
    monkeypatch.setattr(numeric_v2_registry, "_DEFAULT_PACKAGE_ROOT", bundled_root)

    def _concurrent_install(payload):
        compiled = registry.compiler.compile(payload)
        target = registry.package_path(compiled.story_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(compiled.json_bytes)
        raise NumericV2PackageExistsError("numeric_v2_package_exists")

    monkeypatch.setattr(registry, "import_package", _concurrent_install)

    registry.ensure_default_packages()

    assert (package_root / ".defaults_initialized").is_file()
    assert registry.list_packages()[0]["story_id"] == "numeric_v2_contract"


def test_numeric_v2_registry_falls_back_to_exclusive_creation(tmp_path, monkeypatch):
    package_root = tmp_path / "theater" / "numeric_v2" / "packages"
    registry = NumericV2PackageRegistry(package_root)

    def no_hard_links(*_args, **_kwargs):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(numeric_v2_registry.os, "link", no_hard_links)
    result = registry.import_package(numeric_v2_story())

    assert result["story_id"] == "numeric_v2_contract"
    assert (package_root / "numeric_v2_contract.json").is_file()
    with pytest.raises(NumericV2PackageExistsError):
        registry.import_package(numeric_v2_story())
