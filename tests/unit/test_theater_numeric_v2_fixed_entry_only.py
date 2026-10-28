"""Author-marked program-delivered target entry (transition_contract.fixed_entry_only).

No model call: the Actor provider boundary is stubbed; everything else (message packing,
schema, parser, finalize, fixed-text insertion, suggestion fill, commit) is production code.
"""
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError, _parse_output
from services.theater.numeric_v2_runtime import NumericV2Runtime, NumericV2Engine, TurnRequestV2
from services.theater.numeric_v2_fixed_narration import validate_delivery
from services.theater.numeric_v2_structured_output import actor_output_schema

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/theater_stories/story_2fa2c3823a41.json"
_ENTRY_TEXT = "程序原文：潮声里她抬手示意你别出声，只把灯往你这边偏了偏。"
_TARGET_OPENING_MARKER = "尾巴自然地垂在腿边"


def _story(*, flag: bool | None, entry: bool = True, target_policy: str = "optional"):
    story = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    target = next(node for node in story["nodes"] if node["id"] == "mainline_03")
    target["story_beat"]["acting_contract"]["dialogue_policy"] = target_policy
    if entry:
        target["story_beat"]["fixed_narrations"] = [{
            "id": "program_entry",
            "text": _ENTRY_TEXT,
            "trigger": {"type": "entry"},
            "after": [],
            "required_before_exit": True,
        }]
    route = next(
        gate for gate in next(node for node in story["nodes"] if node["id"] == "mainline_02")["route_gates"]
        if gate["target_node_id"] == "mainline_03"
    )
    if flag is not None:
        route["transition_contract"]["fixed_entry_only"] = flag
    return story


def _first_route_story():
    """Same mode on the opening route, so a fresh persisted Session can commit it."""
    story = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    target = next(node for node in story["nodes"] if node["id"] == "mainline_02")
    target["story_beat"]["acting_contract"]["dialogue_policy"] = "optional"
    target["story_beat"]["fixed_narrations"] = [{
        "id": "program_entry",
        "text": _ENTRY_TEXT,
        "trigger": {"type": "entry"},
        "after": [],
        "required_before_exit": True,
    }]
    route = next(
        gate for gate in next(node for node in story["nodes"] if node["id"] == "mainline_01")["route_gates"]
        if gate["target_node_id"] == "mainline_02"
    )
    route["transition_contract"]["fixed_entry_only"] = True
    return story


def _session(engine):
    return engine.create_session(
        session_id="fixed_entry_only",
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘", "player_address": "哥哥"},
        opening_performance={"performance": "（抬眼）开场。", "suggested_inputs": []},
    )


def _advance(engine, session, target_node: str):
    """Walk the fixture's unconditional routes until the source node is `mainline_02`."""
    source = engine.nodes["mainline_01"]
    accept = next(g for g in source["route_gates"] if g["target_node_id"] == "mainline_02")["transition_contract"]["accept_input"]
    session = replace(session, transition_offered=True)
    outcome = engine.resolve_turn(session, TurnRequestV2("walk-1", session.revision, accept, "suggestion"), (),
                                  transition_intent="accept")
    assert outcome.session.current_node_id == "mainline_02"
    return outcome.session


def _transition_outcome(engine, session):
    accept = next(
        gate for gate in engine.nodes["mainline_02"]["route_gates"] if gate["target_node_id"] == "mainline_03"
    )["transition_contract"]["accept_input"]
    session = replace(session, transition_offered=True)
    outcome = engine.resolve_turn(session, TurnRequestV2("turn-2", session.revision, accept, "suggestion"), (),
                                 transition_intent="accept")
    assert outcome.session.current_node_id == "mainline_03"
    return session, outcome


