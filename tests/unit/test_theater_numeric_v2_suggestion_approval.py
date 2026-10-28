"""Review approves each original button without granting actions or new calls."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater import numeric_v2_context as context_module
from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


CHOICES = ("（点头）我先想一想。", "（读起尚未打开的信）", "（看向你）你有什么建议？")


@pytest.mark.parametrize("performance,kept", [
    ("（按下仪表按钮，读数显示北侧通道）可以看清了。", True),
    ("（准备按下仪表按钮）还不知道读数。", False),
    ("（按下仪表按钮）读数尚未出现。", False),
])
def test_prefilter_uses_visible_action_result_without_inventing_unshown_reading(performance, kept):
    text = "（看向仪表）读数显示北侧通道。"
    candidate = {"performance": performance, "suggested_inputs": [text], "transition_offered": False}
    before = deepcopy(candidate)
    result, removed, _ = wf._prefilter_suggestion_candidates(candidate)
    assert result["suggested_inputs"] == ([text] if kept else [])
    assert removed == int(not kept)
    assert result["performance"] == performance
    assert candidate == before
    assert "fact_candidates" not in result


def _payload():
    return {
        "offer_present": False, "valid": False, "body_violations": [],
        "suggestion_checks": [
            {"index": index, "decision": "reject" if index == 1 else "allow", "requires": []}
            for index in range(3)
        ],
    }


def _parse(payload=None, **kwargs):
    return ev._parse_transition_judge_output(
        json.dumps(_payload() if payload is None else payload, ensure_ascii=False),
        display_suggestions=CHOICES, **kwargs,
    )


def _filter(review, choices=CHOICES):
    candidate = {"performance": "（守着封好的信）等你决定。", "scene_narration": "窗外有雨声。",
                 "transition_offered": False, "fact_candidates": [], "suggested_inputs": list(choices)}
    original = deepcopy(candidate)
    result, removed = wf._drop_reported_unsafe_suggestions(
        candidate, review.unsafe_suggestion_indexes, review=review,
    )
    assert candidate == original
    assert {key: value for key, value in result.items() if key != "suggested_inputs"} == {
        key: value for key, value in original.items() if key != "suggested_inputs"
    }
    return result, removed


def test_complete_mixed_verdict_still_revokes_the_batch():
    review = _parse()
    assert review.approved_suggestion_texts == (CHOICES[0], CHOICES[2])
    assert review.unsafe_suggestion_indexes == (1,)
    result, removed = _filter(review, [CHOICES[2], "未经复核的新选项。", CHOICES[1], CHOICES[0]])
    assert result["suggested_inputs"] == []
    assert removed == 4


def test_all_allowed_verdict_binds_original_text_and_excludes_unreviewed_additions():
    choices = (CHOICES[0], CHOICES[2])
    payload = {**_payload(), "suggestion_checks": [
        {"index": index, "decision": "allow", "requires": []} for index in range(2)
    ]}
    review = ev._parse_transition_judge_output(json.dumps(payload), display_suggestions=choices)
    result, removed = _filter(review, [choices[1], "未经复核的新选项。", choices[0]])
    assert result["suggested_inputs"] == [choices[1], choices[0]]
    assert removed == 1


def test_frozen_real_review_false_allow_cannot_escape_a_sibling_rejection():
    """Replay the first live Review: it missed an unopened-cover premise but rejected a fabricated childhood."""
    choices = (
        "（凑近查看已经打开的检修盖内部）里面有什么？",
        "（保持距离等待）你慢慢来。",
        "（回忆小时候修收音机的经历）我也遇到过这种故障。",
    )
    raw = {
        "offer_present": False, "offer_quote": "", "offer_kind": "", "valid": False,
        "body_violations": [],
        "suggestion_checks": [
            {"index": 0, "decision": "allow", "requires": []},
            {"index": 1, "decision": "allow", "requires": []},
            {"index": 2, "decision": "reject", "requires": []},
        ],
        "failure_reason": "按钮索引2涉及回忆玩家个人经历，缺乏作者或历史依据。",
        "player_action_kind": "", "body_issues": [], "scene_update_removal_safe": True,
    }
    review = ev._parse_transition_judge_output(json.dumps(raw), display_suggestions=choices)
    assert review.approved_suggestion_texts == choices[:2]
    candidate = {
        "performance": "（拿起工具轻轻撬起封条边缘）既然陈叔同意开盖查看，那我就先看看里面有没有明显故障。至于签收的事，等你决定就好。",
        "scene_narration": "陈叔站在一旁，双手抱胸，目光停留在小岚身上，等待她的动作。",
        "suggested_inputs": list(choices), "transition_offered": False, "fact_candidates": [],
    }
    result, removed = wf._drop_reported_unsafe_suggestions(
        candidate, review.unsafe_suggestion_indexes, review=review,
    )
    assert result == {**candidate, "suggested_inputs": []}
    assert removed == 3


@pytest.mark.parametrize("changes", [
    {"suggestion_checks": None},
    {"suggestion_checks": []},
    {"suggestion_checks": [{"index": 0, "decision": "allow", "requires": []}]},
    {"unsafe_suggestion_indexes": []},
    {"unsafe_suggestion_indexes": [1]},
    {"unsafe_suggestion_indexes": "broken"},
])
def test_missing_legacy_or_corrupt_protocol_drops_buttons_not_body(changes):
    payload = _payload()
    payload.pop("suggestion_checks")
    payload.update(changes)
    payload["body_violations"] = ["author_boundary"]
    review = _parse(payload)
    assert review.body_violations == ("author_boundary",)
    assert review.approved_suggestion_texts is None
    assert review.unsafe_suggestion_indexes == (0, 1, 2)
    assert _filter(review)[0]["suggested_inputs"] == []


@pytest.mark.parametrize("change", [
    {"index": True}, {"index": 3}, {"index": 1}, {"decision": "unknown"},
    {"requires": ["0"]}, {"extra": True}, {"decision": []},
])
def test_invalid_individual_check_does_not_implicitly_approve_siblings(change):
    payload = _payload()
    payload["suggestion_checks"][0].update(change)
    assert _filter(_parse(payload))[0]["suggested_inputs"] == []


def test_mixed_legacy_and_new_protocol_remains_conservative():
    payload = _payload()
    payload["unsafe_suggestion_indexes"] = []
    assert _filter(_parse(payload))[0]["suggested_inputs"] == []


@pytest.mark.parametrize("formal,fixed", [(False, False), (True, True)])
def test_after_display_cannot_bypass_ordinary_fixed_candidate_scope(formal, fixed):
    payload = _payload()
    payload["suggestion_checks"][0] = {"index": 0, "decision": "after_display", "requires": ["0"]}
    if fixed:
        payload["fixed_narration_triggers"] = []
    if formal:
        payload["delivery_matches_route"] = True
    review = _parse(payload, fixed_narration_review=fixed, fixed_narration_ids=("letter",),
                    transition_delivery_review=formal)
    assert review.display_dependent_suggestions == ()
    assert _filter(review)[0]["suggested_inputs"] == []


def test_after_display_requires_actual_delivery_and_never_restores_rejected_text():
    payload = _payload()
    payload["fixed_narration_triggers"] = []
    payload["suggestion_checks"][0] = {"index": 0, "decision": "after_display", "requires": ["0"]}
    payload["suggestion_checks"][1]["decision"] = "allow"
    review = _parse(payload, fixed_narration_review=True, fixed_narration_ids=("letter",),
                    allow_after_display=True)
    assert review.approved_suggestion_texts == (CHOICES[1], CHOICES[2])
    candidate, _ = _filter(review)
    assert candidate["suggested_inputs"] == list(CHOICES)
    assert wf._drop_undelivered_display_suggestions(candidate, review, node_id="start")[0]["suggested_inputs"] == list(CHOICES[1:])
    candidate["fixed_narrations"] = [{"node_id": "start", "id": "letter", "position": "before"}]
    assert wf._drop_undelivered_display_suggestions(candidate, review, node_id="start")[0]["suggested_inputs"] == list(CHOICES)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ordinary", "opening", "formal", "completion_only"])
async def test_native_review_requests_complete_checks_once_without_display_escape(monkeypatch, phase):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="approval", catgirl_binding=_binding(), opening_performance=_opening())
    candidate = {"performance": "（守着封好的信）等你决定。", "suggested_inputs": list(CHOICES)}
    kwargs = {}
    if phase == "formal":
        outcome = engine.resolve_turn(session, TurnRequestV2("one", 0, "就到这里吧。"), (),
                                      scene_complete=True, natural_ending_ready=True)
        assert outcome.session.current_node_id != session.current_node_id
        kwargs.update(transition_outcome=outcome, route_changed=True)
        candidate["segments"] = []
    elif phase == "opening":
        kwargs["route_changed"] = True
    elif phase == "completion_only":
        kwargs["completion_fact_review_only"] = True
    calls = []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def factory(*args, **options):
        calls.append(options)
        return Client()

    async def invoke(client, messages, **options):
        schema = options["response_format"]["json_schema"]["schema"]
        if set(schema["properties"]) == {"body_violations", "failure_reason"}:
            return SimpleNamespace(content=json.dumps({"body_violations": [], "failure_reason": ""}))
        assert "suggestion_checks" in schema["required"]
        assert "unsafe_suggestion_indexes" not in schema["properties"]
        decisions = schema["properties"]["suggestion_checks"]["items"]["properties"]["decision"]["enum"]
        assert decisions == ["allow", "reject"]
        assert "suggestion_checks必须覆盖每个按钮" in messages[0].content
        # 仅靠完整 allow/reject 形状不能阻止无依据发现，既有同次审核须收到共享语义边界。
        assert messages[0].content.count(context_module.SUGGESTION_EVIDENCE_RULE) == 1
        payload = _payload()
        if phase == "formal":
            payload["delivery_matches_route"] = True
        return SimpleNamespace(content=json.dumps(payload))

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev, "invoke_with_usage", invoke)
    result = await ev.NumericV2MetricEvaluator(object()).validate_transition_offer(
        engine=engine, session=session, message="就到这里吧。",
        actor_performance=candidate, **kwargs,
    )
    assert len(calls) == (2 if phase == "formal" else 1)
    assert calls[0]["max_retries"] == 0
    assert result.approved_suggestion_texts == (CHOICES[0], CHOICES[2])


@pytest.mark.asyncio
async def test_workflow_mixed_verdict_commits_body_without_buttons_and_restores_without_extra_calls(tmp_path, monkeypatch):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="approval-store", catgirl_binding=_binding(), opening_performance=_opening())
    calls = {"actor": 0, "review": 0}

    async def enabled():
        return {"evaluator": True, "review": True, "dispute": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        return {"performance": "（守着封好的信）等你决定。", "suggested_inputs": list(CHOICES),
                "transition_offered": False}

    async def review(self, **kwargs):
        calls["review"] += 1
        return _parse()

    monkeypatch.setattr(wf, "aload_theater_module_options", enabled)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await wf.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("new", 0, "信先别拆。"), ensure_current_binding=lambda _: _binding(),
    )
    assert calls == {"actor": 1, "review": 1}
    assert result.performance["suggested_inputs"] == []
    assert result.performance["performance"] == "（守着封好的信）等你决定。"
    cold = await NumericV2Runtime(runtime.engine, tmp_path).restore_session("approval-store")
    assert cold == result.stored
    fork = await runtime.fork_session_for_test("approval-store", session_id="approval-fork", through_revision=1)
    assert fork.session.performance_history[-1]["suggested_inputs"] == []
    assert await runtime.restore_session("approval-fork") == fork
