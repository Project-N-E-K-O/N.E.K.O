# -*- coding: utf-8 -*-
"""Reflection synthesis 后端循环 - regression test.

Pin 两条不变量：

1. ``_periodic_reflection_synthesis_loop`` 存在，且按 ``MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS``
   每轮对所有 catgirl 调一次 ``reflection_engine.synthesize_reflections(name)``。
   单角色失败不阻塞其余 + 不让循环退出。

2. ``main_routers/system_router.py`` 的 ``proactive_chat`` handler 不再
   ``POST /reflect/{name}``。这条 mutation 之前挂在 frontend-driven 关键路径
   上（PR #1015 顺手塞的），会让"前端关 / proactive 不触发 → reflection 永
   不增长"。现在合成只在后端循环里走，**任何**把 ``/reflect`` POST 加回
   ``proactive_chat`` handler 的 refactor 都会被这里抓到——前端解耦是设计意图。

测不到的部分（留 manual / e2e）：
- 实际 LLM 是否生成 valid reflection（``synthesize_reflections`` 自身已有
  ``tests/unit/test_reflection_*`` 覆盖）
- frontend ``/api/proactive_chat`` setTimeout 是否触发（本来就是 frontend issue，
  不在本 PR scope；本 PR 的 fix 是"即使 proactive 永远不触发，reflection 也
  会持续生长"）
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_synthesis_loop_calls_synthesize_for_each_character():
    """每轮循环对所有 catgirl 各调一次 synthesize_reflections。

    用 patched asyncio.sleep 做开关：第二次 sleep（loop tail）被 raise CancelledError
    打断，让 loop 只跑一轮就退出。第一次 sleep（_INITIAL_DELAY_REFLECTION_SYNTHESIS）
    立即返回。
    """
    from app import memory_server

    fake_engine = MagicMock()
    fake_engine.synthesize_reflections = AsyncMock(return_value=[])

    fake_cm = MagicMock()
    fake_cm.aload_characters = AsyncMock(return_value={
        '猫娘': {'悠怡': {}, '喵酱': {}, '小八': {}}
    })

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # 首次 (initial delay) + tail sleep 都让出，第二次 tail sleep 砍掉 loop
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    with patch.object(memory_server, "reflection_engine", fake_engine), \
         patch.object(memory_server, "_config_manager", fake_cm), \
         patch("app.memory_server.asyncio.sleep", new=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await memory_server._periodic_reflection_synthesis_loop()

    # 每个 catgirl 各被调一次
    assert fake_engine.synthesize_reflections.await_count == 3
    called_names = {call.args[0] for call in fake_engine.synthesize_reflections.await_args_list}
    assert called_names == {'悠怡', '喵酱', '小八'}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_synthesis_loop_single_character_failure_does_not_abort_others():
    """悠怡 synthesize 抛异常，喵酱 / 小八 仍然各被调一次。"""
    from app import memory_server

    fake_engine = MagicMock()

    async def _synth(name):
        if name == '悠怡':
            raise RuntimeError("simulated LLM crash")
        return []

    fake_engine.synthesize_reflections = AsyncMock(side_effect=_synth)

    fake_cm = MagicMock()
    fake_cm.aload_characters = AsyncMock(return_value={
        '猫娘': {'悠怡': {}, '喵酱': {}, '小八': {}}
    })

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    with patch.object(memory_server, "reflection_engine", fake_engine), \
         patch.object(memory_server, "_config_manager", fake_cm), \
         patch("app.memory_server.asyncio.sleep", new=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await memory_server._periodic_reflection_synthesis_loop()

    assert fake_engine.synthesize_reflections.await_count == 3
    called_names = [call.args[0] for call in fake_engine.synthesize_reflections.await_args_list]
    assert '悠怡' in called_names
    assert '喵酱' in called_names
    assert '小八' in called_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_synthesis_loop_load_characters_failure_skips_round_does_not_abort():
    """aload_characters 抛异常时本轮跳过、loop 不挂；下一轮 sleep 后再试。"""
    from app import memory_server

    fake_engine = MagicMock()
    fake_engine.synthesize_reflections = AsyncMock(return_value=[])

    call_count = {'n': 0}

    async def _fail_then_succeed():
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise RuntimeError("simulated disk read fail")
        return {'猫娘': {'悠怡': {}}}

    fake_cm = MagicMock()
    fake_cm.aload_characters = AsyncMock(side_effect=_fail_then_succeed)

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # 让 loop 跑两轮（initial delay + 第一轮失败后 sleep + 第二轮成功后 sleep）
        if len(sleep_calls) >= 3:
            raise asyncio.CancelledError

    with patch.object(memory_server, "reflection_engine", fake_engine), \
         patch.object(memory_server, "_config_manager", fake_cm), \
         patch("app.memory_server.asyncio.sleep", new=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await memory_server._periodic_reflection_synthesis_loop()

    # 第一轮 aload_characters 失败 → 0 次 synthesize
    # 第二轮成功 → 悠怡 被调 1 次
    assert fake_engine.synthesize_reflections.await_count == 1
    assert fake_engine.synthesize_reflections.await_args.args[0] == '悠怡'


@pytest.mark.unit
def test_reflection_synthesis_loop_registered_in_background_tasks():
    """``ensure_memory_server_runtime_initialized`` 必须把
    ``_periodic_reflection_synthesis_loop`` 注册到 ``_spawn_background_task``。

    用源码扫描而非 runtime instrumentation：这里要钉的是"loop 被挂上去"这件事
    本身（regression：删除注册行是单字符级别的、容易疏漏的退化），不需要也不
    应该跑完整 server startup。
    """
    import inspect
    from app import memory_server

    src = inspect.getsource(memory_server.ensure_memory_server_runtime_initialized)
    assert "_periodic_reflection_synthesis_loop()" in src, (
        "ensure_memory_server_runtime_initialized 必须 _spawn_background_task("
        "_periodic_reflection_synthesis_loop()) —— 没有它，pending reflection 增长会停摆"
    )


@pytest.mark.unit
def test_proactive_chat_handler_no_longer_posts_reflect():
    """``main_routers/system_router.proactive_chat`` 必须不再 POST /reflect。

    在 PR #1015 之后，reflection 合成 + auto_transitions 都迁到了 memory_server
    自己的两条后端循环（synthesis loop + auto_promote loop），proactive_chat
    关键路径上保留这条 mutation 只会拖响应时间 + 让 reflection 生命周期与
    frontend setTimeout 强耦合。任何把这行加回去的 refactor 必须先解释为
    什么 backend loop 不够。
    """
    import inspect
    import re
    import main_routers.system_router as system_router

    src = inspect.getsource(system_router.proactive_chat)
    # 精确匹配 "POST 到 /reflect 端点" 这件事——而不是单纯出现 "/reflect/" 字面，
    # 因为说明 backend 已经迁走的注释里允许有路径名（历史 trace）。
    # 任何能形成 HTTP POST 到 /reflect 的代码会包含 .post(...reflect...)。
    post_to_reflect = re.search(
        r"""\.post\s*\(\s*[^)]*?["']\S*?/reflect/""",
        src,
    )
    assert post_to_reflect is None, (
        "proactive_chat handler 里不能再 POST /reflect/{name} —— 合成已迁到 "
        "_periodic_reflection_synthesis_loop 后端循环；这条 mutation 放在 "
        "proactive 关键路径上会拖延 ~15s + 让 reflection 增长依赖前端。"
        f"匹配到: {post_to_reflect.group(0) if post_to_reflect else ''!r}"
    )
