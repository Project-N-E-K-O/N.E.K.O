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

import ast
import asyncio
import inspect
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _missing_reason_code_action_dicts(fn):
    src = textwrap.dedent(inspect.getsource(fn))
    tree = ast.parse(src)
    missing: list[tuple[int, list[str]]] = []

    class Visitor(ast.NodeVisitor):
        def visit_Dict(self, node: ast.Dict) -> None:
            keys = [
                key.value if isinstance(key, ast.Constant) else None
                for key in node.keys
            ]
            if "action" in keys:
                action_idx = keys.index("action")
                action_value = node.values[action_idx]
                action_values: list[str] = []
                if isinstance(action_value, ast.Constant):
                    action_values.append(str(action_value.value))
                elif isinstance(action_value, ast.IfExp):
                    for branch in (action_value.body, action_value.orelse):
                        if isinstance(branch, ast.Constant):
                            action_values.append(str(branch.value))
                if (
                    any(value in {"pass", "chat"} for value in action_values)
                    and "reason_code" not in keys
                ):
                    missing.append((node.lineno, action_values))
            self.generic_visit(node)

    Visitor().visit(tree)
    return missing


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

    with patch.object(memory_server.runtime, "reflection_engine", fake_engine), \
         patch.object(memory_server.runtime, "_config_manager", fake_cm), \
         patch("app.memory_server.refine_loops.asyncio.sleep", new=fake_sleep):
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

    with patch.object(memory_server.runtime, "reflection_engine", fake_engine), \
         patch.object(memory_server.runtime, "_config_manager", fake_cm), \
         patch("app.memory_server.refine_loops.asyncio.sleep", new=fake_sleep):
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

    with patch.object(memory_server.runtime, "reflection_engine", fake_engine), \
         patch.object(memory_server.runtime, "_config_manager", fake_cm), \
         patch("app.memory_server.refine_loops.asyncio.sleep", new=fake_sleep):
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

    from main_logic.proactive_chat import service as proactive_service

    src = inspect.getsource(proactive_service.handle_proactive_chat)
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
        # 显式括号让 !r 应用范围一眼可见，省 reader 心算 conditional-expr +
        # conversion-spec 的 precedence（PEP 498 已经定义了行为，但 future
        # reader 一眼看不出来——CodeRabbit PR #1401 误报为 SyntaxError 就是
        # 因为这个直觉负担，加括号杜绝同类困惑）
        f"匹配到: {(post_to_reflect.group(0) if post_to_reflect else '')!r}"
    )


@pytest.mark.unit
def test_proactive_pass_chat_responses_carry_reason_code():
    """Proactive pass/chat responses expose a stable machine reason.

    The endpoint is intentionally too heavy for a full runtime call here, so keep
    this as a source-level contract: any direct response dict with
    ``action == pass/chat`` in the proactive delivery path must also carry
    ``reason_code``.
    """
    import main_routers.system_router as system_router
    from main_logic.proactive_chat import service as proactive_service

    missing = []
    for fn in (
        proactive_service.handle_proactive_chat,
        system_router._maybe_deliver_mini_game_invite,
    ):
        for line, action_values in _missing_reason_code_action_dicts(fn):
            missing.append((fn.__name__, line, action_values))

    assert not missing, (
        "proactive pass/chat response dicts must include reason_code; "
        f"missing={missing!r}"
    )


@pytest.mark.unit
def test_proactive_reason_code_body_helpers_preserve_shape():
    import main_routers.system_router as system_router

    pass_body = system_router._proactive_pass_body(
        system_router.PROACTIVE_REASON_PASS_SOURCE_EMPTY,
        message="no source",
    )
    assert pass_body == {
        "success": True,
        "reason_code": system_router.PROACTIVE_REASON_PASS_SOURCE_EMPTY,
        "stage": system_router.PROACTIVE_STAGE_SOURCE_SELECTION,
        "action": "pass",
        "message": "no source",
    }

    chat_body = system_router._proactive_chat_body(message="delivered")
    assert chat_body["success"] is True
    assert chat_body["action"] == "chat"
    assert chat_body["reason_code"] == system_router.PROACTIVE_REASON_CHAT_DELIVERED
    assert chat_body["stage"] == system_router.PROACTIVE_STAGE_DELIVERY
    assert chat_body["message"] == "delivered"

    error_body = system_router._proactive_error_body(
        system_router.PROACTIVE_REASON_ERROR_TIMEOUT,
        error="timeout",
    )
    assert error_body["success"] is False
    assert error_body["reason_code"] == system_router.PROACTIVE_REASON_ERROR_TIMEOUT
    assert error_body["stage"] == system_router.PROACTIVE_STAGE_RUNTIME_ERROR

    source_error_body = system_router._proactive_pass_body(
        system_router.PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
        success=False,
        error="all source fetches failed",
    )
    assert source_error_body["success"] is False
    assert source_error_body["action"] == "pass"
    assert source_error_body["reason_code"] == (
        system_router.PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED
    )
    assert source_error_body["stage"] == system_router.PROACTIVE_STAGE_SOURCE_SELECTION

    old_pass_body = {"success": True, "action": "pass", "message": "legacy"}
    assert system_router._ensure_proactive_reason_code(old_pass_body) == {
        "success": True,
        "action": "pass",
        "message": "legacy",
        "reason_code": system_router.PROACTIVE_REASON_PASS_UNSPECIFIED,
        "stage": system_router.PROACTIVE_STAGE_UNKNOWN,
    }


