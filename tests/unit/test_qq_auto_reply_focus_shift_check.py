"""`_run_focus_shift_check` 在 ignore 分支也要推进焦点切换。

一条被 gate ignore 的非焦点消息 boost 后可能让该群变成焦点；若不在这里
check_focus_shift，_last_focus_group 不更新、回溯补回不触发、切换点消息
留在 backlog。本测试验证该方法在有切换时触发回溯、无切换时静默。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from plugin.plugins.qq_auto_reply.message_dispatcher import QQMessageDispatcher


class _Gate:
    def __init__(self, shift=None):
        self._shift = shift
        self.checked = 0
        self._retro_tasks = set()
        self._never = asyncio.Event()  # 让回溯任务挂起，避免完成回调立即 discard

    async def check_focus_shift(self):
        self.checked += 1
        return self._shift

    async def run_retroactive_review(self, group_id):
        await self._never.wait()
        return []


def _plugin(gate):
    return SimpleNamespace(
        attention_gate_service=gate,
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        runtime_service=SimpleNamespace(record_pipeline_outcome=lambda *a, **k: None),
    )


def test_focus_shift_triggers_retroactive_review():
    """有焦点切换时创建回溯补回任务。"""
    gate = _Gate(shift=SimpleNamespace(new_focus_group="g2"))
    dispatcher = QQMessageDispatcher(_plugin(gate))

    async def run_and_assert():
        await dispatcher._run_focus_shift_check()
        # 在 event loop 内断言：create_task 已执行、retro_tasks.add 已同步完成
        assert gate.checked == 1
        assert len(gate._retro_tasks) == 1

    asyncio.run(run_and_assert())


def test_no_focus_shift_is_noop():
    """无焦点切换时静默返回，不创建任务。"""
    gate = _Gate(shift=None)
    dispatcher = QQMessageDispatcher(_plugin(gate))

    async def run_and_assert():
        await dispatcher._run_focus_shift_check()
        assert gate.checked == 1
        assert len(gate._retro_tasks) == 0

    asyncio.run(run_and_assert())


def test_missing_gate_is_noop():
    """插件无 attention_gate_service 时安全返回。"""
    dispatcher = QQMessageDispatcher(_plugin(gate=None))
    # 不应抛异常
    asyncio.run(dispatcher._run_focus_shift_check())