def _walk_to(engine, session, node_id: str):
    """Follow the fixture's first routes (all unconditional) until `node_id`."""
    guard = 0
    while session.current_node_id != node_id:
        guard += 1
        assert guard < 8
        gates = engine.nodes[session.current_node_id].get("route_gates") or []
        assert gates, session.current_node_id
        gate = gates[0]
        accept = gate["transition_contract"].get("accept_input") or "（点头）好。"
        session = replace(session, transition_offered=True)
        outcome = engine.resolve_turn(
            session, TurnRequestV2(f"walk-{guard}", session.revision, accept, "suggestion"), (),
            transition_intent="accept",
        )
        assert outcome.route is not None
        session = outcome.session
    return session


def _ending_story():
    """Real ending route: mainline_06 -> ending_normal with a program-delivered entry."""
    story = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    ending = next(node for node in story["nodes"] if node["id"] == "ending_normal")
    ending["story_beat"]["acting_contract"]["dialogue_policy"] = "optional"
    ending["story_beat"]["fixed_narrations"] = [{
        "id": "program_final",
        "text": _ENTRY_TEXT,
        "trigger": {"type": "entry"},
        "after": [],
        "required_before_exit": True,
    }]
    route = next(
        gate for gate in next(node for node in story["nodes"] if node["id"] == "mainline_06")["route_gates"]
        if gate["target_node_id"] == "ending_normal"
    )
    route["transition_contract"]["fixed_entry_only"] = True
    return story


def _messages_text(messages) -> str:
    return "\n".join(getattr(message, "content", "") for message in messages)


