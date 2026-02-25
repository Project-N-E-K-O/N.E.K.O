"""
Manual integration test for task routing (DirectTaskExecutor.analyze_and_execute).

Tests that the routing logic correctly dispatches tasks to the right execution method:
  - "帮我打开系统计算器" → computer_use
  - "帮我搜索一下今天的新闻" → browser_use
  - "在百度地图上找到当前位置" → ambiguous (browser_use or computer_use)
  - "你好，吃了吗？" → None (casual chat, no task)

Requires:
  - Agent API key configured (via backup/config/core_config.json)
  - Real LLM calls (consumes tokens)
  - Does NOT actually control mouse/keyboard or open a browser

Run with:
  uv run --with pytest python -m pytest tests/test_routing_manual.py -v -s --run-manual
"""

import asyncio

import pytest



@pytest.fixture(scope="module")
def task_executor():
    """Create a real DirectTaskExecutor with real adapters."""
    from brain.computer_use import ComputerUseAdapter
    from brain.browser_use_adapter import BrowserUseAdapter
    from brain.task_executor import DirectTaskExecutor

    cu = ComputerUseAdapter()
    bu = BrowserUseAdapter()
    executor = DirectTaskExecutor(computer_use=cu, browser_use=bu)
    return executor


def _make_messages(user_text: str):
    """Create a minimal conversation for routing."""
    return [{"role": "user", "text": user_text}]


ALL_ENABLED = {
    "computer_use_enabled": True,
    "browser_use_enabled": True,
    "user_plugin_enabled": False,
}


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


@pytest.mark.manual
def test_route_open_calculator(task_executor):
    """'帮我打开系统计算器' should route to computer_use.

    NOTE: browser_use has higher priority in the routing chain, so if the
    browser_use assessor also claims it can handle this, it will win.
    We accept either but warn if it's not computer_use.
    """
    messages = _make_messages("帮我打开系统计算器")

    print("\n[Routing] Testing: '帮我打开系统计算器'")
    print("[Routing] Ideal: computer_use (but browser_use has higher priority)")

    result = _run(
        task_executor.analyze_and_execute(
            messages=messages,
            lanlan_name="Test",
            agent_flags=ALL_ENABLED,
        )
    )

    print(f"[Routing] Result: {result}")
    assert result is not None, "Router returned None — no execution method matched"
    print(f"[Routing] execution_method = {result.execution_method}")
    print(f"[Routing] task_description = {result.task_description}")
    print(f"[Routing] reason = {result.reason}")
    assert result.execution_method in ("computer_use", "browser_use"), \
        f"Unexpected method: {result.execution_method}"
    if result.execution_method != "computer_use":
        print(f"[Routing] WARNING: Ideally computer_use, but got {result.execution_method} "
              f"(browser_use has higher routing priority)")


@pytest.mark.manual
def test_route_search_news(task_executor):
    """'帮我搜索一下今天的新闻' should route to browser_use."""
    messages = _make_messages("帮我搜索一下今天的新闻")

    print("\n[Routing] Testing: '帮我搜索一下今天的新闻'")
    print("[Routing] Expected: browser_use")

    result = _run(
        task_executor.analyze_and_execute(
            messages=messages,
            lanlan_name="Test",
            agent_flags=ALL_ENABLED,
        )
    )

    print(f"[Routing] Result: {result}")
    assert result is not None, "Router returned None — no execution method matched"
    print(f"[Routing] execution_method = {result.execution_method}")
    print(f"[Routing] task_description = {result.task_description}")
    print(f"[Routing] reason = {result.reason}")
    assert result.execution_method == "browser_use", \
        f"Expected browser_use, got {result.execution_method}"


@pytest.mark.manual
def test_route_find_location_on_map(task_executor):
    """
    '在百度地图上找到当前位置' — ambiguous task.
    Could be browser_use (open Baidu Maps website) or computer_use (open maps app).
    This test just verifies routing produces a decision and logs it.
    """
    messages = _make_messages("在百度地图上找到当前位置")

    print("\n[Routing] Testing: '在百度地图上找到当前位置'")
    print("[Routing] Expected: browser_use or computer_use (ambiguous)")

    result = _run(
        task_executor.analyze_and_execute(
            messages=messages,
            lanlan_name="Test",
            agent_flags=ALL_ENABLED,
        )
    )

    print(f"[Routing] Result: {result}")
    if result is None:
        print("[Routing] Router returned None — task may not be recognized as actionable")
        pytest.fail("Expected a routing decision, got None")

    print(f"[Routing] execution_method = {result.execution_method}")
    print(f"[Routing] task_description = {result.task_description}")
    print(f"[Routing] reason = {result.reason}")
    assert result.execution_method in ("computer_use", "browser_use"), \
        f"Unexpected method: {result.execution_method}"


@pytest.mark.manual
def test_route_casual_chat_no_task(task_executor):
    """Casual chat should NOT trigger any task."""
    messages = _make_messages("你好，吃了吗？")

    print("\n[Routing] Testing: '你好，吃了吗？'")
    print("[Routing] Expected: None (no task)")

    result = _run(
        task_executor.analyze_and_execute(
            messages=messages,
            lanlan_name="Test",
            agent_flags=ALL_ENABLED,
        )
    )

    print(f"[Routing] Result: {result}")
    if result is not None:
        print(f"[Routing] WARNING: Router produced a task for casual chat!")
        print(f"[Routing] execution_method = {result.execution_method}")
        print(f"[Routing] task_description = {result.task_description}")
        # Not a hard fail — LLM may occasionally misjudge, but log it clearly
        if result.has_task:
            pytest.fail(
                f"Casual chat triggered a task: method={result.execution_method}, "
                f"desc={result.task_description}"
            )
    else:
        print("[Routing] Correct: no task produced for casual chat")
