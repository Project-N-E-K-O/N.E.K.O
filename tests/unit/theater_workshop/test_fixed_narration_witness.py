"""固定旁白条件的证人来源诊断：以猫娘自身动作为准时提示自证触发风险（问题2.141 B1）。"""  # noqa: DOCSTRING_CJK

from theater_workshop.sdk.numeric_v2_analysis import analyze_numeric_v2_story

CODE = "fixed_narration_condition_actor_witnessed"


def _story(fixed_narrations):
    return {
        "schema": "neko.story.numeric.v2",
        "start_node_id": "start",
        # 真实剧本常不声明隐藏数值；该诊断必须仍然生效（此前的提前返回会跳过它）。
        "metric_schema": {},
        "nodes": [{
            "id": "start",
            "type": "scene",
            "route_gates": [],
            "story_beat": {"opening_scene": "组装台旁。", "fixed_narrations": fixed_narrations},
        }],
    }


def _piece(condition, trigger_type="condition"):
    trigger = {"type": trigger_type}
    if trigger_type == "condition":
        trigger["condition"] = condition
    return {"id": "logs", "text": "旧日志。", "trigger": trigger, "after": [], "required_before_exit": False}


def _codes(story):
    return [warning for warning in analyze_numeric_v2_story(story) if warning.code == CODE]


def test_catgirl_subject_condition_is_reported_without_metric_schema():
    warnings = _codes(_story([_piece("新载体的手已经实际接触旧铭牌，或已经实际接过、拿起旧铭牌。")]))
    assert len(warnings) == 1
    assert warnings[0].path == "nodes[0].story_beat.fixed_narrations[0].trigger.condition"
    assert "entry" in warnings[0].message


def test_player_verifiable_condition_is_not_reported():
    assert _codes(_story([_piece("玩家已经把铭牌递到猫娘手中，或已放入货物槽。")])) == []


def test_entry_trigger_is_not_reported():
    assert _codes(_story([_piece("", trigger_type="entry")])) == []


def test_warning_also_fires_on_the_metric_analyzed_path():
    # 声明了隐藏数值时走完整可达性分析路径，该诊断同样必须保留。
    story = _story([_piece("猫娘已经实际接过旧照片。")])
    story["metric_schema"] = {"trust": {"min": 0, "max": 10, "initial": 5,
                                        "per_turn_limit": {"increase": 2, "decrease": 2}}}
    assert len([w for w in analyze_numeric_v2_story(story) if w.code == CODE]) == 1
