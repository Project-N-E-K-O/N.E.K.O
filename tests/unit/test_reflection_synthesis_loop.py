# -*- coding: utf-8 -*-
"""Reflection-synthesis backend-loop regression test.

Pin two invariants:

1. ``_periodic_reflection_synthesis_loop`` exists and calls ``reflection_engine.synthesize_reflections(name)``
   for every catgirl each ``MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS`` interval.
   One character's failure neither blocks others nor terminates the loop.

2. ``main_routers/system_router.py``'s proactive_chat handler no longer
   POSTs /reflect/{name}. That mutation previously sat on the frontend-driven critical path,
   introduced alongside PR #1015, making reflection growth stop whenever frontend/proactive triggers
   were absent. Synthesis now runs only in backend loops; any refactor restoring /reflect POSTs
   to proactive_chat is caught here because frontend decoupling is intentional.

Not tested here; leave for manual/e2e:
- Whether the real LLM generates valid reflections; synthesize_reflections already has
  ``tests/unit/test_reflection_*`` coverage.
- Whether frontend /api/proactive_chat setTimeout fires, a frontend concern
  outside this PR. This fix guarantees reflection keeps growing even when proactive
  chat never triggers.
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
    """Call synthesize_reflections once per catgirl in each loop round.

    Patch asyncio.sleep as a switch: the second call at loop tail raises CancelledError,
    ending after one round; the first _INITIAL_DELAY_REFLECTION_SYNTHESIS sleep
    returns immediately.
    """
    from app import memory_server

    fake_engine = MagicMock()
    fake_engine.synthesize_reflections = AsyncMock(return_value=[])
    fake_engine.synthesize_scoped_reflections = AsyncMock(return_value=[])

    fake_cm = MagicMock()
    fake_cm.aload_characters = AsyncMock(return_value={
        '猫娘': {'悠怡': {}, '喵酱': {}, '小八': {}}
    })

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        # Initial delay and tail sleep both yield; the second tail sleep ends the loop.
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    with patch.object(memory_server.runtime, "reflection_engine", fake_engine), \
         patch.object(memory_server.runtime, "_config_manager", fake_cm), \
         patch("app.memory_server.refine_loops.asyncio.sleep", new=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await memory_server._periodic_reflection_synthesis_loop()

    # Each catgirl is called once.
    assert fake_engine.synthesize_reflections.await_count == 3
    assert fake_engine.synthesize_scoped_reflections.await_count == 3
    called_names = {call.args[0] for call in fake_engine.synthesize_reflections.await_args_list}
    assert called_names == {'悠怡', '喵酱', '小八'}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_synthesis_loop_uses_character_prompt_locale():
    from app import memory_server
    from utils.language_utils import get_global_language_full

    observed = []

    async def synthesize(name):
        observed.append((name, get_global_language_full()))
        return []

    fake_engine = MagicMock()
    fake_engine.synthesize_reflections = AsyncMock(side_effect=synthesize)
    fake_engine.synthesize_scoped_reflections = AsyncMock(return_value=[])
    fake_cm = MagicMock()
    fake_cm.aload_characters = AsyncMock(return_value={
        '猫娘': {'悠怡': {}}
    })
    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise asyncio.CancelledError

    with patch.object(memory_server.runtime, "reflection_engine", fake_engine), \
         patch.object(memory_server.runtime, "_config_manager", fake_cm), \
         patch(
             "app.memory_server.locale_state.get_character_prompt_locale",
             return_value="zh-TW",
         ), \
         patch("app.memory_server.refine_loops.asyncio.sleep", new=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await memory_server._periodic_reflection_synthesis_loop()

    assert observed == [('悠怡', 'zh-TW')]
    scoped_kwargs = fake_engine.synthesize_scoped_reflections.await_args.kwargs
    assert scoped_kwargs["max_subjects"] == 1
    assert scoped_kwargs["subject_locale_resolver"] is not None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reflection_synthesis_loop_single_character_failure_does_not_abort_others():
    """If 悠怡 synthesis raises, 喵酱 / 小八 are still each called once."""  # noqa: DOCSTRING_CJK
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
    """aload_characters exceptions skip the round without killing the loop; retry after the next sleep."""
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
        # Run two rounds: initial delay, postfailure sleep, then postsuccess sleep.
        if len(sleep_calls) >= 3:
            raise asyncio.CancelledError

    with patch.object(memory_server.runtime, "reflection_engine", fake_engine), \
         patch.object(memory_server.runtime, "_config_manager", fake_cm), \
         patch("app.memory_server.refine_loops.asyncio.sleep", new=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await memory_server._periodic_reflection_synthesis_loop()

    # First-round aload_characters failure -> zero synthesize calls.
    # Second-round success -> one call for 悠怡.
    assert fake_engine.synthesize_reflections.await_count == 1
    assert fake_engine.synthesize_reflections.await_args.args[0] == '悠怡'


@pytest.mark.unit
def test_reflection_synthesis_loop_registered_in_background_tasks():
    """ensure_memory_server_runtime_initialized must register
    _periodic_reflection_synthesis_loop through _spawn_background_task.

    Scan source rather than instrument runtime: pin registration itself, since removing the line is a tiny,
    easily missed regression; complete server startup is unnecessary and
    inappropriate here.
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
    """main_routers/system_router.proactive_chat must no longer POST /reflect.

    After PR #1015, reflection synthesis and auto_transitions moved into memory_server's
    two backend loops, synthesis and auto_promote. Keeping this mutation in proactive_chat's
    critical path only increases latency and tightly couples reflection lifecycle to
    frontend setTimeout. Any refactor restoring it must first explain
    why backend loops are insufficient.
    """
    import inspect
    import re

    from main_logic.proactive_chat import service as proactive_service

    src = inspect.getsource(proactive_service.handle_proactive_chat)
    # Match actual HTTP POSTs to /reflect precisely, not mere "/reflect/" literals,
    # because historical comments explaining backend migration may retain paths.
    # Any HTTP POST code to /reflect contains .post(...reflect...).
    post_to_reflect = re.search(
        r"""\.post\s*\(\s*[^)]*?["']\S*?/reflect/""",
        src,
    )
    assert post_to_reflect is None, (
        "proactive_chat handler 里不能再 POST /reflect/{name} —— 合成已迁到 "
        "_periodic_reflection_synthesis_loop 后端循环；这条 mutation 放在 "
        "proactive 关键路径上会拖延 ~15s + 让 reflection 增长依赖前端。"
        # Explicit parentheses show !r's scope without making readers calculate conditional-expression/
        # conversion-spec precedence. PEP 498 defines it, but future readers
        # may not see it immediately; this intuition burden caused CodeRabbit PR #1401's SyntaxError false positive,
        # so parentheses prevent similar confusion.
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
    """When try_start_proactive rejects, the proactive_chat handler must return
    HTTP 409 with body {"success": False, "error": <str>},
    the wire contract app-proactive.js triggerProactiveChat uses to recognize server
    concurrency contention without consuming this attempt's backoff.

    Changing to status_code=200 or another body shape breaks frontend
    ``if (response.status === 409) return false``, making busy responses count as
    valid attempts, escalating backoff and delaying cadence independently of actual
    server state.

    Scan source rather than call runtime: the handler depends heavily on MEMORY_SERVER_PORT / WS
    / activity tracking / async LLM clients, making full startup unnecessarily costly.
    Pin only the static guarantee here.
    """
    import inspect

    from main_logic.proactive_chat import service as proactive_service

    src = inspect.getsource(proactive_service.handle_proactive_chat)

    # The try_start_proactive failure branch must specify status_code=409.
    assert "try_start_proactive" in src, (
        "proactive_chat 必须用 try_start_proactive 做并发占坑（PR #1015 引入的"
        "原子 check+claim）；refactor 改成无锁 can_start_proactive 双查会重新"
        "引入 PR #1015 修过的双进 PHASE1 race"
    )
    # Rejection retains the domain decision's 409 + success=False frontend contract.
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
