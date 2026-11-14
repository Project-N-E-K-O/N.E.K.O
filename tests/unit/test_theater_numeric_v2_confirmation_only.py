"""Only explicitly authored confirmation buttons may avoid semantic evaluation."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit import test_theater_numeric_v2_same_turn_invitation as invitation


async def _offered(tmp_path, monkeypatch, flag=True):
    original = invitation.story_for

    def authored(destination):
        story = original(destination)
        if flag is not None:
            story["nodes"][0]["route_gates"][1]["transition_contract"]["accept_input_confirmation_only"] = flag
        return story

    monkeypatch.setattr(invitation, "story_for", authored)
    runtime, current, calls = await invitation._setup(tmp_path, monkeypatch)
    completed = await invitation._turn(runtime, current)
    original_options = wf.aload_theater_module_options

    async def compatibility_options():
        return {**await original_options(), "evaluator": False, "review": False}

    async def settled_response(self, **kwargs):
        calls["actor"] += 1
        assert kwargs["outcome"].ledger_event["from_node_id"] == kwargs["outcome"].ledger_event["to_node_id"]
        return {"performance": "文书核验已经结束。", "suggested_inputs": ["谢谢管事。"],
            "transition_offered": False, "fact_candidates": []}

    # 这些用例验证旧作者 confirmation_only 合同：用真实模块关闭兼容链签发静态回执，
    # 再恢复默认判定；不能将动态审批回执换个 kind 冒充旧权限。
    with monkeypatch.context() as compatibility:
        compatibility.setattr(wf, "aload_theater_module_options", compatibility_options)
        compatibility.setattr(wf.NumericV2Actor, "generate_turn", settled_response)
        result = await invitation._turn(runtime, completed.stored, "compat-offer", "接下来呢？")
    assert result.stored.ledger_events[-1]["program_invitation"]["accept_input"] == invitation.ACCEPT
    assert result.stored.ledger_events[-1]["program_invitation"]["offer"] == invitation.OFFER
    assert result.stored.ledger_events[-1]["program_invitation"].get("kind") != "reviewed_dynamic"
    assert runtime.engine.completion_contract_satisfied(result.stored.session) is True
    calls.update(evaluator=0, actor=0, review=0)
    return runtime, result.stored, calls


def _turn(current, **overrides):
    return TurnRequestV2("confirm", current.session.revision,
        overrides.get("message", invitation.ACCEPT), overrides.get("source", "suggestion"))


@pytest.mark.asyncio
@pytest.mark.parametrize("review", [False, True])
async def test_confirmation_preserves_route_facts_review_and_restore_without_evaluation(tmp_path, monkeypatch, review):
    runtime, current, calls = await _offered(tmp_path, monkeypatch)
    restored = await runtime.restore_session(current.session.session_id)
    fork = await runtime.fork_session_for_test(current.session.session_id, session_id="confirm-fork",
        through_revision=current.session.revision)

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": review}

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    for index, stored in enumerate([restored, fork]):
        before = deepcopy(stored)
        result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=stored,
            turn=_turn(stored), ensure_current_binding=lambda _: invitation._binding())
        assert result.stored.session.current_node_id == "ending_leave"
        assert result.stored.session.metrics == stored.session.metrics
        assert result.stored.session.story_state["facts"][invitation.KEY] == stored.session.story_state["facts"][invitation.KEY]
        assert result.stored.ledger_events[-1]["metric_changes"] == []
        assert result.performance["suggested_inputs"] == invitation.TARGET_SUGGESTIONS
        assert result.diagnostics["evaluator_model_attempts"] == 0
        assert result.diagnostics["evaluator_skip_reason"] == "authored_confirmation_only"
        assert calls == {"evaluator": 0, "actor": index + 1, "review": int(review) * (index + 1)}
        assert stored == before
        assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(stored.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, False])
async def test_legacy_contracts_still_evaluate(tmp_path, monkeypatch, flag):
    runtime, current, calls = await _offered(tmp_path, monkeypatch, flag)
    result = await invitation._turn(runtime, current, "legacy", invitation.ACCEPT, "suggestion")
    assert result.stored.session.current_node_id == "ending_leave"
    assert calls == {"evaluator": 1, "actor": 1, "review": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["actor", "review", "commit"])
async def test_skipping_evaluation_keeps_atomic_failure_guards(tmp_path, monkeypatch, stage):
    runtime, current, calls = await _offered(tmp_path, monkeypatch)
    original = runtime.store._path(current.session.session_id).read_bytes()

    async def fail(*args, **kwargs):
        raise RuntimeError("confirmation_failure")

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": stage == "review"}

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    if stage == "actor":
        monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", fail)
    elif stage == "review":
        monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", fail)
    with pytest.raises(RuntimeError, match="confirmation_failure"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=_turn(current), ensure_current_binding=lambda _: invitation._binding(),
            before_commit=fail if stage == "commit" else None)
    assert calls["evaluator"] == 0
    assert runtime.store._path(current.session.session_id).read_bytes() == original
    assert await runtime.restore_session(current.session.session_id) == current
