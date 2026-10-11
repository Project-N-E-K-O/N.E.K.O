"""Persist narrative records with turns and replay confirmed sources after restore and fork."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_store import NumericV2StoreError
from tests.unit.test_theater_numeric_v2_runtime import _binding, _branch_story


def _record(events=(), coverage=None):
    return {
        "schema": "numeric_v2_narrative_record_v1",
        "coverage": coverage or ("confirmed_changes" if events else "confirmed_no_change"),
        "events": deepcopy(list(events)),
        "proposal_count": len(events),
    }


def _event(*, opening=False):
    return {
        "phase": "opening" if opening else "player_input",
        "subject": "character" if opening else "player",
        "kind": "action",
        "changes": [{"entity": "item:木匣", "attribute": "holder",
                     "value": "character" if opening else "player"}],
        "source": "scene_narration" if opening else "player_input",
        "quote": "抱着木匣" if opening else "接过木匣",
        "known_to": ["player", "character"],
        **({"authorization_quote": "接过木匣"} if not opening else {}),
    }


def _opening(*, with_record=False):
    performance = {
        "scene_narration": "Lan抱着木匣站在柜台旁。",
        "performance": "（抬眼）你来了。", "suggested_inputs": [],
    }
    if with_record:
        performance["narrative_record"] = _record([_event(opening=True)])
    return performance


def _runtime(tmp_path):
    return NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)


def _turn(runtime, current, record=None, message="（接过木匣）我先拿着。"):
    outcome = runtime.prepare_turn(
        current, TurnRequestV2(f"turn_{current.session.revision + 1}", current.session.revision, message), (),
    )
    if record is not None:
        outcome = replace(outcome, ledger_event={**outcome.ledger_event, "narrative_record": record})
    return outcome, {"performance": "（松开手）好，你先拿着。", "suggested_inputs": []}


@pytest.mark.asyncio
async def test_legacy_session_and_direct_turn_do_not_infer_narrative_state(tmp_path):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="legacy", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    outcome, performance = _turn(runtime, current)
    stored = await runtime.commit_turn(outcome, performance)

    assert current.session.narrative_state is None
    assert stored.session.narrative_state is None
    assert "narrative_state" not in stored.session.to_dict()
    assert "narrative_record" not in stored.ledger_events[0]
    assert "narrative_record" not in stored.session.performance_history[0]
    assert await _runtime(tmp_path).restore_session("legacy") == stored


@pytest.mark.asyncio
async def test_opening_record_survives_cold_restore_and_revision_zero_fork(tmp_path):
    runtime = _runtime(tmp_path)
    opening = _opening(with_record=True)
    original = deepcopy(opening)
    current = await runtime.start_session(session_id="opening", catgirl_binding=_binding(),
                                          opening_performance=opening)

    assert opening == original
    assert current.ledger_events == ()
    assert current.session.narrative_state["revision"] == 0
    assert current.session.narrative_state["entities"]["item:木匣"]["holder"]["value"] == "character"
    assert await _runtime(tmp_path).restore_session("opening") == current
    fork = await runtime.fork_session_for_test("opening", session_id="opening_fork", through_revision=0)
    assert fork.session.narrative_state == current.session.narrative_state
    assert fork.session.story_state == current.session.story_state
    assert fork.session.opening_performance == current.session.opening_performance


@pytest.mark.asyncio
@pytest.mark.parametrize("opening_record", [False, True])
async def test_narrative_turns_replay_at_each_fork_without_changing_completion_facts(tmp_path, opening_record):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="holder", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=opening_record))
    first_record = _record([_event()])
    outcome, performance = _turn(runtime, current, first_record)
    prepared_state = deepcopy(outcome.session.narrative_state)
    first = await runtime.commit_turn(outcome, performance)

    assert outcome.session.narrative_state == prepared_state == current.session.narrative_state
    assert first.session.narrative_state["revision"] == 1
    assert first.session.narrative_state["coverage_from"] == (0 if opening_record else 1)
    assert first.session.narrative_state["entities"]["item:木匣"]["holder"]["value"] == "player"
    assert first.ledger_events[0]["narrative_record"] == first_record
    assert first.session.performance_history[0]["narrative_record"] == first_record
    assert first.session.story_state == outcome.session.story_state
    outcome, performance = _turn(runtime, first, _record(), "我继续拿着木匣。")
    second = await runtime.commit_turn(outcome, performance)

    assert await _runtime(tmp_path).restore_session("holder") == second
    for revision, expected in [(1, first), (2, second)]:
        fork = await runtime.fork_session_for_test("holder", session_id=f"fork_{revision}",
                                                   through_revision=revision)
        assert fork.session.narrative_state == expected.session.narrative_state
        assert fork.session.performance_history == expected.session.performance_history
        assert fork.session.story_state == expected.session.story_state


@pytest.mark.asyncio
async def test_direct_turn_after_recording_persists_unrecorded_barrier(tmp_path):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="gap", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=True))
    outcome, performance = _turn(runtime, current)
    stored = await runtime.commit_turn(outcome, performance)

    assert stored.ledger_events[0]["narrative_record"] == _record(coverage="unrecorded")
    assert stored.session.narrative_state["revision"] == 1
    assert stored.session.narrative_state["entities"]["item:木匣"]["holder"]["current"] is False
    assert await _runtime(tmp_path).restore_session("gap") == stored
    fork = await runtime.fork_session_for_test("gap", session_id="gap_fork", through_revision=1)
    assert fork.session.narrative_state == stored.session.narrative_state
    outcome, performance = _turn(runtime, stored, _record(), "你接着说吧。")
    unchanged = await runtime.commit_turn(outcome, performance)
    assert unchanged.session.narrative_state["entities"]["item:木匣"]["holder"]["current"] is False
    assert await _runtime(tmp_path).restore_session("gap") == unchanged


@pytest.mark.asyncio
async def test_unverifiable_record_does_not_commit_or_mutate_previous_state(tmp_path):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="bad_quote", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=True))
    event = _event()
    event["quote"] = "这是原文中不存在的交接。"
    outcome, performance = _turn(runtime, current, _record([event]))

    with pytest.raises(ValueError):
        await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session("bad_quote") == current


@pytest.mark.asyncio
@pytest.mark.parametrize("schema", ["numeric_v2_narrative_record_v1", "numeric_v2_narrative_record_v2"])
async def test_partial_versions_restore_exactly_and_new_no_change_cannot_revive_a_gap(tmp_path, schema):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="partial", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=True))
    record = {**_record([_event()], coverage="partial"), "schema": schema, "proposal_count": 2}
    outcome, performance = _turn(runtime, current, record)
    partial = await runtime.commit_turn(outcome, performance)
    holder = partial.session.narrative_state["entities"]["item:木匣"]["holder"]
    assert holder["value"] == "player"
    assert holder["current"] is (schema == "numeric_v2_narrative_record_v1")
    assert await _runtime(tmp_path).restore_session("partial") == partial
    fork = await runtime.fork_session_for_test("partial", session_id="partial_fork", through_revision=1)
    assert fork.session.narrative_state == partial.session.narrative_state
    no_change = {**_record(), "schema": "numeric_v2_narrative_record_v2"}
    outcome, performance = _turn(runtime, partial, no_change, "继续说吧。")
    unchanged = await runtime.commit_turn(outcome, performance)
    assert unchanged.session.narrative_state["entities"]["item:木匣"]["holder"]["current"] is False
    assert await _runtime(tmp_path).restore_session("partial") == unchanged
    confirmed = {**_record([_event()]), "schema": "numeric_v2_narrative_record_v2"}
    outcome, performance = _turn(runtime, unchanged, confirmed)
    restored = await runtime.commit_turn(outcome, performance)
    assert restored.session.narrative_state["entities"]["item:木匣"]["holder"]["current"] is True
    assert await _runtime(tmp_path).restore_session("partial") == restored


@pytest.mark.asyncio
async def test_partial_keeps_approved_phase_history_and_before_chain_without_claiming_current(tmp_path):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="phase_gap", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=True))
    returned = {**_event(opening=True), "phase": "turn", "source": "performance", "quote": "木匣交回她手里"}
    record = {**_record([_event(), returned], coverage="partial"),
              "schema": "numeric_v2_narrative_record_v2", "proposal_count": 3}
    outcome, _ = _turn(runtime, current, record)
    performance = {"performance": "木匣交回她手里。", "suggested_inputs": []}
    stored = await runtime.commit_turn(outcome, performance)
    state = stored.session.narrative_state
    assert state["recent_events"][-1]["changes"][0]["before"] == "player"
    assert state["entities"]["item:木匣"]["holder"]["value"] == "character"
    assert state["entities"]["item:木匣"]["holder"]["current"] is False
    assert stored.ledger_events[-1]["narrative_record"]["events"] == [_event(), returned]
    assert await _runtime(tmp_path).restore_session("phase_gap") == stored
    fork = await runtime.fork_session_for_test("phase_gap", session_id="phase_gap_fork", through_revision=1)
    assert fork.session.narrative_state == state


@pytest.mark.asyncio
async def test_write_failure_preserves_previous_narrative_and_ledger(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="write_failure", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=True))
    previous_bytes = runtime.store._path("write_failure").read_bytes()
    outcome, performance = _turn(runtime, current, _record([_event()]))

    def reject_write(*_args, **_kwargs):
        raise OSError("test write failure")

    monkeypatch.setattr(runtime.store, "_write", reject_write)
    with pytest.raises(OSError, match="test write failure"):
        await runtime.commit_turn(outcome, performance)
    assert runtime.store._path("write_failure").read_bytes() == previous_bytes
    assert await runtime.restore_session("write_failure") == current


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["different_record", "missing_record", "unverifiable_quote",
                                         "state_revision", "state_value", "opening_quote"])
async def test_cold_restore_rejects_narrative_record_or_state_corruption(tmp_path, corruption):
    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="damaged", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=True))
    outcome, performance = _turn(runtime, current, _record([_event()]))
    await runtime.commit_turn(outcome, performance)
    path = runtime.store._path("damaged")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if corruption == "different_record":
        payload["ledger_events"][0]["narrative_record"] = _record()
    elif corruption == "missing_record":
        payload["session"]["performance_history"][0].pop("narrative_record")
    elif corruption == "unverifiable_quote":
        for record in (payload["ledger_events"][0], payload["session"]["performance_history"][0]):
            record["narrative_record"]["events"][0]["quote"] = "不存在的原文。"
    elif corruption == "state_revision":
        payload["session"]["narrative_state"]["revision"] = 0
    elif corruption == "state_value":
        payload["session"]["narrative_state"]["entities"]["item:木匣"]["holder"]["value"] = "character"
    else:
        payload["session"]["opening_performance"]["narrative_record"]["events"][0]["quote"] = "不存在的开场。"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises((ValueError, NumericV2StoreError)):
        await _runtime(tmp_path).restore_session("damaged")


@pytest.mark.asyncio
async def test_public_session_keeps_narrative_state_and_records_on_server(tmp_path):
    from main_routers.numeric_theater_router import _public_session

    runtime = _runtime(tmp_path)
    current = await runtime.start_session(session_id="public", catgirl_binding=_binding(),
                                          opening_performance=_opening(with_record=True))
    outcome, performance = _turn(runtime, current, _record([_event()]))
    stored = await runtime.commit_turn(outcome, performance)
    public = _public_session(stored.session)

    assert "narrative_state" not in public
    for performance in (public["opening_performance"], *public["performance_history"]):
        assert "narrative_record" not in performance
        assert "narrative_events" not in performance
    assert public["performance_history"][0]["performance"] == "（松开手）好，你先拿着。"
    assert stored.session.narrative_state["entities"]["item:木匣"]["holder"]["value"] == "player"


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [False, True])
async def test_reinvitation_preserves_narrative_certainty_and_replay(tmp_path, monkeypatch, gap):
    from tests.unit.test_theater_review_round8_invitation import setup_case
    from tests.unit.test_theater_reinvitation import reinvite

    runtime, _ = await setup_case(tmp_path, monkeypatch, review=True, mode="off")
    opening = _opening(with_record=True)
    event = opening["narrative_record"]["events"][0]
    event["quote"] = "抱着木匣站在柜台旁"
    event["changes"].append({"entity": "character", "attribute": "position", "value": "柜台旁"})
    current = await runtime.start_session(session_id="narrative-reinvite", catgirl_binding=_binding(),
                                          opening_performance=opening)
    ready = runtime.prepare_turn(current, TurnRequestV2("done", 0, "办好了。"), (),
        fact_operations=({"op": "set", "key": "scene:start:done", "value": True,
                          "visibility": "public"},))
    ready = replace(ready, ledger_event={**ready.ledger_event, "narrative_record": {
        "schema": "numeric_v2_narrative_record_v2",
        "coverage": "unrecorded" if gap else "confirmed_no_change",
        "events": [], "proposal_count": 0,
    }})
    current = await runtime.commit_turn(ready, {"performance": "办好了。", "suggested_inputs": []})
    attributes = deepcopy(current.session.narrative_state["entities"])
    result = await reinvite(runtime, current)
    stored = result.stored

    expected = {"schema": "numeric_v2_narrative_record_v2", "coverage": "confirmed_no_change",
                "events": [], "proposal_count": 0}
    assert stored.ledger_events[-1]["narrative_record"] == expected
    assert stored.session.performance_history[-1]["narrative_record"] == expected
    assert stored.session.narrative_state["entities"] == attributes
    assert attributes["item:木匣"]["holder"]["current"] is not gap
    assert attributes["character"]["position"]["current"] is not gap
    cold = NumericV2Runtime(runtime.engine, tmp_path)
    assert await cold.restore_session(stored.session.session_id) == stored
    fork = await cold.fork_session_for_test(stored.session.session_id, session_id="reinvite-fork",
                                          through_revision=stored.session.revision)
    assert fork.session.narrative_state == stored.session.narrative_state
    assert fork.ledger_events[-1]["narrative_record"] == expected
