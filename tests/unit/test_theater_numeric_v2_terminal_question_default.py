"""Terminal question checks preserve legitimate greetings when full review is disabled."""

import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_confirmation_only import _offered
from tests.unit.test_theater_numeric_v2_same_turn_invitation import ACCEPT
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


GREETING = "（点头）你也回来了？好久不见。"
CHOICE = "（停下）你选左边的房间，还是右边的房间？"
CLOSING = "（微笑）好久不见。这一路辛苦了。"


async def _setup(tmp_path, monkeypatch, *, mode="default", drafts=(GREETING,),
                 field="target_performance", failure="", route="ending"):
    """Replace only the model boundary while retaining routing, segment assembly, repair and atomic commit."""
    case = initiation_case(public=True, message="带我去阅览室吧。") if route == "transition" else None
    if mode == "all_off":
        # 使用真正签发并提交的邀请回执，关闭 Evaluator 后仍由原程序接受路径换幕。
        # 外部旧引擎把该目标标为 terminal，复用既有防御场景，不伪造接受意图。
        runtime, current, _ = await _offered(tmp_path, monkeypatch)
        engine = runtime.engine
        engine.nodes["ending_leave"]["terminal"] = True
    else:
        engine = case["engine"] if case else _engine()
        runtime = NumericV2Runtime(engine, tmp_path)
        current = await runtime.start_session(session_id="ending-question-default", catgirl_binding=_binding(),
                                              opening_performance=case["session"].opening_performance if case else _opening())
    calls = {"actor": 0, "terminal": [], "full": 0, "other_contract": 0}
    diagnostics = {}

    async def options():
        if mode == "all_off":
            return {key: False for key in default_options()}
        return {**{key: key == 'evaluator' for key in default_options()}, **({mode: True} if mode != "default" else {})}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), route == "ending",
            natural_ending_ready=route == "ending", transition_intent="initiate" if case else "unclear")

    async def generate(self, **kwargs):
        index = calls["actor"]
        calls["actor"] += 1
        text = drafts[min(index, len(drafts) - 1)]
        if route == "ordinary":
            return {"performance": text, "suggested_inputs": [], "transition_offered": False}
        candidate = _candidate()
        candidate[field] = text
        return engine.finalize_transition_performance(kwargs["outcome"], candidate, target_opening="雨后的长街。")

    async def narrow(self, **kwargs):
        boundaries = kwargs["node"]["story_beat"].get("must_not_happen", [])
        if not any("下一轮回答" in item for item in boundaries):
            calls["other_contract"] += 1
            return ()
        # 通过真实投影检查每份草稿的全部可见段，不能沿用旧稿的批准结果。
        assert kwargs["include_all_segments"] is True
        assert kwargs["terminal_input_closed"] is True
        visible = json.dumps(evaluator._context_content(kwargs["actor_performance"],
            include_all_segments=kwargs["include_all_segments"]), ensure_ascii=False)
        calls["terminal"].append(visible)
        if failure == "timeout":
            raise evaluator.NumericV2EvaluatorError("numeric_v2_contract_check_timeout")
        if failure == "invalid":
            raise evaluator.NumericV2EvaluatorOutputError("numeric_v2_contract_check_invalid")
        return tuple(boundaries) if CHOICE.split("）")[-1] in visible else ()

    async def full_review(self, **kwargs):
        calls["full"] += 1
        return evaluator.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "verify_contract_boundaries", narrow)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", full_review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和克制。")

    async def execute():
        message = ACCEPT if mode == "all_off" else (case["message"] if case else "就到这里吧。")
        return await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("finish", current.session.revision, message,
                              "suggestion" if mode == "all_off" else "freeform"),
            ensure_current_binding=lambda _: _binding(), diagnostics_sink=diagnostics)

    return runtime, current, calls, diagnostics, execute


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["default", "review_contract", "review"])
@pytest.mark.parametrize("disposition", ["greeting", "repair", "reject"])
async def test_terminal_question_uses_same_semantic_boundary_under_each_mode(
    tmp_path, monkeypatch, mode, disposition,
):
    # 改稿仍保留一个无待答要求的疑问句，证明第二稿必须独立核对而不是仅看标点消失。
    drafts = (GREETING,) if disposition == "greeting" else ((CHOICE, GREETING) if disposition == "repair" else (CHOICE,))
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, mode=mode, drafts=drafts)
    if disposition == "reject":
        with pytest.raises(workflow.NumericV2ActorOutputError, match="terminal_new_question"):
            await execute()
        assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == current
        assert calls["actor"] == 2 and len(calls["terminal"]) == 2
        assert calls["full"] == 0
        return
    result = await execute()
    expected_calls = 2 if disposition == "repair" else 1
    assert calls["actor"] == len(calls["terminal"]) == expected_calls
    assert GREETING.split("）")[-1] in calls["terminal"][-1]
    if disposition == "repair":
        assert calls["terminal"][0] != calls["terminal"][1]
    assert calls["full"] == int(mode == "review")
    assert diagnostics["terminal_question_review_calls"] == expected_calls
    assert result.stored.session.status == "ended"
    assert result.stored.session.revision == current.session.revision + 1
    assert result.stored.session.metrics == current.session.metrics
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["source_performance", "bridge_scene_narration", "target_scene_narration"])
async def test_default_terminal_check_reads_source_bridge_and_target_narration(tmp_path, monkeypatch, field):
    runtime, current, calls, _, execute = await _setup(tmp_path, monkeypatch, field=field)
    result = await execute()
    assert calls["actor"] == len(calls["terminal"]) == 1
    assert GREETING.split("）")[-1] in calls["terminal"][0]
    assert result.stored.session.status == "ended"
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "invalid"])
async def test_default_terminal_unknown_check_rolls_back_without_another_actor(tmp_path, monkeypatch, failure):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, failure=failure)
    with pytest.raises(workflow.NumericV2ActorOutputError, match="transition_review_failed"):
        await execute()
    assert calls["actor"] == len(calls["terminal"]) == 1
    assert calls["full"] == 0
    assert diagnostics["terminal_question_review_degraded"] is True
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("route,drafts", [("ending", (CLOSING,)), ("ordinary", (CHOICE,)), ("transition", (CHOICE,))])
async def test_no_question_and_nonterminal_turns_add_no_terminal_check(tmp_path, monkeypatch, route, drafts):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, route=route, drafts=drafts)
    result = await execute()
    assert calls["actor"] == 1
    assert calls["terminal"] == [] and calls["full"] == 0
    assert diagnostics["terminal_question_review_calls"] == 0
    assert result.stored.session.status == ("ended" if route == "ending" else "active")
    if route == "transition":
        assert result.stored.session.current_node_id != current.session.current_node_id
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_all_auxiliary_modules_off_keeps_zero_call_conservative_terminal_gate(tmp_path, monkeypatch):
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, mode="all_off")
    with pytest.raises(workflow.NumericV2ActorOutputError, match="terminal_new_question"):
        await execute()
    assert calls["actor"] == 2 and calls["terminal"] == [] and calls["full"] == 0
    assert diagnostics["terminal_question_review_calls"] == 0
    assert diagnostics["evaluator_skipped"] is True
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [GREETING, CHOICE])
async def test_paused_full_reviews_still_distinguish_terminal_questions_from_greetings(tmp_path, monkeypatch, text):
    # Only terminal interaction semantics are checked; ordinary/full review
    # remains paused, and greetings are never rejected just for punctuation.
    from services.theater import numeric_v2_options

    monkeypatch.setattr(numeric_v2_options, "MODEL_REVIEWS_ENABLED", False)
    runtime, current, calls, diagnostics, execute = await _setup(tmp_path, monkeypatch, drafts=(text, CLOSING))
    result = await execute()
    assert calls["actor"] == (2 if text == CHOICE else 1)
    assert len(calls["terminal"]) == 1 and calls["full"] == 0
    assert diagnostics["terminal_question_review_calls"] == 1
    assert result.stored.session.status == "ended"
    assert result.stored.session.metrics == current.session.metrics
    assert result.performance["suggested_inputs"] == []
    assert await runtime.restore_session(current.session.session_id) == result.stored


