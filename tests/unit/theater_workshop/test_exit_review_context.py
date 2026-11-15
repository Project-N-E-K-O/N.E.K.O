"""Keep exit counterexamples visible without auditing neighboring future scenes twice."""

from tests.utils.theater_prompt import prompt_payload

from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.facts import build_fact_sources
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from .test_numeric_v2_generation import _fact_review_payload, _generation_setup, _quality_story_context


@pytest.mark.parametrize("choice,bridge,agreement,opening", [
    ("玩家可先打电话或去社区核实，也可两者都做。", "玩家已经离开直播间，走进社区值班处。",
     "两人已经同意先核实再编排。", "女主打开录音机，开始播放尚未展示的录音。"),
    ("玩家可留下当面签收或委托快递寄回，两种方式都可完成归还。", "玩家已带着藏品到寄件窗口办理快递。",
     "馆员已清点并签收归还的藏品。", "馆员亮起展柜灯，首次展示藏品的登记信息。"),
])
def test_fact_request_keeps_manual_exit_choices_and_required_results_but_not_neighbor_tasks(
    choice, bridge, agreement, opening,
):
    story, authoring = _quality_story_context()
    previous, source, target = story["nodes"][:3]
    route = source["route_gates"][0]
    route["transition_contract"].update(reason=choice, bridge_scene_narration=bridge,
                                        must_deliver=[bridge], must_preserve=[agreement])
    route["transition_contract"].pop("advance_on_completion", None)
    target["story_beat"]["character_state"]["continuity_from_previous"] = [agreement]
    target["story_beat"]["opening_scene"] = opening
    for neighbor in (previous, target):
        neighbor["story_beat"]["narrative_focus"] = "NEIGHBOR_FUTURE_NARRATIVE" * 500
    turn_goal = deepcopy(target["story_beat"]["goals"][0])
    turn_goal["id"] = "later_independent_task"
    turn_goal["description"] = "NEIGHBOR_TURN_TASK" * 500
    turn_goal["delivery"]["timing"] = "turn"
    target["story_beat"]["goals"].append(turn_goal)
    before = deepcopy((story, authoring))
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), authoring)
    marked, sources = build_fact_sources(context)
    expected = next(node for node in marked["mainline"] if node["id"] == source["id"])
    captured = []

    class Captured(Exception):
        pass

    def model(messages, **options):
        request = prompt_payload(messages[1]["content"])
        if options["operation"] == "numeric_v2_exit_fact_binding":
            return json.dumps({"bindings": [{"route_id": row["route_id"], "claim_ref": claim["ref"],
                "kind": "other", "fact_key": None, "value": None}
                for row in request["routes"] for claim in row["claims"]]})
        assert options["operation"] == "numeric_v2_fact_review"
        if (request["checked_node_ids"] != [source["id"]]
                or request["audit_task"]["kind"] != "transition"):
            return json.dumps(_fact_review_payload(request))
        captured.append(request)
        nodes = {node["id"]: node for node in request["story_outline"]["mainline"]}
        assert nodes[source["id"]] == expected
        assert "NEIGHBOR_FUTURE_NARRATIVE" not in messages[1]["content"]
        assert "NEIGHBOR_TURN_TASK" not in messages[1]["content"]
        assert set(nodes) == {source["id"], target["id"]}
        incoming = next(row for row in request["story_outline"]["entry_routes"]
                        if row["source_node_id"] == previous["id"] and row["target_node_id"] == source["id"])
        assert incoming["completion_contract"] == marked["mainline"][0]["completion_contract"]
        assert "outgoing_routes" not in nodes[target["id"]]
        assert nodes[target["id"]]["character_state"] == marked["mainline"][2]["character_state"]
        assert nodes[target["id"]]["acting_contract"] == marked["mainline"][2]["acting_contract"]
        for original in (choice, bridge, agreement, opening):
            assert original in messages[1]["content"]
        target_opening = nodes[target["id"]]["opening_scene"]
        assert sources[target_opening["ref"]]["quote"] == opening
        raise Captured

    with pytest.raises(Captured):
        NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    assert len(captured) == 1
    assert (story, authoring) == before
