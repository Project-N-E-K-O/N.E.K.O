"""Only speculative recovery Actor failures reuse a reviewed ordinary transaction."""
from copy import deepcopy
import socket

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor import NumericV2ActorError, NumericV2ActorOutputError
from services.theater.numeric_v2_narrative import narrative_body_fingerprint, narrative_event_table
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Runtime, NumericV2RuntimeError, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    connect = socket.socket.connect

    def forbidden(sock, address):
        # Windows Proactor creates a loopback socket pair for its own wakeups.
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}:
            return connect(sock, address)
        raise AssertionError("These regression drafts must never use network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("utils.tokenize._get_encoder", lambda encoding: None)


def test_network_guard_allows_local_wakeups_but_blocks_external_connections():
    with socket.socket() as listener, socket.socket() as client:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client.connect(listener.getsockname())
        connection, _ = listener.accept()
        connection.close()
    with socket.socket() as client:
        with pytest.raises(AssertionError, match="must never use network"):
            client.connect(("192.0.2.1", 443))


CASES = {
    "fantasy": {
        "place": "倒悬湖湖面", "target": "巨鹿栖息地",
        "player_action": "握紧刻刀继续陪她往湖心走", "fact_entity": "environment:水下石阶",
        "input": "（握紧刻刀继续陪她往湖心走）顺着光走到湖心去。",
        "opening": "第四步落下，湖面裂纹亮起翠绿光芒，幻影仍在水底。",
        "body": "（抱稳魔晶迈出第五步）湖面裂开了，水下石阶已经露出来。我们要不要顺着台阶走下去？",
        "fact_quote": "水下石阶已经露出来", "accept": "（握紧刻刀踏上第一级石阶）我们下去吧。",
        "target_opening": "巨大的白鹿伏在空地上沉睡，周围蕨类随呼吸缓缓起伏。",
    },
    "science_fiction": {
        "place": "排气舱隔离闸旁", "target": "中继控制舱",
        "player_action": "按住隔离闸手柄", "fact_entity": "environment:隔离闸",
        "input": "（按住隔离闸手柄）闸门稳住了，你检查一下指示灯。",
        "opening": "两人站在排气舱隔离闸旁，玩家按住手柄等待检查。",
        "body": "（查看指示灯）隔离闸已经锁稳。要不要一起前往中继控制舱查看信号？",
        "fact_quote": "隔离闸已经锁稳", "accept": "（收回手准备出发）一起去中继控制舱吧。",
        "target_opening": "中继控制舱中央的全息屏幕旋转着，一列蓝色信号图在主屏展开。",
    },
}


def event(phase, subject, quote, entity, attribute, value):
    return {"phase": phase, "subject": subject, "kind": "action" if phase == "player_input" else "speech",
            "changes": [{"entity": entity, "attribute": attribute, "value": value}],
            "source": "player_input" if phase == "player_input" else "performance",
            "quote": quote, "known_to": ["player", "character"]}


async def setup_case(tmp_path, monkeypatch, *, genre="fantasy", failure="actor", gate="safe", primary_formal=False):
    c = CASES[genre]
    base = initiation_case(c["place"], c["target"], public=False, message=c["input"])
    engine = base["engine"]
    target = engine.nodes["ending_leave"]
    target["story_beat"]["opening_scene"] = c["target_opening"]
    engine.nodes["start"]["route_gates"][1]["transition_contract"]["accept_input"] = c["accept"]
    fact_key = "scene:start:result_delivered"
    engine.fact_contract[fact_key] = {"value_type": "bool", "visibility": "public", "description": c["fact_quote"]}
    engine.nodes["start"]["completion_contract"] = {"all": [{"key": fact_key, "equals": True}]}
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id=f"{genre}_{failure}_{gate}_{primary_formal}", catgirl_binding=_binding(),
                                          opening_performance={"performance": c["opening"], "suggested_inputs": []})
    original_player = event("player_input", "player", c["input"], "player", "position", c["player_action"])
    original_body = event("turn", "character", c["fact_quote"], c["fact_entity"], "condition", c["fact_quote"])
    failed_player = event("player_input", "player", c["input"], "player", "position", c["target"])
    calls = {"evaluator": 0, "actors": [], "reviews": [], "proposer": [], "commit": 0}
    ordinary = {"performance": c["body"], "suggested_inputs": ["（检查四周环境）先观察一下。", "（静候回应）"],
                "transition_offered": True,
                "fact_candidates": [{"key": fact_key, "value": True, "evidence_quote": c["fact_quote"]}]}

    async def options():
        return {"evaluator": True, "review": True, "actor_retry": False}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return ev.NumericV2EvaluationResult((MetricChangeV2("trust", 2, "玩家兑现承诺", c["input"]),), False,
                                            transition_intent="initiate" if primary_formal else "unclear")

    async def generate(self, **kwargs):
        out = kwargs["outcome"]
        formal = out.session.current_node_id != "start"
        calls["actors"].append(out.session.current_node_id)
        if not formal:
            return deepcopy(ordinary)
        if failure == "actor":
            raise NumericV2ActorError("numeric_v2_actor_model_call_failed")
        if failure == "runtime":
            raise NumericV2RuntimeError("unexpected_runtime_integrity_error")
        candidate = {"source_performance": "（点头）按刚才说的继续。",
                     "bridge_scene_narration": "两人沿通道抵达入口。" + (c["target_opening"] if failure == "overlap" else ""),
                     "target_scene_narration": c["target_opening"],
                     "target_performance": "（观察四周）这里的信号很清楚。", "suggested_inputs": []}
        result = engine.finalize_transition_performance(out, candidate, target_opening=c["target_opening"])
        # Actor-supplied event tables are not proposer approvals and must never leak through failure reuse.
        result["narrative_events"] = [deepcopy(failed_player)]
        return result

    async def propose(self, **kwargs):
        formal = "segments" in kwargs["performance"]
        calls["proposer"].append(formal)
        return [deepcopy(failed_player)] if formal else [deepcopy(original_player), deepcopy(original_body)]

    async def review(self, **kwargs):
        formal = kwargs["route_changed"]
        calls["reviews"].append(formal)
        if (not formal and gate == "review_timeout") or (formal and failure == "review_timeout"):
            raise ev.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
        candidate = kwargs["actor_performance"]
        events = narrative_event_table(candidate, c["input"]) if "narrative_events" in candidate else ()
        return ev.NumericV2TransitionOfferReview(
            offer_present=not formal, valid=not formal and gate != "invalid_offer",
            body_violations=("author_boundary",) if not formal and gate == "body_violation" else (),
            unsafe_suggestion_indexes=(1,) if not formal and genre == "science_fiction" else (),
            offer_quote=c["body"].split("。", 1)[-1] if not formal else "",
            missed_initiation=not formal, public_destination_quote=c["opening"] if not formal else "",
            initiation_authorized=True if formal else None, delivery_matches_route=True if formal else None,
            fact_candidates=tuple(ordinary["fact_candidates"]) if not formal else (),
            fixed_narration_triggers=({"id": "pending_fragment", "evidence": c["fact_quote"]},)
            if not formal and gate == "fixed_trigger" else (),
            approved_narrative_event_indexes=tuple(range(len(events))), narrative_review_complete=True,
            reviewed_narrative_events=events, reviewed_narrative_body=narrative_body_fingerprint(candidate),
        )

    original_commit = runtime.commit_turn
    async def commit(*args, **kwargs):
        calls["commit"] += 1
        return await original_commit(*args, **kwargs)

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "谨慎而温和。")
    monkeypatch.setattr(wf.NumericV2NarrativeProposer, "propose", propose)
    monkeypatch.setattr(runtime, "commit_turn", commit)
    diagnostics = {}
    async def execute():
        return await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("one", 0, c["input"]), ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)
    return c, runtime, current, fact_key, ordinary, original_player, original_body, calls, diagnostics, execute