def test_terminal_check_changes_only_its_system_contract_and_preserves_all_input_segments():
    # 同一份完整可见演绎保留来源、桥段和目标；切换判定任务不裁剪用户原话或边界。
    candidate = json.dumps({"segments": [{"phase": phase, "performance": text} for phase, text in (
        ("source_response", "谢谢你。"), ("transition_bridge", "那天以后，她常想起这句话。"),
        ("target_opening", GREETING))]}, ensure_ascii=False)
    kwargs = dict(required=["不得在结局提出需要下一轮回答的问题。"], candidate_text=candidate, player_input="约定好了。")
    legacy = evaluator._build_contract_check_messages(**kwargs)
    explicit_false = evaluator._build_contract_check_messages(**kwargs, terminal_input_closed=False)
    terminal = evaluator._build_contract_check_messages(**kwargs, terminal_input_closed=True)
    assert [m.content for m in legacy] == [m.content for m in explicit_false]
    assert terminal[1].content == legacy[1].content
    assert "下一轮" in terminal[0].content
    assert "询问或未来计划的，不算已经发生" not in terminal[0].content
    assert "交互要求，一律不判违规" not in terminal[0].content
    assert "询问或未来计划的，不算已经发生" in legacy[0].content
    payload = json.loads(terminal[1].content.split("\n", 1)[1])
    assert payload == {"author_boundaries": kwargs["required"], "player_input": kwargs["player_input"],
                       "candidate_visible_text": candidate}


@pytest.mark.parametrize("invalid", ["自行创造的违规理由", 7, None, {"reason": "需要回答"}])
def test_terminal_strict_parse_rejects_unknown_or_nonstring_verdict_instead_of_approving(invalid):
    required = ["不得在结局追加需要下一轮回答的问题。"]
    content = json.dumps({"violated": [required[0], invalid]}, ensure_ascii=False)
    with pytest.raises(evaluator.NumericV2EvaluatorOutputError, match="contract_check_invalid"):
        evaluator._parse_contract_check_output(content, required, strict=True)
    # 原作者禁令核对保留原容错语义，不能把终局收紧扩散到所有调用。
    assert evaluator._parse_contract_check_output(content, required) == tuple(required)
    assert evaluator._parse_contract_check_output(content, required, strict=False) == tuple(required)


@pytest.mark.parametrize("violated", [[], ["不得在结局追加需要下一轮回答的问题。"]])
def test_terminal_strict_parse_preserves_valid_empty_and_exact_verdicts(violated):
    required = ["不得在结局追加需要下一轮回答的问题。"]
    assert evaluator._parse_contract_check_output(json.dumps({"violated": violated}, ensure_ascii=False),
        required, strict=True) == tuple(violated)
