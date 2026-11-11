"""Mandatory review-template edits fail closed; optional edits may be absent."""

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


def _frame():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    return engine, engine.create_session(session_id="template-guard", catgirl_binding=_binding(), opening_performance=_opening())


@pytest.mark.parametrize("fragment", [
    "正文邀请、事实边界和第一按钮",
    "合法邀请不因首按钮拒绝或暂缓而变成正文违规。"
    "按钮尚未选择，不是本轮执行证据；逐项检查实际前提、公开去向与作者禁令，不能补造未知工具或成功。",
])
def test_invitation_proposal_required_edit_rejects_template_drift(monkeypatch, fragment):
    engine, session = _frame()
    replace = ev._replace_review_template
    checked = []

    def drift(text, original, replacement):
        if original == fragment:
            checked.append(original)
            # Simulate a changed upstream template at this mandatory edit boundary.
            text = text.replace(original, "模板片段已改变。", 1)
        return replace(text, original, replacement)

    monkeypatch.setattr(ev, "_replace_review_template", drift)
    with pytest.raises(ev.NumericV2EvaluatorError, match="numeric_v2_review_template_invalid"):
        ev._build_transition_judge_messages(engine, session, player_input="",
            actor_performance={"performance": "一起去门边吗？", "suggested_inputs": []},
            completion_fact_review_only=True, invitation_proposal_only=True, fixed_candidates=[])
    assert checked == [fragment]


def test_optional_missed_initiation_edit_remains_absent_without_failure():
    engine, session = _frame()
    messages, _ = ev._build_transition_judge_messages(engine, session, player_input="请看桌上的材料。",
        actor_performance={"scene_narration": "材料已经展开。", "performance": "可以看看了。", "suggested_inputs": ["查看材料。"]},
        check_missed_initiation=False,
        fixed_candidates=[{"id": "note", "condition": "材料已展开。", "after": []}])
    assert "本轮先检查待审正文有没有代写程序将展示的固定原文" in messages[0].content
    assert "missed_initiation_check" not in messages[0].content


@pytest.mark.parametrize("copies", [0, 2])
def test_tail_ownership_required_edit_rejects_template_drift(monkeypatch, copies):
    monkeypatch.setattr(actor, "NUMERIC_V2_ACTOR_EVENT_OWNERSHIP_INSTRUCTION", "只在 performance 中" * copies)
    with pytest.raises(actor.NumericV2ActorOutputError, match="numeric_v2_actor_prompt_template_invalid"):
        actor._system_prompt(catgirl_name="小岚", phase="transition_compact", transition_tail_only=True)