async def _generate(monkeypatch, engine, session, outcome, *, reply=None, suggestions=("先应下。", "问他还在不在。")):
    calls = []

    # Mimic the parser's shape: an empty optional narration is omitted, not sent as "".
    parsed = reply if reply is not None else {
        "source_performance": "（低声）跟紧我，别出声。",
        "bridge_scene_narration": "两人沿河岸的碎石路往前走了一段。",
        "target_scene_narration": "",
        "target_performance": "",
        "suggested_inputs": [],
    }

    async def fake_invoke(self, messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        if kwargs.get("suggestions_only") or kwargs.get("transition_suggestions_only"):
            return {"suggested_inputs": list(suggestions)}
        return deepcopy(parsed)

    monkeypatch.setattr(actor.NumericV2Actor, "_invoke", fake_invoke)
    performance = await actor.NumericV2Actor(config_manager=object()).generate_turn(
        engine=engine,
        session=session,
        outcome=outcome,
        player_input="（压低声音）码头那边还有人吗？",
        character_profile="安静克制，习惯用短句回应。",
    )
    return performance, calls


@pytest.mark.asyncio
async def test_flag_absent_keeps_old_transition_request_and_single_call(monkeypatch):
    story = _story(flag=None)
    engine = NumericV2Engine.from_mapping(story)
    session = _advance(engine, _session(engine), "mainline_02")
    source, outcome = _transition_outcome(engine, session)
    performance, calls = await _generate(monkeypatch, engine, source, outcome)
    request = _messages_text(calls[0]["messages"])
    assert _ENTRY_TEXT in request
    assert _TARGET_OPENING_MARKER in request
    assert calls[0]["kwargs"].get("fixed_entry_only") is False
    assert len(calls) == 1
    assert performance["suggested_inputs"] == []


@pytest.mark.asyncio
async def test_flag_true_hides_target_material_but_keeps_player_question(monkeypatch):
    story = _story(flag=True)
    source_node = next(node for node in story["nodes"] if node["id"] == "mainline_02")
    source_node["route_gates"][0]["transition_contract"]["must_deliver"] = ["桥段只沿已公开的河岸小路移动。"]
    engine = NumericV2Engine.from_mapping(story)
    session = _advance(engine, _session(engine), "mainline_02")
    source, outcome = _transition_outcome(engine, session)
    performance, calls = await _generate(monkeypatch, engine, source, outcome)
    request = _messages_text(calls[0]["messages"])
    assert "（压低声音）码头那边还有人吗？" in request
    assert _ENTRY_TEXT not in request
    assert _TARGET_OPENING_MARKER not in request
    assert "mainline_03" not in request
    assert "桥段只沿已公开的河岸小路移动。" in request
    # Target-scene staging rules and the old suggestion clause are replaced by source-only rules.
    assert "target_scene.opening_situation" not in request
    assert "suggested_inputs 必须为空数组" in request
    assert calls[0]["kwargs"].get("fixed_entry_only") is True


def test_schema_constrains_target_fields_and_parser_rejects_nonempty():
    schema = actor_output_schema(transition_required=True, fixed_entry_only=True)
    assert schema["properties"]["target_scene_narration"]["enum"] == [""]
    assert schema["properties"]["target_performance"]["enum"] == [""]
    assert schema["properties"]["suggested_inputs"]["maxItems"] == 0
    legacy = actor_output_schema(transition_required=True)
    assert "enum" not in legacy["properties"]["target_performance"]

    ok = {
        "source_scene_narration": "",
        "source_performance": "（低声）跟紧我。",
        "bridge_scene_narration": "两人沿河岸往前。",
        "target_scene_narration": "",
        "target_performance": "",
        "suggested_inputs": [],
    }
    parsed = _parse_output(json.dumps(ok, ensure_ascii=False), transition_required=True,
                           entry_narration=True, target_dialogue_policy="optional",
                           fixed_entry_only=True)
    assert parsed["target_performance"] == ""

    for field, value in (("target_performance", "（回头）怎么了？"),
                         ("target_scene_narration", "河湾的雾散了。"),
                         ("suggested_inputs", ["先应下。"])):
        bad = dict(ok)
        bad[field] = value
        with pytest.raises(NumericV2ActorOutputError) as excinfo:
            _parse_output(json.dumps(bad, ensure_ascii=False), transition_required=True,
                          entry_narration=True, target_dialogue_policy="optional",
                          fixed_entry_only=True)
        assert excinfo.value.args[0] == "numeric_v2_actor_fixed_entry_only_fields_nonempty"


@pytest.mark.parametrize("suggestions", [[""], ["她抬起头。"], None, "", 0])
def test_fixed_entry_rejects_raw_suggestions_before_normalization(suggestions):
    payload = dict(source_performance="知道了。", bridge_scene_narration="雨停了。",
                   target_scene_narration="", target_performance="", suggested_inputs=suggestions)
    with pytest.raises(NumericV2ActorOutputError, match="fixed_entry_only_fields_nonempty"):
        _parse_output(json.dumps(payload, ensure_ascii=False), transition_required=True,
                      entry_narration=True, target_dialogue_policy="optional", fixed_entry_only=True)


@pytest.mark.asyncio
async def test_program_text_delivered_once_and_suggestions_filled(monkeypatch):
    story = _story(flag=True)
    engine = NumericV2Engine.from_mapping(story)
    session = _advance(engine, _session(engine), "mainline_02")
    source, outcome = _transition_outcome(engine, session)
    performance, calls = await _generate(monkeypatch, engine, source, outcome)
    segments = performance["segments"]
    assert [segment["phase"] for segment in segments] == ["source_response", "transition_bridge", "target_opening"]
    visible = json.dumps(segments, ensure_ascii=False)
    assert visible.count(_ENTRY_TEXT) == 1
    assert performance["suggested_inputs"] == ["先应下。", "问他还在不在。"]
    assert len(calls) == 2
    assert calls[1]["kwargs"].get("suggestions_only") or calls[1]["kwargs"].get("transition_suggestions_only")
    # The filler sees the program text it must respond to (it only writes buttons, never source prose).
    assert _ENTRY_TEXT in _messages_text(calls[1]["messages"])


def test_flag_false_matches_absent_transition_messages():
    """false and absent must produce the same request text (old path, no mode)."""
    rendered = []
    for flag in (None, False):
        engine = NumericV2Engine.from_mapping(_story(flag=flag))
        session = _advance(engine, _session(engine), "mainline_02")
        source, outcome = _transition_outcome(engine, session)
        messages = actor._turn_messages(
            engine, session, outcome, "（压低声音）码头那边还有人吗？", "安静克制。", "测试猫娘", "哥哥",
        )
        rendered.append([(m.type, m.content) for m in messages])
    assert rendered[0] == rendered[1]


@pytest.mark.asyncio
async def test_natural_ending_uses_standard_transition_and_adds_no_suggestion_call(monkeypatch):
    """A real natural ending keeps the author mark: program entry, source reaction, no target text, no buttons."""
    story = _ending_story()
    engine = NumericV2Engine.from_mapping(story)
    session = _walk_to(engine, _session(engine), "mainline_06")
    session = replace(session, transition_offered=True)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("ending-1", session.revision, "（点头）好。", "suggestion"), (),
        scene_complete=True, natural_ending_ready=True,
    )
    assert outcome.session.current_node_id == "ending_normal"
    assert outcome.session.status == "ended"
    assert outcome.ledger_event.get("natural_ending_ready") is True
    performance, calls = await _generate(monkeypatch, engine, session, outcome)
    assert calls[0]["kwargs"].get("fixed_entry_only") is True
    assert calls[0]["kwargs"].get("natural_ending") is False
    assert performance["suggested_inputs"] == []
    assert len(calls) == 1
    assert [segment["phase"] for segment in performance["segments"]] == [
        "source_response", "transition_bridge", "target_opening"]
    assert json.dumps(performance["segments"], ensure_ascii=False).count(_ENTRY_TEXT) == 1


