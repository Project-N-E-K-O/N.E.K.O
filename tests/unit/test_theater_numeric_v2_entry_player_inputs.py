"""Authored entry choices replace model suggestions only at public scene entry."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor_module
from services.theater.numeric_v2_actor import NumericV2Actor
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding


SAFE = ["（点头）我明白了。", "（拿起课本）我继续复习。"]
DYNAMIC = ["（犹豫片刻）要我帮你寻找新店面吗？", "（摸出钱包）我替你付租金。"]


def _actor(monkeypatch, payload):
    config = SimpleNamespace(load_characters=lambda: {"当前猫娘": "Lan", "主人": {"昵称": "哥哥"}})
    instance = NumericV2Actor(config)
    calls = []

    async def invoke(*args, **kwargs):
        calls.append(kwargs)
        assert len(calls) == 1, "entry choices must not trigger an extra recommendation call"
        return deepcopy(payload)

    monkeypatch.setattr(instance, "_invoke", invoke)
    monkeypatch.setattr(instance, "_character_profile", lambda: "温和克制。")
    return instance, calls


def _ordinary_payload():
    return {"performance": "（把账本放回柜台）我会自己安排店里的事。", "suggested_inputs": DYNAMIC, "transition_offered": False}


def _transition_payload():
    return {"source_performance": "",
            "bridge_scene_narration": "两人来到廊下，雨声渐远。",
            "target_scene_narration": "廊下亮着一盏灯。",
            "target_performance": "（停在灯下）这里可以坐一会儿。", "suggested_inputs": DYNAMIC}


async def _setup(tmp_path, *, terminal=False, target_entries=SAFE, source_entries=None):
    engine = _engine(ordinary=not terminal)
    # Test Actor independently; compiler acceptance/rejection is covered by Workshop tests.
    if source_entries is not None:
        engine.nodes["start"]["story_beat"]["entry_player_inputs"] = source_entries
    target_id = "ending_leave" if terminal else "middle"
    if target_entries is not None:
        engine.nodes[target_id]["story_beat"]["entry_player_inputs"] = target_entries
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="entry-options", catgirl_binding=_binding(),
        opening_performance={"scene_narration": "门前雨声未停。", "performance": "（看向门口）请进。", "suggested_inputs": []},
    )
    current = await _commit(runtime, current, "我们去廊下看看，好吗？", offer=True)
    return runtime, current


@pytest.mark.asyncio
async def test_opening_replaces_bad_dynamic_choices_and_projects_names(monkeypatch):
    engine = _engine()
    engine.nodes["start"]["story_beat"]["entry_player_inputs"] = ["（点头）小岚，林舟记住了。"]
    payload = {"scene_narration": "柜台旁亮着灯。", **_ordinary_payload()}
    instance, calls = _actor(monkeypatch, payload)
    events = []
    monkeypatch.setattr(actor_module, "trace_event", lambda event, **data: events.append((event, data)))
    result = await instance.generate_opening(engine=engine)
    assert result["suggested_inputs"] == ["（点头）Lan，哥哥记住了。"]
    assert result["performance"] == payload["performance"]
    assert payload["suggested_inputs"] == DYNAMIC
    assert len(calls) == 1 and instance.suggestion_fill_attempt_count == 0
    entry_events = [data for event, data in events if event == "actor.entry_player_inputs_projected"]
    assert entry_events and all(isinstance(value, int) for data in entry_events for value in data.values())


@pytest.mark.asyncio
async def test_authored_choices_are_installed_before_full_output_validation(monkeypatch):
    engine = _engine()
    engine.story["initial_state"]["player_address_known"] = False
    engine.nodes["start"]["story_beat"]["entry_player_inputs"] = SAFE
    payload = {"scene_narration": "柜台旁亮着灯。", **_ordinary_payload()}
    payload["suggested_inputs"] = ["（写下名字）我叫哥哥。"]
    instance, calls = _actor(monkeypatch, payload)
    checked = []
    validate = actor_module._assert_no_unknown_player_address_leak

    def inspect(performance, **kwargs):
        checked.append(deepcopy(performance["suggested_inputs"]))
        validate(performance, **kwargs)

    monkeypatch.setattr(actor_module, "_assert_no_unknown_player_address_leak", inspect)
    result = await instance.generate_opening(engine=engine)
    assert result["suggested_inputs"] == SAFE
    assert checked == [SAFE]
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_opening_without_author_entries_keeps_existing_dynamic_choices(monkeypatch):
    payload = {"scene_narration": "柜台旁亮着灯。", **_ordinary_payload()}
    instance, calls = _actor(monkeypatch, payload)
    result = await instance.generate_opening(engine=_engine())
    assert result["suggested_inputs"] == DYNAMIC
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_entry_choices_are_reparsed_after_runtime_name_projection(monkeypatch):
    engine = _engine()
    engine.nodes["start"]["story_beat"]["entry_player_inputs"] = [
        "（点头）小岚，我记住了。", "（点头）Lan，我记住了。",
    ]
    instance, calls = _actor(monkeypatch, {"scene_narration": "柜台旁亮着灯。", **_ordinary_payload()})
    result = await instance.generate_opening(engine=engine)
    assert result["suggested_inputs"] == ["（点头）Lan，我记住了。"]
    assert len(calls) == 1 and instance.suggestion_fill_attempt_count == 0


@pytest.mark.asyncio
async def test_formal_target_entries_survive_commit_restore_and_fork(tmp_path, monkeypatch):
    runtime, current = await _setup(tmp_path, source_entries=["（看向柜台）来源专用选项。"])
    request = TurnRequestV2("accept", current.session.revision, "好，我们去廊下。")
    outcome = runtime.prepare_turn(current, request, (), transition_intent="accept")
    assert outcome.session.current_node_id == "middle"
    payload = _transition_payload()
    instance, calls = _actor(monkeypatch, payload)
    result = await instance.generate_turn(engine=runtime.engine, session=current.session, outcome=outcome,
        player_input=request.message, character_profile="温和克制。")
    assert result["suggested_inputs"] == SAFE
    assert len(calls) == 1 and instance.suggestion_fill_attempt_count == 0
    committed = await runtime.commit_turn(outcome, result)
    assert committed.session.performance_history[-1]["suggested_inputs"] == SAFE
    assert await runtime.restore_session("entry-options") == committed
    fork = await runtime.fork_session_for_test("entry-options", session_id="entry-fork", through_revision=2)
    assert fork.session.performance_history[-1]["suggested_inputs"] == SAFE
    assert await runtime.restore_session("entry-fork") == fork
    assert committed.session.transition_offered is False
    next_outcome = runtime.prepare_turn(committed,
        TurnRequestV2("entry-choice", committed.session.revision, SAFE[0], input_source="suggestion"), ())
    assert next_outcome.session.current_node_id == "middle"


@pytest.mark.asyncio
async def test_source_entry_choices_do_not_override_ordinary_or_unconfigured_target(tmp_path, monkeypatch):
    runtime, current = await _setup(tmp_path, target_entries=None, source_entries=SAFE)
    for formal in (False, True):
        message = "好，我们去廊下。" if formal else "先说说这里的天气。"
        request = TurnRequestV2(f"scope-{formal}", current.session.revision, message)
        outcome = runtime.prepare_turn(current, request, (), transition_intent="accept" if formal else "unclear")
        assert (outcome.session.current_node_id != current.session.current_node_id) is formal
        instance, calls = _actor(monkeypatch, _transition_payload() if formal else _ordinary_payload())
        result = await instance.generate_turn(engine=runtime.engine, session=current.session, outcome=outcome,
            player_input=message, character_profile="温和克制。")
        assert result["suggested_inputs"] == DYNAMIC
        assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_fill", [False, True])
async def test_entry_choice_equal_to_this_input_is_removed_without_filling(tmp_path, monkeypatch, allow_fill):
    message = "（点头）好，我们去廊下。"
    runtime, current = await _setup(tmp_path, target_entries=[message])
    request = TurnRequestV2("accept", current.session.revision, message)
    outcome = runtime.prepare_turn(current, request, (), transition_intent="accept")
    instance, calls = _actor(monkeypatch, _transition_payload())
    result = await instance.generate_turn(engine=runtime.engine, session=current.session, outcome=outcome,
        player_input=message, character_profile="温和克制。", allow_suggestion_fill=allow_fill)
    assert result["suggested_inputs"] == []
    assert instance.base_suggestion_parse_counts["repeats_player_input"] == 1
    assert instance.suggestion_fill_attempt_count == 0 and len(calls) == 1


@pytest.mark.asyncio
async def test_terminal_still_clears_choices_even_if_uncompiled_field_is_present(tmp_path, monkeypatch):
    runtime, current = await _setup(tmp_path, terminal=True)
    request = TurnRequestV2("accept", current.session.revision, "好，就这么定了。")
    outcome = runtime.prepare_turn(current, request, (), transition_intent="accept")
    assert outcome.session.status == "ended"
    instance, calls = _actor(monkeypatch, _transition_payload())
    result = await instance.generate_turn(engine=runtime.engine, session=current.session, outcome=outcome,
        player_input=request.message, character_profile="温和克制。")
    assert result["suggested_inputs"] == []
    assert instance.suggestion_fill_attempt_count == 0 and len(calls) == 1
