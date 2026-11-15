"""Fixed information is not evidence that the next invitation was published."""

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_context import current_entry_fixed_narrations
from services.theater.numeric_v2_fixed_narration import add_entry
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for
from tests.utils.theater_prompt import prompt_payload


@pytest.mark.parametrize("destination,information,paraphrase", [
    ("社区值班处", "录音里交付了听众的点歌请求。", "要不要和我到社区值班处了解一下？"),
    ("馆藏室", "展柜里交付了藏品的登记信息。", "我们一起去馆藏室看看，好不好？"),
])
@pytest.mark.parametrize("entry_kind", ["information", "paraphrase", "literal"])
def test_unconfirmed_entry_invitation_keeps_the_author_offer_for_comparison(
    destination, information, paraphrase, entry_kind,
):
    story = story_for(destination)
    source = story["nodes"][0]
    offer = source["route_gates"][1]["transition_contract"]["fallback_offer"]
    for route in source["route_gates"]:
        route["transition_contract"]["fallback_offer"] = offer
    original = {"information": information, "paraphrase": paraphrase, "literal": offer}[entry_kind]
    source["story_beat"]["fixed_narrations"] = [{
        "id": "entry_original", "text": original, "trigger": {"type": "entry"},
        "after": [], "required_before_exit": True,
    }]
    source["completion_contract"] = {"all": [{"fixed_narration_id": "entry_original"}]}
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="entry-comparison", catgirl_binding=_binding(),
        opening_performance=add_entry(engine.nodes["start"], _opening(), _binding(), True),
    )
    message = "这项内容已经听清了，接下来怎么安排？"
    outcome = engine.resolve_turn(session, TurnRequestV2("next", 0, message), (), scene_complete=True)
    assert outcome.route is None
    assert engine.completion_contract_satisfied(session) is True
    system, human = _turn_messages(engine, session, outcome, message, "沉着。", "小岚", "玩家")
    data = prompt_payload(human.content)
    assert original in current_entry_fixed_narrations(session)
    if entry_kind == "literal":
        assert "本次入幕原文已实际公开当前出口的邀约" in system.content
        assert "当前出口可用的作者邀约原文" not in data["next_scene"]
    else:
        assert offer in data["next_scene"]
        assert "原文已公开同一安排时只承接" in system.content
        assert "确实尚未公开且玩家未暂缓时" in system.content
