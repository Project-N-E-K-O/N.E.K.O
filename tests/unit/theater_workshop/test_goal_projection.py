"""Author entry points retain the same formal goal contract when projection is shared."""
from copy import deepcopy

import pytest

from theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
from theater_workshop.sdk.numeric_v2_branch import NumericV2BranchError, NumericV2BranchService


@pytest.mark.parametrize("kind,owner,output", [
    ("catgirl_dialogue", "catgirl", "performance_dialogue"),
    ("catgirl_action", "catgirl", "performance_action"),
    ("environment_fact", "environment", "scene_update"),
    ("player_action", "player", "player_input"),
    ("shared_agreement", "shared", "shared"),
    ("semantic_state", "shared", "evaluator"),
])
@pytest.mark.parametrize("policy", ["unchanged", "required", "optional", "forbidden"])
@pytest.mark.parametrize("mode", ["exact", "semantic"])
def test_goal_projection_preserves_ids_sources_evidence_and_legacy_effects(kind, owner, output, policy, mode):
    goals = [
        {"owner": "environment", "delivery_type": "environment_fact",
         "description": " 开场事实 ", "evidence_mode": "semantic", "anchors": [],
         "sources": ["opening"], "timing": "opening", "dialogue_policy_after": "unchanged"},
        {"owner": owner, "delivery_type": kind, "description": " 你确认原文 ",
         "evidence_mode": mode, "anchors": [" 原文,} "] if mode == "exact" else [],
         "sources": ["previous_goal", "player_input", "opening"],
         "timing": "turn", "dialogue_policy_after": policy},
    ]
    if kind == "semantic_state" and mode == "exact":
        with pytest.raises(NumericV2BranchError) as error:
            NumericV2BranchService._validate_ordered_goals(goals, path="ordered_goals")
        assert error.value.details["path"] == "ordered_goals[1].evidence_mode"
        return
    original = deepcopy(goals)
    expected = [
        {"id": "scene_01_goal_01", "owner": "environment", "description": "开场事实",
         "evidence": {"mode": "semantic", "anchors": []},
         "delivery": {"type": "environment_fact", "output_field": "scene_update",
                      "source_ids": ["opening.scene_01"], "timing": "opening"}},
        {"id": "scene_01_goal_02", "owner": owner, "description": "你确认原文",
         "evidence": {"mode": mode, "anchors": ["原文,}"] if mode == "exact" else []},
         "delivery": {"type": kind, "output_field": output,
                      "source_ids": ["goal.scene_01_goal_01", "runtime.player_input", "opening.scene_01"],
                      "timing": "turn"}},
    ]
    if policy != "unchanged":
        expected[1]["delivery"]["state_effects"] = {"dialogue_policy": policy}
    normalized = NumericV2BranchService._validate_ordered_goals(goals, path="ordered_goals")
    assert NumericV2Generator._project_chapter_goals("scene_01", {"ordered_goals": goals}) == expected
    assert NumericV2BranchService._project_goals("scene_01", normalized) == expected
    assert goals == original
