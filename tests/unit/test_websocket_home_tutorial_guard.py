from pathlib import Path


WEBSOCKET_ROUTER_PATH = Path(__file__).resolve().parents[2] / "main_routers" / "websocket_router.py"


def _read_router() -> str:
    return WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")


def test_home_tutorial_greeting_guard_is_removed_from_backend():
    source = _read_router()

    assert "_home_tutorial_blocking_greeting" not in source
    assert "_is_home_tutorial_blocking_greeting" not in source
    assert "home_tutorial_state" not in source
    assert "blocking_greeting" not in source
    assert "skipped by home tutorial guard" not in source


def test_backend_greeting_check_no_longer_depends_on_tutorial_state():
    source = _read_router()
    greeting_block = source.split('elif action == "greeting_check":', 1)[1].split(
        'elif action == "cat_greeting_check":',
        1,
    )[0]

    # greeting_check 必须发布 agent-intent restore 信号（首个「用户实际进入会话」
    # 信号）。#2289 起该调用加了 new_session=budget_new_session kwarg，故只断言
    # 「以 lanlan_name 为首参调用了该函数」，不锁死尾随参数列表。
    assert "_publish_agent_intent_restore_signal(lanlan_name" in greeting_block
    assert "_is_home_tutorial_blocking_greeting" not in greeting_block
    assert "is_switch = message.get(\"is_switch\", False)" in greeting_block
    assert "greeting_reason = str(message.get(\"reason\")" in greeting_block


def test_backend_cat_greeting_check_no_longer_depends_on_tutorial_state():
    source = _read_router()
    cat_block = source.split('elif action == "cat_greeting_check":', 1)[1].split(
        'elif action == "submit_tool_result":',
        1,
    )[0]

    assert "_is_home_tutorial_blocking_greeting" not in cat_block
    assert "cat_duration = float(message.get(\"cat_duration_seconds\"" in cat_block
