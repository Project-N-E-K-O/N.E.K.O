"""Unit tests for ``scripts/check_llm_budget.py``.

Synthetic-source coverage of the two rules:

  * LLM_OUTPUT_BUDGET — construction sites must carry a token budget AND a
    timeout; per-call / **kwargs / noqa escape hatches.
  * LLM_INPUT_BUDGET — dynamic LLM calls must be input-budget-aware;
    constant-only prompts and budget-aware functions are clean; noqa.

The CLI path is smoke-tested by running the script against its own repo
(the working tree is kept green by CI, so exit 0 is expected).
"""
from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_llm_budget.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("check_llm_budget", SCRIPT_PATH)
    assert spec and spec.loader, f"failed to load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _load_script_module()


def _codes(source: str) -> list[str]:
    source = textwrap.dedent(source)
    tree = ast.parse(source)
    checker = MOD.LLMBudgetChecker(Path("synthetic.py"), source.splitlines(), tree)
    checker.visit(tree)
    return [code for (_ln, _col, code, _msg) in checker.violations]


# ---------------------------------------------------------------------------
# LLM_OUTPUT_BUDGET (construction site)
# ---------------------------------------------------------------------------


def test_output_missing_both_flagged():
    src = "llm = create_chat_llm(model, base_url, api_key)"
    assert _codes(src).count("LLM_OUTPUT_BUDGET") == 1


def test_output_budget_and_timeout_clean():
    src = "llm = create_chat_llm(m, b, k, max_completion_tokens=100, timeout=30)"
    assert "LLM_OUTPUT_BUDGET" not in _codes(src)


def test_output_missing_timeout_flagged():
    src = "llm = create_chat_llm(m, b, k, max_completion_tokens=100)"
    assert _codes(src).count("LLM_OUTPUT_BUDGET") == 1


def test_output_missing_budget_flagged():
    src = "llm = create_chat_llm(m, b, k, timeout=30)"
    assert _codes(src).count("LLM_OUTPUT_BUDGET") == 1


def test_output_max_tokens_satisfies_budget():
    src = "llm = ChatOpenAI(m, max_tokens=100, timeout=30)"
    assert "LLM_OUTPUT_BUDGET" not in _codes(src)


def test_output_alias_class_matched():
    # ``_ChatOpenAI`` / ``BUChatOpenAI`` — any name ending in ChatOpenAI.
    src = "c = _ChatOpenAI(model=m)"
    assert _codes(src).count("LLM_OUTPUT_BUDGET") == 1


def test_output_star_kwargs_not_an_exemption():
    # A splat hides its keys from the checker, so it must NOT silently pass —
    # the hard rule requires an explicit kwarg or a justified noqa.
    src = "llm = create_chat_llm(m, b, k, **opts)"
    assert _codes(src).count("LLM_OUTPUT_BUDGET") == 1


def test_output_star_kwargs_with_noqa_suppressed():
    src = "llm = create_chat_llm(m, b, k, **opts)  # noqa: LLM_OUTPUT_BUDGET"
    assert "LLM_OUTPUT_BUDGET" not in _codes(src)


def test_output_none_value_treated_as_missing():
    # A literal None defeats the budget/timeout (client omits a falsy limit /
    # leaves SDK timeout unset), so it must NOT satisfy the rule.
    src = "llm = create_chat_llm(m, b, k, max_completion_tokens=None, timeout=None)"
    assert _codes(src).count("LLM_OUTPUT_BUDGET") == 1


def test_output_none_timeout_with_real_budget_still_flagged():
    src = "llm = create_chat_llm(m, b, k, max_completion_tokens=100, timeout=None)"
    assert _codes(src).count("LLM_OUTPUT_BUDGET") == 1


def test_output_noqa_suppresses():
    src = "llm = create_chat_llm(m, b, k)  # noqa: LLM_OUTPUT_BUDGET"
    assert "LLM_OUTPUT_BUDGET" not in _codes(src)


def test_output_noqa_with_trailing_rationale_suppresses():
    src = "llm = create_chat_llm(m, b, k)  # noqa: LLM_OUTPUT_BUDGET  # per-call budget"
    assert "LLM_OUTPUT_BUDGET" not in _codes(src)


# ---------------------------------------------------------------------------
# LLM_INPUT_BUDGET (call site, heuristic)
# ---------------------------------------------------------------------------


def test_input_dynamic_without_budget_flagged():
    src = """
    async def f(prompt):
        resp = await llm.ainvoke(prompt)
    """
    assert _codes(src).count("LLM_INPUT_BUDGET") == 1


def test_input_budget_aware_function_clean():
    src = """
    async def f(prompt):
        prompt = truncate_to_tokens(prompt, 100)
        resp = await llm.ainvoke(prompt)
    """
    assert "LLM_INPUT_BUDGET" not in _codes(src)


def test_input_max_tokens_constant_makes_function_aware():
    src = """
    async def f(prompt):
        budget = RECALL_PER_CANDIDATE_MAX_TOKENS
        resp = await llm.ainvoke(prompt)
    """
    assert "LLM_INPUT_BUDGET" not in _codes(src)


def test_input_max_chars_constant_is_not_budget_aware():
    src = """
    async def f(prompt):
        budget = RECALL_PER_CANDIDATE_MAX_CHARS
        resp = await llm.ainvoke(prompt)
    """
    assert _codes(src).count("LLM_INPUT_BUDGET") == 1


def test_input_token_budget_suffix_is_not_budget_aware():
    src = """
    async def f(prompt):
        budget = RECALL_PER_CANDIDATE_TOKEN_BUDGET
        resp = await llm.ainvoke(prompt)
    """
    assert _codes(src).count("LLM_INPUT_BUDGET") == 1


def test_input_count_tokens_alone_not_budget_aware():
    # count_tokens only *measures* — it doesn't cap, so it must not satisfy the
    # input-budget rule for a dynamic call.
    src = """
    async def f(prompt):
        n = count_tokens(prompt)
        resp = await llm.ainvoke(prompt)
    """
    assert _codes(src).count("LLM_INPUT_BUDGET") == 1


def test_input_constant_prompt_skipped():
    src = """
    async def f():
        resp = await llm.ainvoke([{"role": "user", "content": "hi"}])
    """
    assert "LLM_INPUT_BUDGET" not in _codes(src)


def test_input_noqa_suppresses():
    src = """
    async def f(prompt):
        resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET
    """
    assert "LLM_INPUT_BUDGET" not in _codes(src)


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_repo_is_clean():
    """The working tree must satisfy the lint (CI gate)."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,  # never let a misbehaving scan hang the test suite
    )
    assert result.returncode == 0, (
        f"check_llm_budget.py reported violations:\n{result.stdout}\n{result.stderr}"
    )
