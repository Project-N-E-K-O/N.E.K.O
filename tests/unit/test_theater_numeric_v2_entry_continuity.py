"""Author continuity reaches ordinary play without becoming settled history.

These verify native SDK/runtime/prompt transport, not model interpretation.
"""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2 import MAX_ACTOR_PROMPT_FIELD_TOKENS, NumericV2Compiler
from services.theater.numeric_v2_budget import numeric_v2_actor_budget
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.theater_workshop.test_numeric_v2_generation import _generation_setup, _idea_outline
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator


CONTINUITY = [
    "延续锚 C047：女主暂时代男主保管密封盒，但尚未获准打开。",
    "延续锚 T018：检视台仍是三月十五日凌晨，不能据此推定已完成检修。",
]
ACTUAL_HISTORY = "密封盒已经交还给你，封条仍在；我们只移到桌边，尚未打开。"
MESSAGE = "我们先核对封条，可以吗？"


def _story(continuity=CONTINUITY, *, scope="full", restricted=None, known=False, first_continuity=False):
    outline = _idea_outline()
    if known:
        outline["relationship_arc"]["stages"][0]["address_state"] = "known_before_story"
    for index, stage in enumerate(outline["character_state_arc"]["stages"]):
        # Native transition tests keep the author's first-act-empty convention.
        # Isolated builder/budget cases may use a compiler-valid synthetic first
        # state; they do not claim complete author-outline semantic validation.
        stage["continuity_from_previous"] = list(continuity) if index or first_continuity else []
        stage["acting_contract"]["persona_scope"] = scope
        if restricted:
            stage["acting_contract"][restricted[0]] = restricted[1]
    story = NumericV2Generator()._project_story(
        title="入幕延续投影", original_idea="核对旧记录", setup=_generation_setup(),
        outline=outline, tone=outline["tone"],
    )
    return NumericV2Compiler().compile(story).story


def _session(engine):
    return engine.create_session(
        session_id="entry-continuity", catgirl_binding=_binding(),
        opening_performance={"performance": "先在门边商量。", "suggested_inputs": []},
    )


def _request(engine, session, *, profile="说话简短。", address="你"):
    outcome = engine.resolve_turn(
        session, TurnRequestV2("ask", session.revision, MESSAGE), (),
    )
    return actor._turn_messages(
        engine, session, outcome, MESSAGE, profile, "Lan", address,
        player_address_known=address != "你",
    )


def _data(messages):
    text = messages[1].content
    return prompt_payload(text)


def _projected(engine, values, address="你"):
    return NumericV2CastProjection.from_story(
        engine.story, player_name="玩家" if address == "你" else address, catgirl_name="Lan",
    ).value(values)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["full", "style_only"])
@pytest.mark.parametrize("address", ["你", "哥哥"])
async def test_sdk_transition_restore_and_fork_keep_continuity_as_author_plan(tmp_path, scope, address):
    engine = NumericV2Engine.from_mapping(_story(scope=scope, known=address != "你"))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="entry-continuity", catgirl_binding=_binding(),
        opening_performance={"performance": "先在门边商量。", "suggested_inputs": []},
    )
    moved = runtime.prepare_turn(
        current, TurnRequestV2("move", 0, "我们去下一处。"), (),
        transition_intent="initiate",
    )
    assert moved.session.current_node_id == "mainline_02"
    current = await runtime.commit_turn(moved, engine.finalize_transition_performance(
        moved, {
            "source_performance": "好。",
            "bridge_scene_narration": "两人来到桌边。",
            "target_scene_narration": "窗边有一张桌子。",
            "target_performance": ACTUAL_HISTORY,
            "suggested_inputs": [],
        }, target_opening="窗边有一张桌子。",
    ))
    assert current.session.player_address_known is (address != "你")
    saved = runtime.store._path(current.session.session_id).read_bytes()
    restored = await runtime.restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(
        current.session.session_id, session_id="entry-continuity-fork", through_revision=1,
    )
    assert restored == current
    assert forked.session.performance_history == current.session.performance_history
    for stored in (current, restored, forked):
        before = deepcopy((engine.story, stored))
        data = _data(_request(engine, stored.session, address=address))
        projected = _projected(engine, CONTINUITY, address)
        assert all(item in data["current_scene"] for item in projected)
        assert data["current_scene"].index(projected[0]) < data["current_scene"].index(projected[1])
        assert "作者入幕延续规划" in data["current_scene"]
        assert "本轮授权" in data["current_scene"]
        assert "不是已发生事实、任务或新增权限" in data["current_scene"]
        # The current possession comes from actual play, not the earlier plan.
        assert ACTUAL_HISTORY in data["story_so_far"]
        assert all(item not in data["story_so_far"] for item in projected)
        assert "女主暂代男主" not in data["role"]
        assert data["player_input"] == MESSAGE
        review_messages, _ = evaluator._build_transition_judge_messages(
            engine, stored.session, player_input=MESSAGE,
            actor_performance={"performance": "先核对封条。", "suggested_inputs": []},
        )
        review = _data(review_messages)
        # Review's existing binding projection uses the configured address;
        # Actor's speaking projection must still hide an unknown address.
        review_projected = _projected(engine, CONTINUITY, stored.session.catgirl_binding["player_address"])
        assert review["current_scene"]["author_entry_plan"]["continuity_from_previous"] == review_projected
        assert all(item not in json.dumps(review["scene_context"], ensure_ascii=False) for item in review_projected)
        assert (engine.story, stored) == before
    assert runtime.store._path(current.session.session_id).read_bytes() == saved


