"""Generation duties bind actual inputs separately from unexecuted author staging."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_entry_trigger_order import CASES, _case
from tests.utils.theater_prompt import prompt_payload, prompt_reference, serialized_prompt_tokens


def _scope(data):
    if "transition" in data:
        return data["transition"]["generation_scope"]
    return json.loads(data["current_scene"].rsplit("\n本轮生成职责：", 1)[1])


def _assert_sources(data, scope):
    assert scope["writable_subjects"] == ["active_catgirl", "npc", "environment"]
    for source in scope["player_authorization_sources"]:
        assert (source.get("source") in {"current_player_input", "accepted_public_offer"}
                or source.get("filter") == "player_input_only")
        prompt_reference(data, source["ref"])
        assert source["ref"] not in scope["author_reference_refs"]
        assert "entry_prefix" not in source["ref"]
    for ref in scope["author_reference_refs"]:
        prompt_reference(data, ref["ref"] if isinstance(ref, dict) else ref)
    for source in scope["history_records"]:
        prompt_reference(data, source["ref"])
        assert source["status"] == "committed_record"


@pytest.mark.parametrize("topic", CASES)
@pytest.mark.parametrize("known", [False, True])
def test_formal_generation_scope_preserves_author_player_staging_without_authorizing_it(topic, known):
    engine, session, outcome, _ = _case(topic)
    target = engine.nodes[outcome.session.current_node_id]["story_beat"]
    target["character_state"] = {"catgirl_state": "角色正在接近操作台。", "player_state": "玩家检查设备接口。",
        "environment_state": "记录仍在原位。", "continuity_from_previous": [], "scene_boundaries": []}
    original = deepcopy((engine.story, session, outcome))
    player = "一起去看看，但先别替我检查设备。"
    request = actor._turn_messages(engine, session, outcome, player, "细心。", "小岚", "林舟",
                                   player_address_known=known, entry_part="prefix")
    data = prompt_payload(request[1].content)
    scope = _scope(data)
    _assert_sources(data, scope)
    assert scope["cast"]["active_catgirl"] == "小岚"
    assert scope["cast"]["player"] == ("林舟" if known else "玩家")
    assert "transition.target_scene.opening_situation" in scope["author_reference_refs"]
    assert "transition.target_scene.author_entry_plan" in scope["author_reference_refs"]
    assert data["transition"]["target_scene"]["author_entry_plan"]["player_state"] == "玩家检查设备接口。"
    assert data["player_input"] == player
    assert "generation_scope" in request[0].content
    assert "正常共同移动、交接的直接结果可自然承接" in request[0].content
    assert (engine.story, session, outcome) == original


@pytest.mark.parametrize("topic", CASES)
def test_response_scope_uses_actual_prefix_end_without_promoting_it_to_authorization(topic):
    engine, session, outcome, _ = _case(topic)
    prefix = {"status": "uncommitted_this_turn", "blocks": [
        {"type": "narration", "text": topic[3]}, {"type": "narration", "text": topic[2]}]}
    original = deepcopy(prefix)
    request = actor._turn_messages(engine, session, outcome, "我在旁边等。", "细心。", "小岚", "林舟",
                                   entry_part="response", entry_prefix=prefix)
    data = prompt_payload(request[1].content)
    scope = _scope(data)
    _assert_sources(data, scope)
    assert "transition.target_scene.opening_situation" not in scope["author_reference_refs"]
    assert scope["draft_prefix"] == {"ref": "transition.entry_prefix.blocks", "status": "uncommitted_this_turn"}
    assert scope["response_after_ref"] == "transition.entry_prefix.blocks"
    assert data["transition"]["entry_prefix"] == original == prefix


@pytest.mark.parametrize("message", ["请你展示记录。", "我检查接口，你先别操作。", "以后再一起过去。"])
def test_ordinary_scope_keeps_complete_player_original_and_six_part_layout(message):
    engine, session, _, _ = _case(CASES[0])
    outcome = engine.resolve_turn(session, TurnRequestV2("stay", 0, message), ())
    request = actor._turn_messages(engine, session, outcome, message, "细心。", "小岚", "林舟")
    data = prompt_payload(request[1].content)
    assert list(data) == ["role", "current_scene", "pacing", "next_scene", "story_so_far", "player_input"]
    scope = _scope(data)
    _assert_sources(data, scope)
    assert {"ref": "story_so_far", "filter": "player_input_only"} in scope["player_authorization_sources"]
    assert {"ref": "story_so_far", "status": "committed_record"} in scope["history_records"]
    assert scope["author_reference_refs"] == [{"ref": "current_scene", "filter": "author_staging_and_direction_only"}]
    assert data["player_input"] == message


def test_formal_scope_counts_in_existing_fixed_context_budget():
    engine, session, outcome, _ = _case(CASES[0])
    request = actor._turn_messages(engine, session, outcome, "好，我们去。", "细心。", "小岚", "林舟", entry_part="prefix")
    data = prompt_payload(request[1].content)
    scope = _scope(data)
    prefix = request[1].content[:request[1].content.index("======")]
    tokens = serialized_prompt_tokens(request[0].content, prefix + actor.render_prompt_context(data))
    fitted = actor._fit_turn_prompt_data(system_prompt=request[0].content, human_prefix=prefix, data=data,
                                        max_tokens=tokens)
    assert _scope(fitted) == scope
    with pytest.raises(actor.NumericV2ActorError, match="fixed_context_budget_exceeded"):
        actor._fit_turn_prompt_data(system_prompt=request[0].content, human_prefix=prefix, data=data,
                                   max_tokens=tokens - 1)


@pytest.mark.asyncio
async def test_scope_exposes_only_the_actual_accepted_public_offer(tmp_path):
    from tests.unit.test_theater_numeric_v2_target_entry_plan import _case as accepted_case

    runtime, current, outcome, turn, _ = await accepted_case(tmp_path, mode="accept")
    request = actor._turn_messages(runtime.engine, current.session, outcome, turn.message, "细心。", "Lan", "哥哥",
                                   recent_ledger_events=current.ledger_events, entry_part="prefix")
    data = prompt_payload(request[1].content)
    scope = _scope(data)
    _assert_sources(data, scope)
    assert {"ref": "transition.pending_invitation", "source": "accepted_public_offer"} in scope["player_authorization_sources"]
    pending = data["transition"]["pending_invitation"]
    assert pending["revision"] == current.session.performance_history[-1]["revision"]
    assert "要一起去工作桌旁吗？" in json.dumps(data["recent_context"], ensure_ascii=False)


def test_mixed_history_is_a_record_not_a_player_authorization_or_completed_fact():
    from dataclasses import replace

    engine, session, _, _ = _case(CASES[0])
    session = replace(session, revision=1, story_state={**session.story_state, "revision": 1}, performance_history=({
        "revision": 1, "from_node_id": session.current_node_id, "to_node_id": session.current_node_id,
        "input_text": "我只想看看，不操作。", "performance": "玩家已经同意检查全部设备。",
    },))
    outcome = engine.resolve_turn(session, TurnRequestV2("stay", 1, "请你展示记录。"), ())
    data = prompt_payload(actor._turn_messages(engine, session, outcome, "请你展示记录。", "细心。", "小岚", "林舟")[1].content)
    scope = _scope(data)
    assert "玩家已经同意检查全部设备。" in data["story_so_far"]
    assert scope["history_records"] == [{"ref": "story_so_far", "status": "committed_record"}]
    assert scope["player_authorization_sources"] == [
        {"ref": "player_input", "source": "current_player_input"},
        {"ref": "story_so_far", "filter": "player_input_only"},
    ]
    assert "actor_performance" not in json.dumps(scope["player_authorization_sources"])


def test_registered_fact_provenance_remains_in_current_scene_outside_author_filter():
    from dataclasses import replace
    from tests.unit import test_theater_numeric_v2_same_turn_invitation as scenario
    from services.theater.numeric_v2_runtime import NumericV2Engine
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening

    engine = NumericV2Engine.from_mapping(scenario.story_for("档案室"))
    session = engine.create_session(session_id="registered-scope", catgirl_binding=_binding(), opening_performance=_opening())
    state = deepcopy(session.story_state)
    state["facts"][scenario.KEY] = {"value": True, "visibility": "public", "updated_revision": 0,
                                  "source_revision": 0, "client_turn_id": "opening"}
    session = replace(session, story_state=state)
    outcome = engine.resolve_turn(session, TurnRequestV2("stay", 0, "先等等。"), ())
    data = prompt_payload(actor._turn_messages(engine, session, outcome, "先等等。", "细心。", "小岚", "林舟")[1].content)
    assert '"committed":true' in data["current_scene"]
    assert '"committed_value":true' in data["current_scene"]
    assert scenario.KEY in data["current_scene"]
    assert _scope(data)["author_reference_refs"] == [{"ref": "current_scene", "filter": "author_staging_and_direction_only"}]
