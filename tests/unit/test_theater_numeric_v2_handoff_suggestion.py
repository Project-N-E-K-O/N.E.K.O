"""Displayed player actions authorize a handoff, never its unperformed result."""

import json

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


KEY = "scene:start:received"


def _action(item):
    return f"走近柜台，将{item}递向保管员"


def _receipt(item):
    return f"保管员双手接过{item}，稳稳放在柜台上。"


async def _start(tmp_path, item, suggestions):
    story = numeric_v2_story()
    story["fact_contract"] = {"facts": {KEY: {
        "value_type": "bool", "visibility": "public", "evidence_source": "performance",
        "description": f"保管员已实际接过{item}；玩家递出时仍未完成交接。",
        "player_handoff_item": [item],
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="suggested-handoff", catgirl_binding=_binding(),
        opening_performance={"scene_narration": f"你拿着{item}，保管员站在柜台后。",
                             "performance": "请把物品交给保管员核收。", "suggested_inputs": suggestions})
    return runtime, current


def _install(monkeypatch, runtime, current, item, *, ending=False, received=True, approved=True,
             actor_candidate=True, wrong_evaluator_source=False):
    calls = []
    original = runtime.store._path(current.session.session_id).read_bytes()
    body = _receipt(item) if received else f"保管员仍站在柜台后，没有接过{item}。"

    async def evaluate(self, **kwargs):
        calls.append("evaluator")
        assert KEY not in kwargs["session"].story_state["facts"]
        if wrong_evaluator_source:
            result = ev._parse_output(json.dumps({"metric_changes": {}, "scene_complete": False,
                "fact_candidates": [{"op": "set", "key": KEY, "value": True, "visibility": "public",
                    "subject": "保管员", "action": "接过", "object": item, "result": "交接完成",
                    "evidence": [{"source": "player_input", "quote": kwargs["message"]}]}]},
                ensure_ascii=False), runtime.engine, kwargs["message"], kwargs["session"])
            assert result.fact_operations == () and result.fact_audit == ()
            return result
        return ev.NumericV2EvaluationResult((), ending, natural_ending_ready=ending)

    async def generate(self, **kwargs):
        calls.append("actor")
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        assert KEY not in kwargs["outcome"].session.story_state["facts"]
        candidate = [{"key": KEY, "value": True, "evidence_quote": body}] if actor_candidate else []
        if kwargs["outcome"].route is None:
            return {"scene_narration": body, "performance": "我在一旁等着核收。",
                    "suggested_inputs": [], "fact_candidates": candidate}
        return runtime.engine.finalize_transition_performance(kwargs["outcome"], {
            "source_scene_narration": body, "source_performance": "交接到这里告一段落。",
            "bridge_scene_narration": "", "target_scene_narration": "柜台边重新安静下来。",
            "target_performance": "辛苦了。", "suggested_inputs": [], "fact_candidates": candidate,
        }, target_opening="", bridge_required=False)

    async def contract(self, **kwargs):
        calls.append("contract")
        return ()

    async def review(self, **kwargs):
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        assert KEY not in kwargs["session"].story_state["facts"]
        if not kwargs["claims"]:
            calls.append("ending_review")
            return ev.NumericV2StoryEvidenceReview((), kwargs.get("ending_route") is not None)
        calls.append("fact_review")
        assert "actor" in calls and "contract" in calls
        assert kwargs["actor_evidence"] and body in kwargs["actor_evidence"]
        assert all(claim["key"] == KEY for claim in kwargs["claims"])
        assert all(row["source"] == "actor_performance"
                   for claim in kwargs["claims"] for row in claim["evidence"])
        return ev.NumericV2StoryEvidenceReview(tuple(range(len(kwargs["claims"])))
                                               if received and approved else ())

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和克制。")
    return calls, original


async def _run(runtime, current, message, source="suggestion"):
    return await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("handoff", current.session.revision, message, source),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize("item", ["陶罐", "徽章"])
@pytest.mark.parametrize("ending", [False, True])
async def test_selected_displayed_action_still_needs_actual_receipt_and_independent_approval(
        tmp_path, monkeypatch, item, ending):
    message = _action(item)
    runtime, current = await _start(tmp_path, item, [message])
    calls, _ = _install(monkeypatch, runtime, current, item, ending=ending)
    result = await _run(runtime, current, message)
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert result.stored.session.revision == 1
    assert result.stored.session.status == ("ended" if ending else "active")
    assert calls.count("actor") == calls.count("fact_review") == 1
    assert calls.index("actor") < calls.index("contract") < calls.index("fact_review")
    assert result.stored.ledger_events[-1]["input_source"] == "suggestion"
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["freeform", "forged", "unshown", "stale"])
async def test_omitted_subject_needs_the_current_displayed_suggestion(tmp_path, monkeypatch, case):
    item, message = "陶罐", _action("陶罐")
    suggestions = [message] if case in {"freeform", "stale"} else ([] if case == "unshown" else ["检查柜台上的登记本"])
    runtime, current = await _start(tmp_path, item, suggestions)
    if case == "stale":
        staged = runtime.prepare_turn(current, TurnRequestV2("wait", 0, "先核对一下登记本。"), ())
        current = await runtime.commit_turn(staged, {"performance": "登记本还摊在柜台上。",
                                                    "suggested_inputs": ["继续核对登记本"]})
    calls, original = _install(monkeypatch, runtime, current, item, ending=True)
    with pytest.raises(wf.NumericV2ActorOutputError, match="player_handoff_unverified"):
        await _run(runtime, current, message, "freeform" if case == "freeform" else "suggestion")
    assert "fact_review" not in calls
    assert runtime.store._path(current.session.session_id).read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "他将陶罐递向保管员", "请你将陶罐递向保管员", "准备将陶罐递向保管员",
    "明天将陶罐递向保管员", "如果他来了，将陶罐递向保管员", "将陶罐递向保管员吗？",
])
async def test_a_displayed_nonaction_does_not_become_player_execution(tmp_path, monkeypatch, message):
    runtime, current = await _start(tmp_path, "陶罐", [message])
    _, original = _install(monkeypatch, runtime, current, "陶罐", ending=True)
    with pytest.raises(wf.NumericV2ActorOutputError, match="player_handoff_unverified"):
        await _run(runtime, current, message)
    assert runtime.store._path(current.session.session_id).read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["not_received", "review_rejected"])
@pytest.mark.parametrize("ending", [False, True])
async def test_selected_action_cannot_complete_without_reviewed_actor_receipt(tmp_path, monkeypatch, case, ending):
    message = _action("陶罐")
    runtime, current = await _start(tmp_path, "陶罐", [message])
    calls, original = _install(monkeypatch, runtime, current, "陶罐", ending=ending,
                               received=case != "not_received", approved=case != "review_rejected")
    if ending:
        with pytest.raises(wf.NumericV2ActorOutputError, match="player_handoff_unverified"):
            await _run(runtime, current, message)
        assert runtime.store._path(current.session.session_id).read_bytes() == original
    else:
        result = await _run(runtime, current, message)
        assert KEY not in result.stored.session.story_state["facts"]
        assert result.stored.session.status == "active"
    if case == "review_rejected":
        assert "fact_review" in calls


@pytest.mark.asyncio
async def test_evaluator_cannot_relabel_the_selected_input_as_performance_evidence(tmp_path, monkeypatch):
    message = _action("陶罐")
    runtime, current = await _start(tmp_path, "陶罐", [message])
    calls, _ = _install(monkeypatch, runtime, current, "陶罐", received=False,
                        actor_candidate=False, wrong_evaluator_source=True)
    result = await _run(runtime, current, message)
    assert KEY not in result.stored.session.story_state["facts"]
    assert result.stored.session.status == "active"
    assert "fact_review" not in calls
    assert result.stored.ledger_events[-1].get("fact_operations", []) == []
