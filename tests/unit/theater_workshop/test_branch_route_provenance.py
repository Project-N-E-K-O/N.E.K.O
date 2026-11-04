"""A reconverged branch receives alternative author plans, never a combined run log."""

from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
from services.theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService
from .test_numeric_v2_generation import _ending_enhancement_case


def _diamond_project():
    nodes = [
        {"id": "start", "route_gates": [
            {"target_node_id": "left"}, {"target_node_id": "right"}]},
        {"id": "left", "route_gates": [{"target_node_id": "join"}]},
        {"id": "right", "route_gates": [{"target_node_id": "join"}]},
        {"id": "join", "route_gates": [{"target_node_id": "ending"}]},
        {"id": "ending", "route_gates": []},
    ]
    states = [
        {"node_id": "left", "owner": "player", "state": "左路领取的蓝色凭证"},
        {"node_id": "right", "owner": "catgirl", "state": "右路领取的红色凭证"},
    ]
    return {"story": {"nodes": nodes}, "authoring": {
        "mainline_node_ids": ["start", "left", "join", "ending"],
        "key_props": [{"id": "token", "name": "凭证", "purpose": "辨认路线", "states": states}],
    }}


@pytest.mark.parametrize("operation,old_claim", [
    ("generate_branch_path", "到来源节点为止已经成立的关键道具台账"),
    ("generate_branch_ending", "已有关键道具的事实台账"),
])
def test_branch_prompt_preserves_diamond_prop_provenance_without_asserting_both_paths_happened(operation, old_claim):
    project = _diamond_project()
    nodes = project["story"]["nodes"]
    states = project["authoring"]["key_props"][0]["states"]
    original = deepcopy(project)
    context = NumericV2BranchService()._base_context(
        project, source=nodes[3], original_route=nodes[3]["route_gates"][0],
        endpoint={"node_id": "ending"}, skipped_nodes=[], continuity_items=[],
        condition_candidates=[], direction="承接汇流后的当前状态。", length=1,
    )
    calls = []
    generator = NumericV2Generator()

    def capture(messages, **_kwargs):
        calls.append(deepcopy(messages))
        if operation == "generate_branch_path":
            # This test inspects the real prompt; fresh path output must now carry
            # an explicit completion contract even though it is not applied here.
            return json.dumps({
                "scenes": [{"completion_facts": [{
                    "id": "token_returned", "description": "凭证已归还原持有人。",
                    "value_type": "bool", "target_value": True, "visibility": "public",
                }]}],
                "transitions": [{}, {"trigger_fact_ids": ["token_returned"]}],
            }, ensure_ascii=False)
        return '{"scenes":[],"transitions":[]}'

    generator.call_llm = capture
    getattr(generator, operation)(context=context)

    assert project == original
    assert len(calls) == 1
    system, user = calls[0]
    assert json.loads(user["content"])["global"]["key_props"][0]["states"] == states
    assert old_claim not in system["content"]
    assert "不是已提交的演绎日志" in system["content"]
    assert "互斥路线" in system["content"]
    assert "node_id" in system["content"]
    assert "fallback_offer 与 accept_input 只请求尚待决定的下一步" in system["content"]
    assert "本幕动作已做或未做两种状态都不强制重做" in system["content"]
    assert "opening_scene、桥段与角色状态须保持一致" in system["content"]


def test_node_enhancement_keeps_alternative_and_current_prop_plans_as_plans():
    project = _diamond_project()
    project["story"]["nodes"][-1].update({
        "type": "ending", "chapter": "分歧之后", "ending_id": "end",
        "story_beat": {"summary": "沿各自来源保留凭证。"},
    })
    props = project["authoring"]["key_props"]
    props[0]["states"].append({
        "node_id": "ending", "owner": "player", "state": "本幕尚待规划的归还后状态",
    })
    original = deepcopy(project)
    generator, _, candidate = _ending_enhancement_case()
    calls = []

    def capture(messages, **_kwargs):
        calls.append(deepcopy(messages))
        return json.dumps(candidate, ensure_ascii=False)

    generator.call_llm = capture
    generator.enhance_node(story=project["story"], node_id="ending", key_props=props)

    assert project == original
    assert len(calls) == 1
    system, user = calls[0]
    assert json.loads(user["content"])["key_props"][0]["states"] == props[0]["states"]
    assert "必须保持已经成立的名称、用途、归属和状态" not in system["content"]
    assert "不是已提交的演绎日志" in system["content"]
    assert "互斥路线" in system["content"]
    assert "node_id" in system["content"]
    assert "fallback_offer 与 accept_input 只请求尚待决定的下一步" in system["content"]
    assert "本幕动作已做或未做两种状态都不强制重做" in system["content"]
    assert "opening_scene、桥段与角色状态须保持一致" in system["content"]
