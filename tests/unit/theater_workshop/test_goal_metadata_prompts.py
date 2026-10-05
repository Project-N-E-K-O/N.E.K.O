"""Every authoring and assessment prompt describes current goal metadata semantics."""
import pytest

from theater_workshop.sdk.generation.numeric_v2 import (
    _MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT, _NODE_ENHANCEMENT_PROMPT,
    _BRANCH_ENDING_PROMPT, _BRANCH_PATH_PROMPT,
)
from theater_workshop.sdk.generation.quality import _ASSESSMENT_PROMPT, _NODE_OPTIMIZATION_PROMPT
from theater_workshop.sdk.generation.facts import FACT_REVIEW_PROMPT
from theater_workshop.sdk.generation.evidence import EVIDENCE_REVIEW_PROMPT
from theater_workshop.sdk.generation.plan_review import PLAN_REVIEW_PROMPT


@pytest.mark.parametrize("prompt", [
    _MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT, _NODE_ENHANCEMENT_PROMPT,
    _BRANCH_ENDING_PROMPT, _BRANCH_PATH_PROMPT, _ASSESSMENT_PROMPT,
    _NODE_OPTIMIZATION_PROMPT, FACT_REVIEW_PROMPT, EVIDENCE_REVIEW_PROMPT, PLAN_REVIEW_PROMPT,
], ids=["mainline", "continuation", "enhancement", "branch-ending", "branch-path",
        "literature", "optimization", "facts", "evidence", "plans"])
def test_actual_prompt_constants_do_not_promise_retired_goal_execution(prompt):
    assert prompt.count("目标元数据边界：") == 1
    assert "Evaluator 不逐项判定目标完成" in prompt
    assert "Runtime 不执行目标 evidence 或 state_effects" in prompt
    assert "exact 不锁存锚点，也不保证运行时逐字展示" in prompt
    assert "实际发声权限以节点 acting_contract.dialogue_policy 和 Session 为准" in prompt
    assert "原样展示旁白须由作者显式声明 fixed_narrations" in prompt
    assert "不自动将 exact anchors 转成固定旁白" in prompt
    assert "Evaluator 按 description 判断语义是否完成" not in prompt
    assert "由 Evaluator 按 description 判断是否完成" not in prompt
    assert "运行时正文与字面证据无法匹配" not in prompt
