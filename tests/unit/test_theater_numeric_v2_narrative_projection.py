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


def test_mandatory_player_items_survive_soft_budget_without_silent_loss():
    session = _session(all_player=True)
    projection = project_narrative_state(session)

    assert projection["projection"]["over_budget"] is True
    assert projection["projection"]["retained_entities"] == 301
    assert projection["projection"]["omitted_entities"] == 0
    for name in ["旧信匣", *(f"木匣{index:04d}号" for index in range(1, 301))]:
        holder = projection["current"][f"item:{name}"]["holder"]
        assert holder["value"] == "player"
        assert holder["source_event"]


def test_legacy_partial_current_flag_is_exposed_only_as_last_known():
    state = _append(None, "旧信匣", "player")
    state["coverage"] = "partial"
    original = deepcopy(state)
    projection = project_narrative_state(SimpleNamespace(narrative_state=state, current_node_id="old"))
    assert projection["current"] == {}
    assert projection["last_known"]["item:旧信匣"]["holder"]["value"] == "player"
    assert projection["recent_events"]
    assert state == original
