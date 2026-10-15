"""Project committed narrative state into model inputs without exposing unknown facts."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_narrative import apply_narrative_record, project_narrative_state
from utils.tokenize import count_tokens


def _append(state, name, holder, *, private=False):
    revision = state["revision"] + 1 if state is not None else 1
    quote = f"接过{name}" if holder == "player" else f"保管员收好{name}"
    event = {
        "phase": "player_input" if holder == "player" else "turn",
        "subject": "player" if holder == "player" else "person:保管员",
        "kind": "action", "summary": quote,
        "changes": [{"entity": f"item:{name}", "attribute": "holder", "value": holder}],
        "source": "player_input" if holder == "player" else "scene_narration", "quote": quote,
        "known_to": ["player"] if private else ["player", "character"],
        **({"authorization_quote": quote} if holder == "player" else {}),
    }
    record = {"schema": "numeric_v2_narrative_record_v1", "coverage": "confirmed_changes",
              "events": [event], "proposal_count": 1}
    return apply_narrative_record(
        state, record, revision=revision, node_id="old", from_node_id="old",
        performance={"scene_narration": quote}, message=quote if holder == "player" else "",
    )


def _session(*, all_player=False):
    state = _append(None, "旧信匣", "player")
    for index in range(1, 301):
        state = _append(state, f"木匣{index:04d}号", "player" if all_player else "person:保管员")
    state = _append(state, "隐秘纸条", "player", private=True)
    state = apply_narrative_record(
        state, {"schema": "numeric_v2_narrative_record_v1", "coverage": "confirmed_no_change",
                "events": [], "proposal_count": 0},
        revision=state["revision"] + 1, node_id="new", from_node_id="old", performance={}, message="",
    )
    return SimpleNamespace(narrative_state=state, current_node_id="new")


@pytest.fixture(scope="module")
def large_session():
    return _session()


def _entities(projection):
    return set(projection["current"]) | set(projection["last_known"])


def test_hundreds_of_departed_npc_items_do_not_fill_fixed_actor_context(large_session):
    original = deepcopy(large_session.narrative_state)
    projection = project_narrative_state(large_session)
    retained = _entities(projection)
    metadata = projection["projection"]

    assert len(retained - {"player", "character", "item:旧信匣"}) <= 16
    assert projection["current"]["item:旧信匣"]["holder"]["value"] == "player"
    assert "item:木匣0001号" not in retained
    assert metadata["retained_entities"] == len(retained)
    assert metadata["omitted_entities"] == 301 - len(retained)
    assert len(projection["recent_events"]) <= 4
    assert metadata["omitted_recent_events"] > 0
    assert metadata["quotes_omitted"] >= 0
    assert metadata["over_budget"] is False
    assert count_tokens(json.dumps(projection, ensure_ascii=False, separators=(",", ":"))) <= 1600
    assert large_session.narrative_state == original
    assert len(large_session.narrative_state["entities"]) == 302


def test_current_input_prioritizes_an_early_item_without_creating_an_alias(large_session):
    projection = project_narrative_state(large_session, focus="木匣0001号现在由谁保管？")

    assert projection["current"]["item:木匣0001号"]["holder"]["value"] == "person:保管员"
    assert projection["current"]["item:旧信匣"]["holder"]["value"] == "player"
    assert projection["projection"]["omitted_entities"] == 301 - len(_entities(projection))
    unmatched = project_narrative_state(large_session, focus="木匣0001现在在哪里？")
    assert "item:木匣0001" not in _entities(unmatched)
    assert "item:木匣0001号" not in _entities(unmatched)
    assert len(large_session.narrative_state["entities"]) == 302


def test_private_item_and_event_do_not_leak_through_focus_or_omission_counts(large_session):
    without_private = deepcopy(large_session.narrative_state)
    without_private["entities"].pop("item:隐秘纸条")
    without_private["recent_events"] = [
        event for event in without_private["recent_events"] if "character" in event["known_to"]
    ]
    public_session = SimpleNamespace(narrative_state=without_private, current_node_id="new")
    focused = project_narrative_state(large_session, focus="隐秘纸条呢？")
    public = project_narrative_state(public_session, focus="隐秘纸条呢？")

    assert "隐秘纸条" not in json.dumps(focused, ensure_ascii=False)
    for field in ("retained_entities", "omitted_entities", "omitted_recent_events", "quotes_omitted"):
        assert focused["projection"][field] == public["projection"][field]


@pytest.mark.parametrize("focus", ["出山前检查旧信匣", "放学前检查木匣0001号"])
def test_many_held_items_are_bounded_with_explicit_omissions(focus):
    session = _session(all_player=True)
    original = deepcopy(session.narrative_state)
    projection = project_narrative_state(session, focus=focus)
    metadata = projection["projection"]
    retained = _entities(projection)

    assert count_tokens(json.dumps(projection, ensure_ascii=False, separators=(",", ":"))) <= 1600
    assert metadata["over_budget"] is False
    assert metadata["retained_entities"] == len(retained)
    assert metadata["omitted_entities"] == 301 - len(retained)
    assert metadata["omitted_essential_entities"] == metadata["omitted_entities"] > 0
    name = "旧信匣" if "旧信匣" in focus else "木匣0001号"
    assert projection["current"][f"item:{name}"]["holder"]["value"] == "player"
    for entity in retained:
        holder = projection["current"][entity]["holder"]
        assert holder["source_event"]
        assert "quote" not in holder
    assert session.narrative_state == original
    from services.theater.numeric_v2_actor import _fit_turn_prompt_data
    data = {"story_context": {"narrative_state": projection}, "player_input": focus,
            "recent_context": [{"revision": session.narrative_state["revision"],
                                "player_input": focus, "performance": "请继续。"}]}
    packed = _fit_turn_prompt_data(system_prompt="system", human_prefix="context:",
                                  data=data, max_tokens=4800)
    assert packed["story_context"]["narrative_state"] == projection
    assert packed["player_input"] == focus


def test_legacy_partial_current_flag_is_exposed_only_as_last_known():
    state = _append(None, "旧信匣", "player")
    state["coverage"] = "partial"
    original = deepcopy(state)
    projection = project_narrative_state(SimpleNamespace(narrative_state=state, current_node_id="old"))
    assert projection["current"] == {}
    assert projection["last_known"]["item:旧信匣"]["holder"]["value"] == "player"
    assert projection["recent_events"]
    assert state == original


@pytest.mark.asyncio
@pytest.mark.parametrize("focus", ["出山前检查旧信匣", "放学前检查木匣0001号"])
async def test_next_actor_turn_generates_after_held_items_overflow(monkeypatch, focus):
    from dataclasses import replace
    from services.theater.numeric_v2_actor import NumericV2Actor
    from services.theater.numeric_v2_runtime import TurnRequestV2
    from tests.unit.test_theater_numeric_v2_natural_ending import _engine
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening

    state = _session(all_player=True).narrative_state
    engine = _engine(ordinary=True)
    session = engine.create_session(session_id="overflow", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    session = replace(session, revision=state["revision"], narrative_state=state,
                      story_state={**session.story_state, "revision": state["revision"]})
    outcome = engine.resolve_turn(session, TurnRequestV2("next", session.revision, focus), ())
    sent = []

    async def invoke(self, messages, **kwargs):
        assert sum(count_tokens(part.content) for part in messages) <= kwargs["max_input_tokens"]
        sent.append(messages)
        return {"performance": "我陪你检查信匣的外壳。", "suggested_inputs": []}

    monkeypatch.setattr(NumericV2Actor, "_invoke", invoke)
    result = await NumericV2Actor(object()).generate_turn(engine=engine, session=session,
        outcome=outcome, player_input=focus, character_profile="温和。", allow_suggestion_fill=False)
    assert result["performance"] == "我陪你检查信匣的外壳。"
    assert len(sent) == 1
    assert "omitted_essential_entities" in sent[0][1].content
    assert session.narrative_state == state


def test_overflow_packing_uses_logarithmic_whole_projection_tokenizations(monkeypatch):
    from services.theater import numeric_v2_narrative as narrative

    session = _session(all_player=True)
    calls, fragments = [], []

    def counted(text):
        (calls if "projection" in json.loads(text) else fragments).append(len(text))
        return count_tokens(text)

    monkeypatch.setattr(narrative, "count_tokens", counted)
    projection = narrative.project_narrative_state(session, focus="旧信匣")
    assert len(calls) <= 20
    assert len(fragments) <= 301
    assert count_tokens(json.dumps(projection, ensure_ascii=False, separators=(",", ":"))) <= 1600
    assert projection["current"]["item:旧信匣"]["holder"]["value"] == "player"
    assert projection["projection"]["omitted_essential_entities"] > 0


@pytest.mark.parametrize("blocking_entity,focus", [
    ("player", "出山前检查旧信匣"), ("item:巨匣", "放学前检查巨匣"),
])
def test_oversized_entity_does_not_hide_later_character_or_held_items(blocking_entity, focus):
    from services.theater.numeric_v2_narrative import ATTRIBUTES, validate_narrative_state

    state = _append(None, "旧信匣", "player")
    prototype = state["entities"]["item:旧信匣"]["holder"]
    state["entities"][blocking_entity] = {
        attribute: {**deepcopy(prototype), "value": "龘" * 120}
        for attribute in ATTRIBUTES - {"holder"}
    }
    state["entities"]["character"] = {
        "position": {**deepcopy(prototype), "value": "门口"},
    }
    validate_narrative_state(state)
    original = deepcopy(state)
    session = SimpleNamespace(narrative_state=state, current_node_id="old")
    projection = project_narrative_state(session, focus=focus)

    assert blocking_entity not in _entities(projection)
    assert projection["current"]["character"]["position"]["value"] == "门口"
    assert projection["current"]["item:旧信匣"]["holder"]["value"] == "player"
    assert projection["projection"]["omitted_essential_entities"] == 1
    assert projection["projection"]["omitted_entities"] == 1
    assert count_tokens(json.dumps(projection, ensure_ascii=False, separators=(",", ":"))) <= 1600
    assert state == original


def test_large_entity_that_only_exceeds_remaining_budget_is_skipped():
    state = _append(None, "旧信匣", "player")
    prototype = state["entities"]["item:旧信匣"]["holder"]
    state["entities"]["player"] = {
        attribute: {**deepcopy(prototype), "value": "龘" * 120}
        for attribute in ("position", "condition", "location", "choice", "commitment")
    }
    state["entities"]["item:巨匣"] = {
        attribute: {**deepcopy(prototype), "value": "龘" * 120}
        for attribute in ("condition", "location", "choice", "commitment", "arrangement")
    }
    session = SimpleNamespace(narrative_state=state, current_node_id="old")
    projection = project_narrative_state(session, focus="检查巨匣")
    assert "player" in _entities(projection)
    assert "item:巨匣" not in _entities(projection)
    assert projection["current"]["item:旧信匣"]["holder"]["value"] == "player"
    assert projection["projection"]["omitted_essential_entities"] == 1
    assert count_tokens(json.dumps(projection, ensure_ascii=False, separators=(",", ":"))) <= 1600
