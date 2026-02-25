"""
Manual integration test for Computer Use (CUA).

Requires:
  - Agent API key configured in backup/config/core_config.json
  - pyautogui installed
  - A display (will actually control mouse/keyboard)

Run with:
  uv run --with pytest python -m pytest tests/test_computer_use_manual.py -v -s --run-manual
"""

import json
import os
import pytest


def _load_config():
    cfg_path = os.path.join(os.path.dirname(__file__), "..", "backup", "config", "core_config.json")
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def config():
    return _load_config()


@pytest.fixture(scope="module")
def computer_use_adapter():
    from brain.computer_use import ComputerUseAdapter
    adapter = ComputerUseAdapter()
    return adapter


@pytest.mark.manual
def test_computer_use_is_available(computer_use_adapter):
    """Check that ComputerUseAdapter initializes and reports availability."""
    status = computer_use_adapter.is_available()
    print(f"\n[CUA availability] {status}")
    assert status.get("ready") is True, f"ComputerUseAdapter not ready: {status.get('reasons')}"


@pytest.mark.manual
def test_computer_use_open_calculator(computer_use_adapter):
    """
    Actually opens the system calculator via CUA.

    WARNING: This will take control of your mouse and keyboard!
    Do not touch the computer while this test is running.
    """
    import time

    status = computer_use_adapter.is_available()
    if not status.get("ready"):
        pytest.skip(f"ComputerUseAdapter not ready: {status.get('reasons')}")

    print("\n[CUA] Starting task: open system calculator")
    print("[CUA] DO NOT touch mouse/keyboard during execution!")
    time.sleep(2)

    result = computer_use_adapter.run_instruction("打开Windows系统计算器")

    print(f"\n[CUA] Result: {result}")
    assert result is None or isinstance(result, dict), f"Unexpected result type: {type(result)}"
    if isinstance(result, dict):
        if "error" in result:
            print(f"[CUA] Error: {result['error']}")
        else:
            print("[CUA] Task completed successfully")
