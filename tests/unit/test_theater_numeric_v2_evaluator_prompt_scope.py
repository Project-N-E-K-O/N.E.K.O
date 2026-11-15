"""Keep only applicable evaluator protocols without losing authorization rules."""

from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_evaluator import _build_messages, _parse_output
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.utils.theater_prompt import pending_transition_text, prompt_payload, prompt_reference, without_public_destination_indexes
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def _context(*, metrics, invitation, destination):
    story = numeric_v2_story()
    if not metrics:
        story["metric_schema"] = {}
        story["initial_state"]["metrics"] = {}
        story["nodes"] = story["nodes"][:2]
        story["endings"] = story["endings"][:1]
        story["nodes"][0]["route_gates"] = story["nodes"][0]["route_gates"][:1]
        story["nodes"][0]["route_gates"][0]["conditions"] = {"all": []}
    story["fact_contract"] = {"facts": {
        "prop:letter": {"value_type": "string", "visibility": "public"},
    }}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="prompt_scope", catgirl_binding={"catgirl_name": "测试猫娘"},
        opening_performance={"performance": f"这条路通往{destination}。"},
    )
    if invitation != "missing":
        offer = {
            "revision": 1, "from_node_id": "start", "to_node_id": "start",
            "transition_offered": True, "transition_offer_presented": True,
            "performance": f"要和我一起去{destination}吗？",
            "suggested_inputs": [f"好，我们去{destination}。"],
        }
        withdrawn = {
            "revision": 2, "from_node_id": "start", "to_node_id": "start",
            "transition_offered": False, "input_text": "先不去，等等。",
            "performance": "好，先留在这里。",
        }
        if invitation == "invalidated":
            withdrawn["transition_offer_invalidated"] = True
        active = invitation == "active"
        session = replace(
            session, revision=1 if active else 2, node_turn_count=1 if active else 2,
            transition_offered=active,
            performance_history=(offer,) if active else (offer, withdrawn),
        )
    return engine, session


@pytest.mark.parametrize("destination", ["长街寻找旧信", "轨道站核对星图"])
@pytest.mark.parametrize("metrics", [False, True])
@pytest.mark.parametrize("invitation", ["missing", "active", "withdrawn", "invalidated"])
def test_evaluator_protocols_follow_available_metrics_and_invitation(
    destination, metrics, invitation,
):
    engine, session = _context(metrics=metrics, invitation=invitation, destination=destination)
    player_input = f"我准备去{destination}，但先问清楚，尚未出发。"
    messages = _build_messages(engine, session, player_input)
    system = messages[0].content
    data = prompt_payload(messages[1].content)

    assert data["player_input"] == player_input
    assert bool(data["metrics"]) == metrics
    has_invitation = invitation in {"active", "withdrawn"}
    focused_ending = "natural_ending_context" in data["transition_preview"] and not has_invitation
    if focused_ending:
        assert "每项最多一次" in system and "新的行为可连续计分" in system
        assert "没有依据或 metrics 为空则 metric_changes={}" in system
    else:
        assert ("每个数值每轮最多变化一次" in system) == metrics
        assert ("没有四回合冷却" in system) == metrics
        if not metrics:
            assert "metric_changes 必须为 {}" in system
    assert ("pending_transition" in data) == has_invitation
    assert ("pending_transition.status=withdrawn" in system) == has_invitation
    assert ("若 pending_transition.immediately_previous=false" in system) == has_invitation
    if has_invitation:
        assert data["pending_transition"]["status"] == invitation
        assert f"要和我一起去{destination}吗？" in pending_transition_text(data)
    else:
        assert "transition_reply_target=unclear" in system
    # Missing protocols must not strip independent player-action, evidence, fact or ending duties.
    assert "interaction_intent" not in system
    if focused_ending:
        assert "提问、准备或含糊同意都不构成公开授权" in system
        assert "source=performance" in system
        assert "current_visit=true 或 current_entry_bridge=true" in system
        assert "其余 transition_intent=unclear" in system
        assert "逐字摘录该邀约供纠正" not in system
    else:
        assert "询问下一步安排不代表同意执行" in system
        assert "公开依据只能来自本次访问的实际演出" in system
        assert "public_destination_index只在initiate或纠正不符邀约时选择" in system
        assert "作者方向、程序进出场记录、推荐按钮" in system
        assert "不满足主动请求条件则 unclear，不能空口 accept" in system
        assert "保持 unclear、exit_matches=false，public_destination_index选择该邀约供纠正。其他 unclear 填-1" in system
    assert "natural_ending_ready" in system
    assert "subject、action、object、result" in system
    assert "prop:letter" in data["current_story_beat"]["fact_contract"]["facts"]


def test_retrieved_entry_bridge_has_same_permission_in_both_prompt_rules(monkeypatch):
    engine, session = _context(metrics=False, invitation="missing", destination="轨道站")
    bridge = {"revision": 0, "source": "performance", "current_visit": False,
              "current_entry_bridge": True, "text": "两人停在轨道站的入口旁。"}
    monkeypatch.setattr("services.theater.numeric_v2_evaluator.history_evidence", lambda *args, **kwargs: [bridge])
    system, human = _build_messages(engine, session, "我们去刚才说的轨道站。")
    assert without_public_destination_indexes(prompt_payload(human.content)["history_evidence"]) == [bridge]
    assert system.content.count("current_visit=true 或 current_entry_bridge=true") == 2


@pytest.mark.parametrize("player_input,exit_matches", [
    ("（走到石桌旁拉开椅子）好，就在这儿弄。你确认一下折页夹还在不在？", False),
    ("好，咱们到放着夹子的那张共用桌，把这些纸收成一本。", True),
])
def test_appended_invitation_is_bound_to_its_exact_public_line(player_input, exit_matches):
    engine, session = _context(metrics=False, invitation="active", destination="共用长桌")
    local_response = "（顺着你的目光看向树荫下的石桌）行，那就去那边坐定，看看折页夹够不够用。"
    invitation = "带上拓样去长桌，用那只折页夹做成册，好吗？"
    origin = {**session.performance_history[0], "performance": local_response + "\n" + invitation,
              "transition_offer_start": len(local_response) + 1,
              "transition_offer_end": len(local_response) + 1 + len(invitation)}
    session = replace(session, performance_history=(origin,))
    system, human = _build_messages(engine, session, player_input)
    data = prompt_payload(human.content)
    pending = data["pending_transition"]
    assert pending["content_ref_final_line"] is True
    assert prompt_reference(data, pending["content_ref"]).splitlines()[-1] == invitation
    assert "visible_performance" not in pending
    assert human.content.count(invitation) == 1
    assert data["player_input"] == player_input
    assert "同义改写可相符" in system.content
    assert "本轮明确去别处时，即使行动、道具或分工相同" in system.content
    assert "同轮其他对白不代替邀约" in system.content
    result = _parse_output(json.dumps({
        "scene_complete": False, "metric_changes": {}, "exit_matches": exit_matches,
        "exit_comparison": "明确同意同一张桌。" if exit_matches else "石桌和原邀约的长桌不一致。",
        "transition_intent": "accept", "transition_reply_target": "pending_transition",
    }), engine, player_input, session)
    assert result.transition_intent == ("accept" if exit_matches else "unclear")