@pytest.mark.asyncio
@pytest.mark.parametrize("genre", list(CASES))
@pytest.mark.parametrize("failure", ["actor", "overlap"])
async def test_reviewed_new_offer_and_fact_survive_failed_recovery(tmp_path, monkeypatch, genre, failure):
    c, runtime, current, key, ordinary, player, body, calls, diagnostics, execute = await setup_case(
        tmp_path, monkeypatch, genre=genre, failure=failure)
    result = await execute()
    assert calls == {"evaluator": 1, "actors": ["start", "ending_leave"], "reviews": [False],
                     "proposer": [False], "commit": 1}
    assert result.stored.session.revision == current.session.revision + 1
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.metrics["trust"] == current.session.metrics["trust"] + 2
    assert result.performance["performance"] == ordinary["performance"]
    # Keep reviewed acceptance buttons; one unsafe recommendation retracts the complete group.
    expected_suggestions = [] if genre == "science_fiction" else [c["accept"], *ordinary["suggested_inputs"]]
    assert result.performance["suggested_inputs"] == expected_suggestions
    assert result.performance["transition_offered"] and result.stored.session.transition_offered
    assert result.stored.session.story_state["facts"][key]["value"] is True
    assert not any(k.startswith("event:scene.entered:ending_leave") for k in result.stored.session.story_state["facts"])
    record = result.stored.ledger_events[-1]["narrative_record"]
    assert record["coverage"] == "confirmed_changes" and record["events"] == [player, body]
    assert result.stored.session.narrative_state["entities"]["player"]["position"]["value"] == c["player_action"]
    assert "segments" not in result.performance
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored
    assert diagnostics["recovered_ordinary_drafts_reused"] == 1
    assert diagnostics["completion_fallback_offer_applied"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["body_violation", "invalid_offer", "fixed_trigger", "review_timeout"])
