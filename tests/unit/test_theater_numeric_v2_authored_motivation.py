"""Author motivation survives SDK projection without becoming public or settled history.

These test native compilation and prompt transport, not model interpretation.
"""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from main_routers import numeric_theater_router
from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2 import NumericV2Compiler
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_registry import NumericV2PackageRegistry
from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_setup
from tests.unit.theater_workshop.test_numeric_v2_generation import _idea_outline
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator


MOTIVATION = "女主想核实男主当年的决定，但不要求男主留下；她还不知道旧信的真相。"
MESSAGE = "我愿意解释当年的决定，但这不表示我会留下。"


def _story():
    outline = _idea_outline()
    outline["story_protagonist"]["motivation"] = MOTIVATION
    generator = NumericV2Generator()
    story = generator._project_story(
        title="动机投影测试", original_idea="旧信", setup=numeric_v2_setup(),
        outline=outline, tone=["克制"],
    )
    return NumericV2Compiler().compile(story).story


def _case(*, scope="full", restricted=None):
    story = _story()
    for node in story["nodes"]:
        node["story_beat"]["acting_contract"]["persona_scope"] = scope
    if restricted:
        node_id, field, value = restricted
        node = next(node for node in story["nodes"] if node["id"] == node_id)
        node["story_beat"]["acting_contract"][field] = value
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="authored_motivation",
        catgirl_binding={**_binding(), "catgirl_name": "测试猫娘"},
        opening_performance={"performance": "我们先在这里聊聊。", "suggested_inputs": []},
    )
    return engine, session


def _data(messages):
    content = messages[1].content
    return prompt_payload(content)


def _projected(engine, text):
    return NumericV2CastProjection.from_story(
        engine.story, player_name="哥哥", catgirl_name="测试猫娘",
    ).text(text)


def _messages(engine, session, phase, *, review=False):
    if phase == "formal":
        session = replace(session, transition_offered=True)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("next", session.revision, MESSAGE), (),
        transition_intent="accept" if phase == "formal" else "unclear",
    )
    assert (outcome.session.current_node_id != session.current_node_id) is (phase == "formal")
    if review:
        performance = {"performance": "我愿意听你解释。", "suggested_inputs": []}
        if phase == "formal":
            performance = {
                "segments": [
                    {"phase": "source_response", "performance": ""},
                    {"phase": "transition_bridge", "scene_narration": "两人继续核对记录。"},
                    {"phase": "target_opening", "performance": "我们看看眼前的记录。"},
                ],
                "suggested_inputs": [],
            }
        return evaluator._build_transition_judge_messages(
            engine, session, actor_performance=performance, player_input=MESSAGE,
            route_changed=phase in {"opening", "formal"},
            transition_outcome=outcome if phase == "formal" else None,
            completion_fact_review_only=phase == "completion",
        )[0]
    if phase == "opening":
        return actor._opening_messages(
            engine, "说话简短，遇事先听完。", "测试猫娘", "哥哥", max_tokens=10000,
        )
    return actor._turn_messages(
        engine, session, outcome, MESSAGE, "说话简短，遇事先听完。", "测试猫娘", "哥哥",
    )


@pytest.mark.parametrize('scope', ['style_only'])
@pytest.mark.parametrize("phase", ["opening", "ordinary", "formal"])
def test_actor_native_entries_receive_full_cast_motivation(phase, scope):
    engine, session = _case(scope=scope)
    before = deepcopy((engine.story, session.to_dict()))
    data = _data(_messages(engine, session, phase))
    role = data["role"] if phase == "ordinary" else data["acting_context"]["story_role_context"]
    projected = _projected(engine, MOTIVATION)
    assert projected in role
    assert "不要求哥哥留下；她还不知道旧信的真相。" in role
    assert "女主想核实男主" not in role
    assert (engine.story, session.to_dict()) == before


def test_review_receives_same_sdk_role_text_separate_from_public_evidence():
    engine, session = _case()
    before = deepcopy((engine.story, session.to_dict()))
    data = _data(_messages(engine, session, "ordinary", review=True))
    assert data["author_initial_role_context"] == {
        "source": "catgirl_binding.role_overlay",
        "text": _projected(engine, engine.story["catgirl_binding"]["role_overlay"]),
    }
    assert _projected(engine, MOTIVATION) in data["author_initial_role_context"]["text"]
    for field in ("scene_context", "actor_performance", "scene_update", "player_action_projection"):
        assert _projected(engine, MOTIVATION) not in json.dumps(data[field], ensure_ascii=False)
    assert data["player_input"] == MESSAGE
    assert (engine.story, session.to_dict()) == before


@pytest.mark.parametrize("field,value", [("cognition_state", "limited"), ("memory_state", "partial")])
@pytest.mark.parametrize('phase,node_id', [('ordinary', 'mainline_01'), ('formal', 'mainline_02')])
def test_actual_source_or_target_restriction_hides_motivation_for_actor(phase, node_id, field, value):
    engine, session = _case(restricted=(node_id, field, value))
    assert MOTIVATION in engine.story["catgirl_binding"]["role_overlay"]
    data = _data(_messages(engine, session, phase))
    assert "想核实" not in json.dumps(data, ensure_ascii=False)


def test_restricted_future_preview_does_not_hide_current_role_motivation():
    engine, session = _case(restricted=("mainline_02", "memory_state", "partial"))
    data = _data(_messages(engine, session, "ordinary"))
    assert _projected(engine, MOTIVATION) in json.dumps(data, ensure_ascii=False)


def test_real_history_can_change_initial_motivation_without_rewriting_author_text():
    engine, session = _case()
    change = "你已解释清楚，我不再追问当年的决定；你是否留下仍由你选择。"
    session = replace(
        session, revision=1, story_state={**session.story_state, "revision": 1},
        performance_history=({
            "revision": 1, "from_node_id": "mainline_01", "to_node_id": "mainline_01",
            "input_text": "我把当年的决定解释清楚，也暂时不留下。",
            "performance": change,
        },),
    )
    messages = _messages(engine, session, "ordinary")
    data = _data(messages)
    assert change in data["story_so_far"]
    assert _projected(engine, MOTIVATION) in data["role"]
    # This checks the existing prompt's historical precedence, not a model's reaction.
    assert "当前状态承接实际历史" in messages[0].content
    assert "未来规划不是当前事实" in data["role"]
    assert MOTIVATION in engine.story["catgirl_binding"]["role_overlay"]


@pytest.mark.asyncio
async def test_registry_and_public_session_dtos_do_not_publish_private_role_overlay(tmp_path, monkeypatch):
    engine, session = _case()
    assert MOTIVATION in engine.story["catgirl_binding"]["role_overlay"]
    registry = NumericV2PackageRegistry(tmp_path / "packages")
    summary = registry.validate_package(engine.story)

    async def options():
        return {"review": False}

    from services.theater import numeric_v2_options
    monkeypatch.setattr(numeric_v2_options, "aload_theater_module_options", options)
    payload = await numeric_theater_router._numeric_payload(
        SimpleNamespace(engine=engine), SimpleNamespace(session=session, ledger_events=()),
    )
    for public in (summary, payload, numeric_theater_router._public_session(session)):
        text = json.dumps(public, ensure_ascii=False)
        assert "role_overlay" not in text
        assert "想核实" not in text
    assert summary["intro"] == engine.story["intro"]
    assert session.player_address_known is False
    assert payload["story_intro"] == NumericV2CastProjection.from_story(
        engine.story, player_name="你", catgirl_name="测试猫娘",
    ).intro(engine.story)
