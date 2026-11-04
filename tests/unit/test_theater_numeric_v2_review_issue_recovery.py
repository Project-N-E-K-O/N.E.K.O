"""Preserve located review vetoes without turning incomplete summaries into repair authority."""

import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow


def _issue(**changes):
    return {
        "code": "other", "field": "actor_performance", "quote": "候选中的原句。",
        "violations": ["author_boundary"], **changes,
    }


def _payload(issues, **changes):
    return json.dumps({
        "offer_present": False, "valid": False, "body_violations": [],
        "unsafe_suggestion_indexes": [], "failure_reason": "",
        "body_issues": issues, **changes,
    }, ensure_ascii=False)


def _parse(issues, *, evidence=None, **changes):
    return evaluator._parse_transition_judge_output(
        _payload(issues, **changes),
        body_evidence={"actor_performance": "候选中的原句。", "scene_update": "你重新回到店里。"}
        if evidence is None else evidence,
        scene_update_removal_allowed=True,
    )


@pytest.mark.parametrize("code", ["player_action", "scene_boundary", "author_boundary"])
def test_located_explicit_enum_survives_an_empty_top_level_summary(code):
    review = _parse([_issue(violations=[code])])
    assert review.body_violations == (code,)
    assert not review.offer_present and not review.valid
    assert not review.body_issues and not review.scene_update_removal_safe
    assert not review.fact_candidates and not review.approved_evaluator_fact_indexes


def test_recovery_adds_only_missing_enums_and_keeps_unlocated_existing_vetoes():
    review = _parse([_issue(violations=["author_boundary", "author_boundary"])],
                    body_violations=["player_action"])
    assert review.body_violations == ("player_action", "author_boundary")
    assert not review.body_issues


@pytest.mark.parametrize("issues", [
    None, {}, [], [_issue()] * 7,
    [_issue(quote="另一稿的原句。")], [_issue(field="scene_update")],
    [_issue(field="history")], [_issue(code="unknown")], [_issue(quote="")],
    [_issue(quote=[])], [_issue(violations=[])], [_issue(violations="author_boundary")],
    [_issue(violations=["unknown"])], [_issue(violations=["author_boundary", "unknown"])],
    [_issue(violations=[True])], [_issue(extra="author_boundary")],
    [_issue(), _issue(quote="另一稿的原句。")],
    [_issue(), _issue(violations=["unknown"])],
])
def test_incomplete_or_fabricated_issue_groups_cannot_create_a_veto(issues):
    review = _parse(issues, failure_reason="author_boundary：理由文字不能签发判定。")
    assert not review.body_violations and not review.body_issues


@pytest.mark.parametrize("evidence", [
    {}, {"scene_update": "候选中的原句。"},
    {"actor_performance": None}, {"actor_performance": ["候选中的原句。"]},
])
def test_recovery_requires_the_same_current_candidate_field(evidence):
    assert not _parse([_issue()], evidence=evidence).body_violations


def test_missing_evidence_and_reason_only_diagnostics_keep_legacy_no_offer_behavior():
    review = evaluator._parse_transition_judge_output(_payload(
        [_issue()], failure_reason="author_boundary：这只是文字说明。",
    ))
    assert not review.body_violations and not review.body_issues
    assert review.failure_reason == "author_boundary：这只是文字说明。"
    assert not review.offer_present and not review.valid


def test_a_malformed_issue_group_never_erases_an_existing_veto():
    review = _parse([_issue(), _issue(violations=["unknown"])], body_violations=["player_action"])
    assert review.body_violations == ("player_action",) and not review.body_issues


@pytest.mark.parametrize("fixed_review,forged_tail,expected", [
    (False, False, ()), (True, False, ("author_boundary",)), (True, True, ()),
])
def test_fixed_content_recovery_checks_the_entire_original_before_any_truncation(
    fixed_review, forged_tail, expected,
):
    original = "完整原文中的句子。" * 20
    review = evaluator._parse_transition_judge_output(_payload([
        _issue(code="fixed_narration_content", quote=original + ("虚构尾巴。" if forged_tail else "")),
    ], **({"fixed_narration_triggers": []} if fixed_review else {})),
        fixed_narration_review=fixed_review,
        body_evidence={"actor_performance": original}, scene_update_removal_allowed=True)
    assert review.body_violations == expected
    assert not review.body_issues and not review.scene_update_removal_safe