@pytest.mark.parametrize("field,value", [
    ("cognition_state", "limited"), ("memory_state", "partial"),
])
def test_continuity_uses_existing_state_visibility_without_restoring_hidden_identity(field, value):
    engine = NumericV2Engine.from_mapping(_story(restricted=(field, value), first_continuity=True))
    session = _session(engine)
    before = deepcopy((engine.story, session.to_dict()))
    data = _data(_request(engine, session))
    role = data["role"]
    assert all(item in data["current_scene"] for item in _projected(engine, CONTINUITY))
    assert value in role
    # Existing state plans remain readable; backstage identity and motivation
    # still pass through the existing cognition gate instead of this new line.
    assert "多年后回乡处理旧屋" not in role
    assert "确认玩家是否值得再次信任" not in role
    assert "雨季小镇与保存旧信的花店。" not in data["current_scene"]
    assert (engine.story, session.to_dict()) == before


@pytest.mark.parametrize("legacy", [False, True])
def test_empty_or_legacy_absent_state_adds_no_continuity_noise(legacy):
    story = _story([])
    if legacy:
        for node in story["nodes"]:
            node["story_beat"].pop("character_state")
    engine = NumericV2Engine.from_mapping(story)
    messages = _request(engine, _session(engine))
    assert "作者入幕延续规划" not in _data(messages)["current_scene"]


def _long_continuity():
    text = "女主延续记录："
    suffix = "；但未取得男主许可，也没有完成检修。"
    while actor.count_tokens(text + " detail" + suffix) <= MAX_ACTOR_PROMPT_FIELD_TOKENS:
        text += " detail"
    text += suffix
    assert 360 < actor.count_tokens(text) <= MAX_ACTOR_PROMPT_FIELD_TOKENS
    return text


def test_compiler_valid_continuity_keeps_complete_tail_negation():
    text = _long_continuity()
    engine = NumericV2Engine.from_mapping(_story([text], first_continuity=True))
    scene = _data(_request(engine, _session(engine)))["current_scene"]
    projected = _projected(engine, [text])[0]
    assert projected in scene
    assert projected.endswith("；但未取得玩家许可，也没有完成检修。")


@pytest.mark.asyncio
async def test_continuity_uses_the_existing_single_actor_request(monkeypatch):
    engine = NumericV2Engine.from_mapping(_story(first_continuity=True))
    session = _session(engine)
    outcome = engine.resolve_turn(session, TurnRequestV2("ask", 0, MESSAGE), ())
    calls = []
    performance = {
        "performance": "封条的边缘还贴着，先从这一面看吧。",
        "suggested_inputs": ["需要我念出记录吗？"], "fact_candidates": [],
    }

    async def invoke(self, messages, **kwargs):
        calls.append((messages, kwargs))
        return deepcopy(performance)

    monkeypatch.setattr(actor.NumericV2Actor, "_invoke", invoke)
    before = deepcopy((engine.story, session.to_dict()))
    result = await actor.NumericV2Actor(object()).generate_turn(
        engine=engine, session=session, outcome=outcome, player_input=MESSAGE,
        character_profile="说话简短。", allow_suggestion_fill=False,
    )
    assert len(calls) == 1
    assert all(item in _data(calls[0][0])["current_scene"] for item in _projected(engine, CONTINUITY))
    assert calls[0][1]["max_input_tokens"] == numeric_v2_actor_budget(session.actor_budget_profile)["input_max_tokens"]
    assert calls[0][1]["max_output_tokens"] == actor.NUMERIC_V2_ACTOR_TURN_MAX_OUTPUT_TOKENS
    assert result == performance
    assert (engine.story, session.to_dict()) == before


@pytest.mark.asyncio
async def test_continuity_counts_against_real_fixed_budget_before_any_model_call(monkeypatch):
    short_engine = NumericV2Engine.from_mapping(_story([]))
    long_engine = NumericV2Engine.from_mapping(_story([_long_continuity()], first_continuity=True))
    short_session, long_session = _session(short_engine), _session(long_engine)
    cap = numeric_v2_actor_budget(short_session.actor_budget_profile)["input_max_tokens"]

    def profile(repetitions):
        return "说话简短，耐心倾听。" * repetitions

    # Locate the real fixed-context limit; do not raise the production budget or
    # truncate author text merely to make a request fit.
    low, high = 0, 2000
    while low + 1 < high:
        mid = (low + high) // 2
        try:
            _request(short_engine, short_session, profile=profile(mid))
        except actor.NumericV2ActorError as exc:
            assert str(exc) == "numeric_v2_actor_fixed_context_budget_exceeded"
            high = mid
        else:
            low = mid
    short_messages = _request(short_engine, short_session, profile=profile(low))
    assert sum(actor.count_tokens(m.content) for m in short_messages) <= cap
    calls = []

    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    async def factory(*args, **kwargs):
        calls.append(kwargs)
        raise AssertionError("over-budget request must not create a client")

    monkeypatch.setattr(actor, "_model_config", config)
    monkeypatch.setattr(actor, "create_chat_llm_async", factory)
    outcome = long_engine.resolve_turn(
        long_session, TurnRequestV2("ask", 0, MESSAGE), (),
    )
    before = deepcopy((long_engine.story, long_session.to_dict()))
    with pytest.raises(actor.NumericV2ActorError, match="fixed_context_budget_exceeded"):
        await actor.NumericV2Actor(object()).generate_turn(
            engine=long_engine, session=long_session, outcome=outcome, player_input=MESSAGE,
            character_profile=profile(low), allow_suggestion_fill=False,
        )
    assert calls == []
    assert (long_engine.story, long_session.to_dict()) == before
