"""Keep only applicable evaluator protocols without losing authorization rules."""

from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_evaluator import _build_messages
from services.theater.numeric_v2_runtime import NumericV2Engine
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
    data = json.loads(messages[1].content.split("\n", 1)[1])

    assert data["player_input"] == player_input
    assert bool(data["metrics"]) == metrics
    assert ("每个数值每轮最多变化一次" in system) == metrics
    assert ("没有四回合冷却" in system) == metrics
    if not metrics:
        assert "metric_changes 必须为 {}" in system
    has_invitation = invitation in {"active", "withdrawn"}
    assert ("pending_transition" in data) == has_invitation
    assert ("pending_transition.status=withdrawn" in system) == has_invitation
    assert ("若 pending_transition.immediately_previous=false" in system) == has_invitation
    if has_invitation:
        assert data["pending_transition"]["status"] == invitation
        assert f"要和我一起去{destination}吗？" in data["pending_transition"]["visible_performance"]
    else:
        assert "transition_reply_target 固定为 unclear" in system
    # Missing protocols must not strip independent player-action, evidence, fact or ending duties.
    assert "interaction_intent" not in system
    assert "询问下一步安排不代表同意执行" in system
    assert "公开依据只能来自本次访问的实际演出" in system
    assert "不满足主动请求条件则 unclear，不能空口 accept" in system
    assert "natural_ending_ready" in system
    assert "subject、action、object、result" in system
    assert "prop:letter" in data["current_story_beat"]["fact_contract"]["facts"]
