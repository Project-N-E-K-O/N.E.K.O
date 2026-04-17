# -*- coding: utf-8 -*-
"""
P1.c integration tests: outbox wiring inside memory_server.

Verifies:
  - _spawn_outbox_extract_facts appends a pending op, runs handler, marks done
  - _replay_pending_outbox picks up unfinished ops and re-runs handler
  - Handler not executing (e.g. no registered handler) → op remains pending
"""
from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from utils.llm_client import AIMessage, HumanMessage


def _install_fresh_memory_state(tmpdir: str):
    """Replace memory_server's outbox / config_manager with fresh instances backed by tmpdir."""
    from memory.outbox import Outbox
    import memory_server

    mock_cm = MagicMock()
    mock_cm.memory_dir = tmpdir
    mock_cm.load_characters = MagicMock(
        return_value={"猫娘": {"小天": {}}, "当前猫娘": "小天"}
    )
    with patch("memory.outbox.get_config_manager", return_value=mock_cm):
        ob = Outbox()
    ob._config_manager = mock_cm

    memory_server.outbox = ob
    memory_server._config_manager = mock_cm
    return ob, mock_cm


@pytest.mark.asyncio
async def test_spawn_outbox_happy_path_marks_done(tmp_path):
    """Handler 成功完成 → outbox pending_ops 为空。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    import memory_server
    from memory.outbox import OP_EXTRACT_FACTS

    calls: list[tuple[str, dict]] = []

    async def _fake_handler(name: str, payload: dict):
        calls.append((name, payload))

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_EXTRACT_FACTS: _fake_handler},
        clear=False,
    ):
        msgs = [HumanMessage(content="喵"), AIMessage(content="mrrp")]
        task = await memory_server._spawn_outbox_extract_facts("小天", msgs)
        await task

    assert len(calls) == 1
    name, payload = calls[0]
    assert name == "小天"
    # payload serialized via messages_to_dict → round-trippable
    assert isinstance(payload.get("messages"), list)
    assert len(payload["messages"]) == 2

    # Outbox should show no pending ops after success
    pending = await ob.apending_ops("小天")
    assert pending == []


@pytest.mark.asyncio
async def test_handler_failure_keeps_op_pending(tmp_path):
    """Handler raises → op stays pending (next startup replays it)."""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    import memory_server
    from memory.outbox import OP_EXTRACT_FACTS

    async def _bad_handler(name: str, payload: dict):
        raise RuntimeError("simulated LLM crash mid-call")

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_EXTRACT_FACTS: _bad_handler},
        clear=False,
    ):
        task = await memory_server._spawn_outbox_extract_facts(
            "小天", [HumanMessage(content="hi")]
        )
        await task

    pending = await ob.apending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["type"] == OP_EXTRACT_FACTS


@pytest.mark.asyncio
async def test_replay_reinvokes_pending_handler(tmp_path):
    """模拟进程重启场景：上一跑 outbox 里有 pending，启动 replay 应重跑 handler。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    import memory_server
    from memory.outbox import OP_EXTRACT_FACTS
    from utils.llm_client import messages_to_dict

    # 场景：上一跑在 append_pending 后崩溃，没跑完 handler
    payload = {"messages": messages_to_dict([HumanMessage(content="反驳：不喜欢咖啡")])}
    op_id = await ob.aappend_pending("小天", OP_EXTRACT_FACTS, payload)

    replay_calls: list[tuple[str, dict]] = []

    async def _replay_handler(name: str, payload: dict):
        replay_calls.append((name, payload))

    with patch.dict(
        memory_server._OUTBOX_HANDLERS,
        {OP_EXTRACT_FACTS: _replay_handler},
        clear=False,
    ):
        await memory_server._replay_pending_outbox()
        # 等待 spawn 的任务跑完
        await asyncio.sleep(0)
        # 拿 background task 集合的强引用等全部完成
        pending_tasks = list(memory_server._BACKGROUND_TASKS)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    assert len(replay_calls) == 1
    assert replay_calls[0][0] == "小天"
    # done 应被写入 → pending_ops 空
    assert await ob.apending_ops("小天") == []


@pytest.mark.asyncio
async def test_replay_skips_unknown_op_type(tmp_path):
    """未注册的 op type 不应让 replay 崩溃，该 op 静默跳过但 append_done
    不会被调用 → 保持 pending，等升级后兼容 handler 补跑。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    import memory_server

    op_id = await ob.aappend_pending("小天", "future_op_type_v2", {"x": 1})

    # clear handlers to ensure this type isn't registered
    with patch.dict(memory_server._OUTBOX_HANDLERS, {}, clear=True):
        await memory_server._replay_pending_outbox()
        pending_tasks = list(memory_server._BACKGROUND_TASKS)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    # 仍 pending（handler 没跑、也没 append_done）
    pending = await ob.apending_ops("小天")
    assert len(pending) == 1
    assert pending[0]["op_id"] == op_id


@pytest.mark.asyncio
async def test_append_pending_failure_falls_back_to_in_memory(tmp_path):
    """Outbox 写失败 → 降级为传统内存任务；主流程不应崩溃。"""
    ob, _ = _install_fresh_memory_state(str(tmp_path))
    import memory_server

    # 强制 aappend_pending 抛异常
    async def _boom(*a, **kw):
        raise OSError("disk full")

    ob.aappend_pending = _boom  # type: ignore[assignment]

    # 同时 patch _extract_facts_and_check_feedback 成 noop，避免真 LLM 调用
    noop = AsyncMock(return_value=None)
    with patch("memory_server._extract_facts_and_check_feedback", noop):
        task = await memory_server._spawn_outbox_extract_facts(
            "小天", [HumanMessage(content="hi")]
        )
        await task

    # 降级路径：函数被调用过
    noop.assert_called_once()
    # 没有 pending 记录产生（写盘本来就失败了）
    assert not os.path.exists(ob._outbox_path("小天"))