@pytest.mark.asyncio
async def test_displayed_entry_revisit_keeps_old_path(monkeypatch):
    story = _story(flag=True)
    engine = NumericV2Engine.from_mapping(story)
    session = _advance(engine, _session(engine), "mainline_02")
    shown = {
        "revision": 2,
        "from_node_id": "mainline_02",
        "to_node_id": "mainline_02",
        "segments": [{"phase": "ordinary", "performance": "（点头）",
                      "fixed_narrations": [{"node_id": "mainline_03", "id": "program_entry",
                                            "text": _ENTRY_TEXT, "position": "before"}]}],
    }
    session = replace(session, performance_history=(*session.performance_history, shown))
    source, outcome = _transition_outcome(engine, session)
    performance, calls = await _generate(monkeypatch, engine, source, outcome, reply={
        "source_performance": "（点头）好。",
        "bridge_scene_narration": "两人沿河岸的碎石路往前走了一段。",
        "target_scene_narration": "河湾的风很轻。",
        "target_performance": "（回头）慢一点。",
        "suggested_inputs": [],
    })
    request = _messages_text(calls[0]["messages"])
    assert _ENTRY_TEXT in request
    assert calls[0]["kwargs"].get("fixed_entry_only") is False


@pytest.mark.asyncio
async def test_fixed_entry_only_commits_through_store_and_cold_restores(tmp_path, monkeypatch):
    """Real Store commit path: persisted Session, atomic commit, then a fresh runtime cold restore."""
    story = _first_route_story()
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path / "theater")
    # Keep the persisted Session consistent with an empty ledger, then route via an
    # explicit departure so no latched invitation has to be faked into storage.
    session = _session(engine)
    await runtime.store.create(session)
    accept = next(
        gate for gate in engine.nodes["mainline_01"]["route_gates"] if gate["target_node_id"] == "mainline_02"
    )["transition_contract"]["accept_input"]
    outcome = engine.resolve_turn(session, TurnRequestV2("commit-1", 0, accept, "suggestion"), (),
                                  transition_intent="initiate")
    performance, _calls = await _generate(monkeypatch, engine, session, outcome)
    validate_delivery(engine.story, performance, session=outcome.session)
    stored = await runtime.commit_turn(outcome, performance)
    reloaded = await NumericV2Runtime(engine, tmp_path / "theater").restore_session(stored.session.session_id)
    assert reloaded == stored
    assert stored.session.revision == 1
    assert stored.session.current_node_id == "mainline_02"
    assert json.dumps(stored.session.performance_history[-1], ensure_ascii=False).count(_ENTRY_TEXT) == 1