@pytest.mark.unit
def test_proactive_reason_codes_have_stage_mapping():
    import main_routers.system_router as system_router

    known_stages = {
        system_router.PROACTIVE_STAGE_ENTRY_GUARD,
        system_router.PROACTIVE_STAGE_ACTIVITY_GATE,
        system_router.PROACTIVE_STAGE_SOURCE_SELECTION,
        system_router.PROACTIVE_STAGE_MODEL_DECISION,
        system_router.PROACTIVE_STAGE_GENERATION,
        system_router.PROACTIVE_STAGE_DEDUP,
        system_router.PROACTIVE_STAGE_DELIVERY,
        system_router.PROACTIVE_STAGE_RUNTIME_ERROR,
        system_router.PROACTIVE_STAGE_UNKNOWN,
    }
    reason_codes = [
        value
        for name, value in vars(system_router).items()
        if name.startswith("PROACTIVE_REASON_") and isinstance(value, str)
    ]

    assert reason_codes
    for reason_code in reason_codes:
        stage = system_router._proactive_stage_for_reason(reason_code)
        assert stage in known_stages, reason_code
        assert stage != system_router.PROACTIVE_STAGE_UNKNOWN or reason_code == (
            system_router.PROACTIVE_REASON_PASS_UNSPECIFIED
        )


@pytest.mark.unit
def test_proactive_phase2_abort_reasons_stay_specific():
    import inspect

    from main_logic.proactive_chat import generation

    src = inspect.getsource(generation._generate_phase2_stream)
    assert "abort_reason_code" in src
    assert "PROACTIVE_REASON_DELIVERY_PREEMPTED" in src
    assert "PROACTIVE_REASON_PASS_MODEL_PASS" in src
    assert "final_reason = abort_reason_code or PROACTIVE_REASON_PASS_GENERATION_EMPTY" in src


@pytest.mark.unit
def test_end_proactive_rewrites_body_after_reason_stage_fallback():
    import inspect

    from main_logic.proactive_chat.service import ProactiveLifecycle

    src = inspect.getsource(ProactiveLifecycle.finalize)
    assert "body = _ensure_proactive_reason_code(dict(result.body))" in src
    assert 'body.setdefault(' in src
    assert '"next_schedule_fixed_mode"' in src
    assert "return result" not in src


@pytest.mark.unit
def test_proactive_chat_concurrent_rejection_returns_http_409():
    """``proactive_chat`` handler 因 ``try_start_proactive`` 拒绝时必须返回
    HTTP 409，且 response body 是 ``{"success": False, "error": <str>}``——
    这是前端 ``app-proactive.js`` ``triggerProactiveChat`` 用来识别"server
    并发忙、本次 attempt 不消耗 backoff"的 wire 契约。

    任何把这条改成 status_code=200 / 不同 body shape 的 refactor 都会让前端
    的 ``if (response.status === 409) return false`` guard 失效，server 一忙
    就被前端误判成一次有效 attempt → 升级 backoff → 节奏整体往后拉，跟 server
    实际状态正交。

    用源码扫描而非 runtime call：handler 太重（依赖 MEMORY_SERVER_PORT / WS
    / activity tracker / async LLM client 等等），跑完整 startup 不划算。这里
    只钉静态保证。
    """
    import inspect

    from main_logic.proactive_chat import service as proactive_service

    src = inspect.getsource(proactive_service.handle_proactive_chat)

    # try_start_proactive 失败那一段必须有 status_code=409
    assert "try_start_proactive" in src, (
        "proactive_chat 必须用 try_start_proactive 做并发占坑（PR #1015 引入的"
        "原子 check+claim）；refactor 改成无锁 can_start_proactive 双查会重新"
        "引入 PR #1015 修过的双进 PHASE1 race"
    )
    # 拒绝路径必须继续使用领域决策给出的 409 + success=False（前端契约）。
    assert "status_code=entry_result.status_code" in src
    from main_logic.proactive_chat.decisions import _decide_busy_entry_guard

    result = _decide_busy_entry_guard(False, state_snapshot={"phase": "PHASE1"})
    assert result is not None
    assert result.status_code == 409, (
        "proactive_chat 并发拒绝时必须 HTTP 409 —— 前端 "
        "app-proactive.js triggerProactiveChat 据此跳过 backoff++。若改成 "
        "200/500 等其他 status，前端会把 server 忙误算成 attempt 消耗"
    )
    assert result.body["success"] is False