async def test_original_draft_without_safe_review_cannot_fallback(tmp_path, monkeypatch, gate):
    _c, runtime, current, _key, _ordinary, _player, _body, calls, diagnostics, execute = await setup_case(
        tmp_path, monkeypatch, gate=gate)
    with pytest.raises(NumericV2ActorError):
        await execute()
    assert calls["commit"] == 0
    assert diagnostics["recovered_ordinary_drafts_reused"] == 0
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_primary_formal_actor_error_has_no_ordinary_fallback(tmp_path, monkeypatch):
    _c, runtime, current, _key, _ordinary, _player, _body, calls, _diagnostics, execute = await setup_case(
        tmp_path, monkeypatch, primary_formal=True)
    with pytest.raises(NumericV2ActorError):
        await execute()
    assert calls["actors"] == ["ending_leave"] and calls["reviews"] == [] and calls["commit"] == 0
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_recovery_review_timeout_is_outside_this_narrow_fallback(tmp_path, monkeypatch):
    _c, runtime, current, _key, _ordinary, _player, _body, calls, _diagnostics, execute = await setup_case(
        tmp_path, monkeypatch, failure="review_timeout")
    with pytest.raises(NumericV2ActorOutputError, match="numeric_v2_transition_review_failed"):
        await execute()
    assert calls["commit"] == 0 and calls["reviews"] == [False, True]
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_unrelated_runtime_error_is_not_swallowed(tmp_path, monkeypatch):
    _c, runtime, current, _key, _ordinary, _player, _body, calls, _diagnostics, execute = await setup_case(
        tmp_path, monkeypatch, failure="runtime")
    with pytest.raises(NumericV2RuntimeError, match="unexpected_runtime_integrity_error"):
        await execute()
    assert calls["commit"] == 0
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_successful_recovery_still_commits_only_the_formal_draft(tmp_path, monkeypatch):
    c, runtime, current, key, ordinary, _player, _body, calls, diagnostics, execute = await setup_case(
        tmp_path, monkeypatch, failure="none")
    result = await execute()
    assert calls["actors"] == ["start", "ending_leave"] and calls["reviews"] == [False, True]
    assert calls["commit"] == 1 and diagnostics["recovered_ordinary_drafts_reused"] == 0
    assert result.stored.session.current_node_id == "ending_leave"
    assert key not in result.stored.session.story_state["facts"]
    assert ordinary["performance"] not in str(result.stored.session.performance_history)
    assert c["target_opening"] in str(result.performance["segments"])
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored
