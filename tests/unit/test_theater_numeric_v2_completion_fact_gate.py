"""New Actor completion facts need one bounded approval before progress changes."""

import asyncio
from copy import deepcopy
from itertools import count
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit import test_theater_numeric_v2_same_turn_invitation as scenario
from tests.unit.test_theater_numeric_v2_runtime import _binding


def _candidate():
    return {"key": scenario.KEY, "value": True, "evidence_quote": scenario.NPC_RESULT}


def _install_review(monkeypatch, calls, result=None, *, error=None, seen=None):
    async def review(self, **kwargs):
        calls["review"] += 1
        if seen is not None:
            seen.append({**kwargs, "actor_performance": deepcopy(kwargs["actor_performance"])})
        if error is not None:
            raise error
        if result is None and kwargs["actor_performance"].get("transition_offered"):
            return ev.NumericV2TransitionOfferReview(True, True, (), (),
                offer_quote=scenario.OFFER, offer_kind="invitation",
                approved_suggestion_texts=(scenario.ACCEPT,))
        return result if result is not None else ev.NumericV2TransitionOfferReview(
            False, False, (), (), fact_candidates=(_candidate(),))

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)


def _assert_no_completion(result):
    assert scenario.KEY not in result.stored.session.story_state["facts"]
    assert not result.stored.session.transition_offered
    assert "program_invitation" not in result.stored.ledger_events[-1]
    assert scenario.OFFER not in result.performance["performance"]


@pytest.mark.asyncio
@pytest.mark.parametrize("result_text", [
    "值班老师清点了借书并签收，说：数量一致，交接完成。",
    "检修员检查了接口并签收，说：设备已接收，可以离开机库。",
])
@pytest.mark.parametrize("approve", [False, True])
async def test_route_only_npc_result_needs_review_before_commit_restore_and_fork(
    tmp_path, monkeypatch, result_text, approve,
):
    # Use the normal Workflow with the same public target only in route gates;
    # removing completion must neither discard the candidate nor bless its prose.
    original_story = scenario.story_for

    def route_only_story(destination):
        story = original_story(destination)
        story["nodes"][0].pop("completion_contract")
        return story

    monkeypatch.setattr(scenario, "story_for", route_only_story)
    monkeypatch.setattr(scenario, "NPC_RESULT", result_text)
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    before = runtime.store._path(current.session.session_id).read_bytes()
    original_candidate = _candidate()

    async def review(self, **kwargs):
        calls["review"] += 1
        assert kwargs["completion_fact_review_only"] is True
        assert kwargs["actor_performance"]["fact_candidates"] == [original_candidate]
        assert scenario.KEY not in kwargs["session"].story_state["facts"]
        assert runtime.store._path(current.session.session_id).read_bytes() == before
        return ev.NumericV2TransitionOfferReview(
            False, False, (), (), fact_candidates=(original_candidate,) if approve else ())

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    result = await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert result.performance["scene_narration"] == result_text
    assert result.stored.session.current_node_id == "start"
    assert (scenario.KEY in result.stored.session.story_state["facts"]) is approve
    assert bool(runtime.engine.preview_route("start", result.stored.session.metrics,
        facts=result.stored.session.story_state["facts"])) is approve
    assert result.diagnostics["completion_fact_review_calls"] == 1
    assert not result.stored.session.transition_offered
    assert "program_invitation" not in result.stored.ledger_events[-1]
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored
    fork = await runtime.fork_session_for_test(current.session.session_id,
        session_id="route-only-result-fork", through_revision=result.stored.session.revision)
    assert fork.session.story_state == result.stored.session.story_state
    assert fork.session.performance_history == result.stored.session.performance_history
    assert fork.ledger_events[-1].get("fact_operations", []) == result.stored.ledger_events[-1].get("fact_operations", [])
    assert bool(fork.ledger_events[-1].get("fact_operations")) is approve


@pytest.mark.asyncio
async def test_approved_npc_result_commits_before_new_exit_is_invited(tmp_path, monkeypatch):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    # Keep timing assertions independent of Windows monotonic-clock resolution.
    ticks = count(100.0, 0.001)
    monkeypatch.setattr(wf, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    seen = []
    _install_review(monkeypatch, calls, seen=seen)

    async def unexpected_narrative(self, **kwargs):
        raise AssertionError("completion approval must not start narrative extraction")

    monkeypatch.setattr(wf.NumericV2NarrativeProposer, "propose", unexpected_narrative)
    result = await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert result.performance["scene_narration"] == scenario.NPC_RESULT
    assert result.stored.session.story_state["facts"][scenario.KEY]["value"] is True
    assert scenario.OFFER not in result.performance["performance"]
    assert not result.stored.session.transition_offered
    assert "program_invitation" not in result.stored.ledger_events[-1]
    assert result.diagnostics["completion_fact_review_calls"] == 1
    assert result.diagnostics["transition_judge_calls"] == 1
    timings = result.diagnostics["timings_ms"]
    assert 0 < timings["completion_fact_review_work"] <= timings["transition_judge_work"]
    assert seen[0].get("check_missed_initiation", False) is False
    assert seen[0].get("dispute_review", False) is False
    assert seen[0].get("transition_outcome") is None
    assert scenario.OFFER not in seen[0]["actor_performance"]["performance"]
    assert await runtime.restore_session(current.session.session_id) == result.stored
    fork = await runtime.fork_session_for_test(current.session.session_id,
        session_id="approved-fork", through_revision=result.stored.session.revision)
    assert fork.session.story_state == result.stored.session.story_state
    # Fork identities differ; the approved facts and performed body must replay.
    assert fork.session.performance_history == result.stored.session.performance_history
    assert fork.ledger_events[-1]["fact_operations"] == result.stored.ledger_events[-1]["fact_operations"]
    assert "program_invitation" not in fork.ledger_events[-1]


@pytest.mark.asyncio
async def test_unapproved_candidate_does_not_reject_legal_body(tmp_path, monkeypatch):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    _install_review(monkeypatch, calls, ev.NumericV2TransitionOfferReview(False, False, (), ()))
    result = await scenario._turn(runtime, current)
    assert result.stored.session.revision == current.session.revision + 1
    assert result.performance["scene_narration"] == scenario.NPC_RESULT
    assert result.stored.session.performance_history[-1]["scene_narration"] == scenario.NPC_RESULT
    _assert_no_completion(result)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}


@pytest.mark.asyncio
async def test_rejected_completion_repairs_and_rechecks_even_without_new_candidates(tmp_path, monkeypatch):
    """A fresh, incomplete answer still needs its own verdict before the sole commit."""
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()
    rejected = "你替自己补上签名，管事盖章后放行。"
    repaired = "管事还在核对文书，我们先等他确认。"
    seen, generated = [], []

    async def generate(self, **kwargs):
        calls["actor"] += 1
        generated.append(kwargs)
        assert calls["actor"] <= 2
        assert scenario.KEY not in kwargs["outcome"].session.story_state["facts"]
        if calls["actor"] == 1:
            return {"performance": "签名和核验都办妥了。", "scene_narration": rejected,
                "suggested_inputs": [], "transition_offered": False,
                "fact_candidates": [{"key": scenario.KEY, "value": True, "evidence_quote": rejected}]}
        return {"performance": repaired, "suggested_inputs": [], "transition_offered": False}

    async def review(self, **kwargs):
        calls["review"] += 1
        seen.append(deepcopy(kwargs["actor_performance"]))
        assert kwargs["completion_fact_review_only"] is True
        assert kwargs["session"].revision == current.session.revision
        assert kwargs["session"].performance_history == current.session.performance_history
        assert scenario.KEY not in kwargs["session"].story_state["facts"]
        assert path.read_bytes() == before
        if calls["review"] == 1:
            return ev.NumericV2TransitionOfferReview(False, False, ("player_action",), (),
                "玩家未签名，不能代填。", fact_candidates=tuple(kwargs["actor_performance"]["fact_candidates"]))
        assert calls["review"] == 2
        assert kwargs["actor_performance"]["performance"] == repaired
        assert not kwargs["actor_performance"].get("fact_candidates")
        return ev.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    result = await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 2, "review": 2}
    assert result.performance["performance"] == repaired
    assert len(seen) == len(generated) == 2
    assert generated[1]["session"] == current.session
    assert result.stored.session.revision == current.session.revision + 1
    assert len(result.stored.session.performance_history) == len(current.session.performance_history) + 1
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert rejected not in str(result.stored.session.performance_history)
    _assert_no_completion(result)
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("second_rejection", ["body", "invalid_offer"])
async def test_second_completion_rejection_rolls_back_without_a_third_draft(tmp_path, monkeypatch, second_rejection):
    """Dropping fact candidates cannot let an independently rejected new body or offer commit."""
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()

    async def generate(self, **kwargs):
        calls["actor"] += 1
        assert calls["actor"] <= 2
        if calls["actor"] == 1:
            return {"performance": "核验已经完成。", "scene_narration": scenario.NPC_RESULT,
                "suggested_inputs": [], "transition_offered": False, "fact_candidates": [_candidate()]}
        return {"performance": "我们已经到了院门，进去吧。", "suggested_inputs": [],
            "transition_offered": second_rejection == "invalid_offer"}

    async def review(self, **kwargs):
        calls["review"] += 1
        assert calls["review"] <= 2
        if calls["review"] == 1:
            return ev.NumericV2TransitionOfferReview(False, False, ("author_boundary",), (),
                "核验前因仍不成立。")
        assert kwargs["completion_fact_review_only"] is True
        assert not kwargs["actor_performance"].get("fact_candidates")
        return ev.NumericV2TransitionOfferReview(
            second_rejection == "invalid_offer", False,
            ("scene_boundary",) if second_rejection == "body" else (), (), "新稿仍越过当前阶段。")

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_completion_fact_review_rejected$"):
        await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 2, "review": 2}
    assert path.read_bytes() == before
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_completion_repair_exhausted_budget_does_not_start_another_actor(tmp_path, monkeypatch):
    """Advance only the Workflow clock; asyncio timers and provider cleanup remain real."""
    from types import SimpleNamespace

    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()
    real_time, elapsed = wf.time, [0.0]
    monkeypatch.setattr(wf, "time", SimpleNamespace(monotonic=lambda: real_time.monotonic() + elapsed[0]))

    async def review(self, **kwargs):
        calls["review"] += 1
        elapsed[0] = wf.NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS + 1.0
        return ev.NumericV2TransitionOfferReview(False, False, ("player_action",), (),
            "仍缺玩家实际签名。")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    with pytest.raises(wf.NumericV2ActorError):
        await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert path.read_bytes() == before
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [
    ev.NumericV2EvaluatorError("numeric_v2_transition_judge_timeout"),
    ev.NumericV2EvaluatorOutputError("numeric_v2_transition_judge_invalid_json"),
])
async def test_failed_completion_check_leaves_store_unchanged(tmp_path, monkeypatch, error):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    before = runtime.store._path(current.session.session_id).read_bytes()
    _install_review(monkeypatch, calls, error=error)
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_completion_fact_review_failed$"):
        await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing", "invalid_quote", "already_committed"])
async def test_no_new_eligible_candidate_adds_no_check(tmp_path, monkeypatch, case):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch,
        scenario="valid" if case == "already_committed" else case)
    if case == "already_committed":
        current = (await scenario._turn(runtime, current)).stored
    prior_calls = dict(calls)

    async def unexpected_review(self, **kwargs):
        raise AssertionError("no new eligible completion fact may add a review call")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", unexpected_review)
    result = await scenario._turn(runtime, current, key="next")
    assert calls["review"] == prior_calls["review"]
    assert result.diagnostics.get("completion_fact_review_calls", 0) == 0
    if case != "already_committed":
        _assert_no_completion(result)


@pytest.mark.asyncio
async def test_enabled_review_reuses_existing_final_verdict(tmp_path, monkeypatch):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch, review=True)
    result = await scenario._turn(runtime, current)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert result.diagnostics.get("completion_fact_review_calls", 0) == 0
    assert result.stored.session.story_state["facts"][scenario.KEY]["value"] is True
    assert scenario.OFFER not in result.performance["performance"]


@pytest.mark.asyncio
async def test_disabled_evaluator_and_review_freeze_actor_completion_only(tmp_path, monkeypatch):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "evaluator": False, "review": False}

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    result = await scenario._turn(runtime, current)
    assert result.performance["scene_narration"] == scenario.NPC_RESULT
    assert result.stored.session.revision == current.session.revision + 1
    _assert_no_completion(result)
    assert calls == {"evaluator": 0, "actor": 1, "review": 0}


@pytest.mark.asyncio
async def test_review_cannot_invent_key_or_change_original_value_or_quote(tmp_path, monkeypatch):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    original = _candidate()
    changed = (
        {**original, "key": "scene:start:other"},
        {**original, "value": False},
        {**original, "evidence_quote": "文书无误，可以通行。"},
    )
    _install_review(monkeypatch, calls, ev.NumericV2TransitionOfferReview(
        False, False, (), (), fact_candidates=changed))
    result = await scenario._turn(runtime, current)
    _assert_no_completion(result)
    assert not result.stored.ledger_events[-1].get("fact_operations")
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}


@pytest.mark.asyncio
async def test_fifth_candidate_cannot_bypass_four_item_review_limit(tmp_path, monkeypatch):
    original_story = scenario.story_for
    keys = [scenario.KEY, *(f"scene:start:check_{index}" for index in range(1, 5))]

    def story_with_five_checks(destination):
        story = original_story(destination)
        for key in keys[1:]:
            story["fact_contract"]["facts"][key] = deepcopy(story["fact_contract"]["facts"][scenario.KEY])
            story["nodes"][0]["completion_contract"]["all"].append({"key": key, "equals": True})
        return story

    monkeypatch.setattr(scenario, "story_for", story_with_five_checks)
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    original_generate = wf.NumericV2Actor.generate_turn
    candidates = [{**_candidate(), "key": key} for key in keys]

    async def generate(self, **kwargs):
        body = await original_generate(self, **kwargs)
        body["fact_candidates"] = deepcopy(candidates)
        return body

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    _install_review(monkeypatch, calls, ev.NumericV2TransitionOfferReview(
        False, False, (), (), fact_candidates=tuple(candidates)))
    result = await scenario._turn(runtime, current)
    facts = result.stored.session.story_state["facts"]
    assert all(facts[key]["value"] is True for key in keys[:4])
    assert keys[4] not in facts
    assert not result.stored.session.transition_offered
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}


@pytest.mark.asyncio
async def test_cancelling_completion_check_awaits_cleanup_without_commit(tmp_path, monkeypatch):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    before = runtime.store._path(current.session.session_id).read_bytes()
    entered, cleaned = asyncio.Event(), asyncio.Event()

    async def review(self, **kwargs):
        calls["review"] += 1
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    task = asyncio.create_task(scenario._turn(runtime, current))
    waiting = asyncio.create_task(entered.wait())
    try:
        await asyncio.wait({task, waiting}, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        assert entered.is_set(), "new completion facts must reach the approval gate"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
    finally:
        for pending in (task, waiting):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, waiting, return_exceptions=True)
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_commit_fence_still_protects_approved_fact_and_invitation(tmp_path, monkeypatch):
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch)
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def before_commit():
        raise RuntimeError("completion_gate_commit_fence")

    with pytest.raises(RuntimeError, match="^completion_gate_commit_fence$"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("fence", current.session.revision, "请管事核验文书。"),
            ensure_current_binding=lambda _: _binding(), before_commit=before_commit)
    assert calls == {"evaluator": 1, "actor": 1, "review": 1}
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current