@pytest.mark.parametrize("code", ["other", "player_return_after_departure"])
def test_recovered_veto_never_grants_narration_deletion_or_a_movement_exemption(code):
    review = _parse([_issue(code=code, field="scene_update", quote="你重新回到店里。",
                            violations=["player_action"])],
                    player_action_kind="requested_movement", scene_update_removal_safe=True)
    candidate = {"performance": "明天见。", "scene_narration": "你重新回到店里。"}
    assert review.body_violations == ("player_action",)
    assert not review.player_action_kind and not review.body_issues
    assert not review.scene_update_removal_safe
    assert not workflow._review_mislabels_explicit_player_movement(review)
    assert workflow._safe_drop_invalid_scene_update(candidate, review) is None
    assert workflow._safe_degrade_conflicting_scene_update(
        candidate, review, workflow.project_player_action_result("（推门离开）明天见。"),
    ) is None


def test_already_complete_top_level_summary_keeps_existing_local_repair_permissions():
    issue = _issue(code="player_return_after_departure", field="scene_update",
                   quote="你重新回到店里。", violations=["player_action"])
    review = _parse([issue], body_violations=["player_action"], scene_update_removal_safe=True)
    assert review.body_issues == (issue,) and review.scene_update_removal_safe
    assert workflow._safe_degrade_conflicting_scene_update(
        {"performance": "明天见。", "scene_narration": issue["quote"]}, review,
        workflow.project_player_action_result("（推门离开）明天见。"),
    ) == {"performance": "明天见。", "transition_offered": False}


@pytest.mark.asyncio
async def test_native_review_rewrites_a_located_rejection_before_commit(tmp_path, monkeypatch):
    from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
    from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="located_veto", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    unsafe, safe = "我替你答应了交出私人钥匙。", "（收回手）决定由你自己来做。"
    counts = {"actor": 0, "review": 0}
    requests = []

    async def options():
        return {"evaluator": True, "review": True, "dispute": False}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        counts["actor"] += 1
        return {"performance": unsafe if counts["actor"] == 1 else safe,
                "scene_narration": "窗外的灯仍亮着。", "suggested_inputs": [], "transition_offered": False}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages):
            if json.loads(messages[1].content[messages[1].content.index("{"):]).get("review_task") == "player_actions":
                return SimpleNamespace(content='{"body_violations":[],"failure_reason":""}')
            counts["review"] += 1
            assert (unsafe if counts["review"] == 1 else safe) in messages[1].content
            return SimpleNamespace(content=_payload(
                [_issue(quote=unsafe)] if counts["review"] == 1 else [],
                failure_reason="正文替玩家作出了决定。" if counts["review"] == 1 else "",
            ))

    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    async def factory(*args, **kwargs):
        requests.append(kwargs)
        return Client()

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(evaluator.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(evaluator, "_model_config", config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("located_veto_turn", 0, "我先考虑一下。"), ensure_current_binding=lambda _: _binding(),
    )
    assert counts == {"actor": 2, "review": 2}
    # Workflow 原有漏判补查和账本同次审批分别预留 96/90 tokens；本次解析修复不改额度。
    assert all(request["timeout"] == evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS
               and request["max_retries"] == 0 and request["max_completion_tokens"] in {190, 512 + 96 + 90}
               for request in requests), requests
    assert result.performance["performance"] == safe
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert unsafe not in json.dumps(result.stored.session.performance_history, ensure_ascii=False)
    assert await runtime.restore_session("located_veto") == result.stored
    fork = await runtime.fork_session_for_test("located_veto", session_id="located_veto_fork",
                                               through_revision=result.stored.session.revision)
    assert fork.session.story_state == result.stored.session.story_state
