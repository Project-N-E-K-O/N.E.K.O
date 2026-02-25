"""
Manual integration test for Browser Use.

Requires:
  - Agent API key configured (via backup/config/core_config.json)
  - browser-use and playwright installed
  - Will open a real Chromium browser

Run with:
  uv run --with pytest python -m pytest tests/test_browser_use_manual.py -v -s --run-manual
"""

import asyncio
import time

import pytest



@pytest.fixture(scope="module")
def browser_use_adapter():
    from brain.browser_use_adapter import BrowserUseAdapter
    adapter = BrowserUseAdapter()
    yield adapter
    asyncio.run(adapter.close())


@pytest.mark.manual
def test_browser_use_is_available(browser_use_adapter):
    """Check that BrowserUseAdapter initializes and reports availability."""
    status = browser_use_adapter.is_available()
    print(f"\n[BrowserUse availability] {status}")
    assert status.get("ready") is True, f"BrowserUseAdapter not ready: {status.get('reasons')}"


@pytest.mark.manual
def test_browser_use_search_news(browser_use_adapter):
    """
    Actually opens a browser and searches for today's news.

    WARNING: This will open a Chromium browser window!
    """
    status = browser_use_adapter.is_available()
    if not status.get("ready"):
        pytest.skip(f"BrowserUseAdapter not ready: {status.get('reasons')}")

    print("\n[BrowserUse] Starting task: search today's news")
    print("[BrowserUse] A browser window will open...")
    time.sleep(1)

    result = asyncio.run(
        browser_use_adapter.run_instruction(
            "打开百度，搜索'今天的新闻'，告诉我第一条结果的标题"
        )
    )

    print(f"\n[BrowserUse] Result: {result}")
    assert isinstance(result, dict), f"Unexpected result type: {type(result)}"
    if result.get("success"):
        print(f"[BrowserUse] Task completed: {result.get('result', '')[:200]}")
    else:
        print(f"[BrowserUse] Task failed: {result.get('error', 'unknown')}")
