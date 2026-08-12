from __future__ import annotations

import pytest

from plugin.plugins.study_companion._solution_narration import (
    extract_solution_narration_sections,
)
from plugin.plugins.study_companion._solution_structure import parse_solution_structure
from plugin.plugins.study_companion.entry_tutor_explain_entries import (
    _image_only_explain_prompt,
)
from plugin.plugins.study_companion.llm_prompts import build_concept_explain_messages


def _user_prompt(response_mode: str, *, language: str = "en") -> str:
    messages = build_concept_explain_messages(
        text="A supplied study request.",
        language=language,
        context={"study_response_mode": response_mode},
    )
    return messages[-1]["content"]


def test_problem_solving_prompt_requires_one_fixed_four_section_contract() -> None:
    prompt = _user_prompt("problem_solving")
    ordered_headings = (
        "Problem Analysis",
        "Solution Process",
        "Answer",
        "Transfer Practice",
    )

    assert "exactly four sections" in prompt
    assert "each heading appearing exactly once" in prompt
    assert "fixed order" in prompt
    first_contract = prompt.index("fixed order")
    positions = [prompt.index(heading, first_contract) for heading in ordered_headings]
    assert positions == sorted(positions)
    assert "Do not write a preface" in prompt
    assert "a fifth heading" in prompt
    assert "trailing note after Transfer Practice" in prompt
    assert "Do not expose drafts" in prompt
    assert "abandoned attempts" in prompt
    assert "internal dialogue" in prompt


def test_problem_solving_prompt_preserves_verifiable_work() -> None:
    prompt = _user_prompt("problem_solving")

    assert "formulas or theorem basis" in prompt
    assert "key substitutions and transformations" in prompt
    assert "applicable units" in prompt
    assert "boundary checks" in prompt
    assert "verification" in prompt
    assert "numbered body content inside Solution Process" in prompt
    assert "must not introduce another heading" in prompt


@pytest.mark.parametrize(
    ("language", "required_fragments"),
    [
        (
            "en",
            (
                "identify the problem",
                "State the givens, target, and applicable rules",
                "formulas or theorems",
                "key substitutions",
                "check units, boundaries",
                "verified formal derivation",
                "Problem Analysis",
                "Solution Process",
                "Answer",
                "Transfer Practice",
                "numbered body text",
                "Reserve output budget",
                "draft-like exploration",
                "verify each item independently",
                "all correct options",
            ),
        ),
        (
            "zh-CN",
            (
                "识别图片中的题目",
                "列出已知条件",
                "公式或定理的依据",
                "关键代入",
                "检查单位",
                "必要验算",
                "核验后的正式推导",
                "题目解析",
                "解题过程",
                "答案",
                "举一反三",
                "编号正文",
                "预留输出预算",
                "草稿式探索",
                "逐项验证",
                "多个正确选项",
            ),
        ),
        (
            "zh-TW",
            (
                "識別圖片中的題目",
                "列出已知條件",
                "公式或定理的依據",
                "關鍵代入",
                "檢查單位",
                "必要驗算",
                "核驗後的正式推導",
                "題目解析",
                "解題過程",
                "答案",
                "舉一反三",
                "編號正文",
                "預留輸出預算",
                "草稿式探索",
                "逐項驗證",
                "多個正確選項",
            ),
        ),
    ],
    ids=["english", "simplified-chinese", "traditional-chinese"],
)
def test_image_only_prompts_express_equivalent_solution_requirements(
    language: str,
    required_fragments: tuple[str, ...],
) -> None:
    prompt = _image_only_explain_prompt(language)

    assert all(fragment in prompt for fragment in required_fragments)


@pytest.mark.parametrize("response_mode", ["general_explanation", "general_discussion"])
def test_general_response_modes_are_not_polluted_by_solution_contract(
    response_mode: str,
) -> None:
    prompt = _user_prompt(response_mode)

    assert f"Response mode: {response_mode}" in prompt
    assert "Do not use solution headings" in prompt
    for heading in (
        "Problem Analysis",
        "Solution Process",
        "Transfer Practice",
        "题目解析",
        "解题过程",
        "举一反三",
    ):
        assert heading not in prompt
    assert "exactly four sections" not in prompt
    assert "fixed order" not in prompt


def test_complex_solution_is_complete_and_process_detail_is_not_narrated() -> None:
    process_sentinel = "PROCESS_FORMULA_SENTINEL"
    reply = (
        "### Problem Analysis\n"
        "Use conservation of energy and retain SI units.\n\n"
        "### Solution Process\n"
        "1. By conservation of energy, mgh = 1/2 mv².\n"
        f"2. {process_sentinel}: v = sqrt(2gh) = sqrt(2×9.8×5) m/s.\n"
        "3. The dimensions reduce to m/s; substituting back verifies both the "
        "boundary h = 0 and the stated result.\n\n"
        "### Answer\n"
        "v = 9.9 m/s (to two significant figures).\n\n"
        "### Transfer Practice\n"
        "Repeat the calculation for h = 20 m."
    )

    structure = parse_solution_structure(reply)
    narration = extract_solution_narration_sections(reply)

    assert structure.complete is True
    assert structure.missing_sections == ()
    assert "mgh = 1/2 mv²" in structure.process
    assert process_sentinel in structure.process
    assert narration == {
        "analysis": "Use conservation of energy and retain SI units.",
        "answer": "v = 9.9 m/s (to two significant figures).",
        "transfer": "Repeat the calculation for h = 20 m.",
    }
    assert process_sentinel not in repr(narration)
