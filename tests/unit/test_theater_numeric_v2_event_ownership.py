"""Generation contracts assign an event once without deleting legitimate result text."""

import json

from services.theater.numeric_v2_actor_output import _parse_actor_suggestions, _parse_output
from services.theater.numeric_v2_performance import performance_content_blocks


def test_single_grounded_option_is_valid_and_does_not_report_a_count_defect():
    diagnostics = {}
    option = "（停下来等她处理完）"
    assert _parse_actor_suggestions([option], diagnostics=diagnostics) == [option]
    assert diagnostics["accepted_items"] == 1
    assert diagnostics["insufficient_valid_items"] == 0


def test_event_ownership_contract_preserves_actual_npc_and_own_action_without_rewriting():
    """A positive control protects result text: no fuzzy same-turn semantic deletion."""
    raw = {
        "scene_update": "管理员说：可以，请只打开外层盖板。",
        "performance": "（打开外层盖板，盖板下的指示灯显露）我先看看这里。",
        "suggested_inputs": ["（等她看清楚）"], "transition_offered": False,
    }
    parsed = _parse_output(json.dumps(raw, ensure_ascii=False))
    blocks = performance_content_blocks(parsed)
    assert [block["text"] for block in blocks] == [
        raw["scene_update"], "打开外层盖板，盖板下的指示灯显露", "我先看看这里。",
    ]
    assert parsed["suggested_inputs"] == raw["suggested_inputs"]


def test_literal_label_failure_remains_rejected_without_rewriting_player_intent():
    """Replay the observed shape defect; do not reinterpret text outside a label."""
    diagnostics = {}
    assert _parse_actor_suggestions([
        "（玩家动作）走到旧收音机旁查看", "那我们先看看有哪些照片可选吧",
        "（玩家对白）好的，那就按这个时间来安排",
    ], diagnostics=diagnostics) == ["那我们先看看有哪些照片可选吧"]
    assert diagnostics["placeholder_item"] == 2
    assert diagnostics["mixed_shape_invalid"] == 0
    concrete = ["（抬眼）你愿意多说一点吗？", "（保持安静）"]
    assert _parse_actor_suggestions(concrete) == concrete
