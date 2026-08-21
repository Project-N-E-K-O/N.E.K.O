# -*- coding: utf-8 -*-
"""best-effort 后台压缩（主路径压缩失败时兜底）回归测试。

主路径 update_history 压缩失败（如 RPM 限流连续失败）→ _on_compress_done(ok=False)
起一个受保护的一次性后台压缩；主路径某轮成功 → ok=True cancel 在跑的后台。失败退避
（复用 review 的 Gate 6 模式）防 summary 模型持续故障时每轮起注定失败的任务空烧。
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.llm_client import AIMessage, HumanMessage, SystemMessage


def _history(n: int):
    out = []
    for i in range(n):
        out.append(HumanMessage(content=f"u{i}") if i % 2 == 0 else AIMessage(content=f"a{i}"))
    return out


async def _cleanup_task(task):
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            # cleanup-only：吞掉 cancel 抛出的 CancelledError 及 task 内部任何异常
            pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_character_drains_old_identity_review_and_backup_tasks():
    from app import memory_server

    name = "即将改名角色"
    cancel_event = asyncio.Event()
    review_task = asyncio.create_task(asyncio.sleep(30))
    backup_task = asyncio.create_task(asyncio.sleep(30))
    memory_server.review.correction_cancel_flags[name] = cancel_event
    memory_server.review.correction_tasks[name] = review_task
    memory_server.review.compress_backup_tasks[name] = backup_task
    memory_server.review.compress_backup_task_generations[name] = ("old", 0)
    fake_time_manager = MagicMock()
    fake_time_manager.dispose_engine.return_value = False

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), patch.object(
        memory_server.runtime, "_deferred_time_managers", [],
    ):
        result = await memory_server.runtime.release_character_resources(
            name,
            derived_task_claim_token="drain-claim",
            derived_task_claim_generation=0,
        )

    assert result["status"] == "success"
    assert result["cancelled_derived_tasks"] == 2
    assert cancel_event.is_set()
    assert review_task.cancelled()
    assert backup_task.cancelled()
    assert name not in memory_server.review.correction_tasks
    assert name not in memory_server.review.compress_backup_tasks
    assert name not in memory_server.review.compress_backup_task_generations
    fake_time_manager.dispose_engine.assert_called_once_with(name)
    memory_server.review._retired_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_character_disposes_current_and_deferred_time_managers_once():
    """A hot-reload generation must not retain the character's SQLite handle."""
    from app import memory_server

    name = "热重载后删除角色"
    current_manager = MagicMock()
    current_manager.dispose_engine.return_value = False
    old_manager = MagicMock()
    old_manager.dispose_engine.return_value = True
    other_old_manager = MagicMock()
    other_old_manager.dispose_engine.return_value = False
    deferred = [old_manager, current_manager, old_manager, other_old_manager]

    with patch.object(memory_server.runtime, "time_manager", current_manager), patch.object(
        memory_server.runtime, "_deferred_time_managers", deferred,
    ):
        result = await memory_server.runtime.release_character_resources(
            name,
            derived_task_claim_token="cross-generation-claim",
            derived_task_claim_generation=0,
        )

        assert memory_server.runtime._deferred_time_managers == deferred

    assert result["status"] == "success"
    current_manager.dispose_engine.assert_called_once_with(name)
    old_manager.dispose_engine.assert_called_once_with(name)
    other_old_manager.dispose_engine.assert_called_once_with(name)
    memory_server.review._retired_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_drains_inflight_process_and_fences_engine_recreation():
    """A pre-release write must finish, while queued writes cannot reopen SQLite."""
    from app import memory_server

    name = "发布窗口竞态角色"
    claim_token = "foreground-drain-claim"
    write_started = asyncio.Event()
    finish_write = asyncio.Event()
    order: list[str] = []

    async def _astore(*_args, **_kwargs):
        order.append("write_started")
        write_started.set()
        await finish_write.wait()
        order.append("write_finished")

    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(side_effect=_astore)
    fake_time_manager.dispose_engine.side_effect = lambda _name: order.append("disposed") or True
    fake_recent = MagicMock()
    fake_recent.update_history = AsyncMock(return_value=None)
    fake_config = MagicMock()
    fake_config.aload_characters = AsyncMock(return_value={"猫娘": {name: {}}})
    request = memory_server.HistoryRequest(input_history="[]")

    memory_server.review._retired_derived_task_names.discard(name)
    memory_server.review._publication_held_derived_task_names.discard(name)
    context_token = memory_server.runtime._begin_character_request(name)
    assert context_token is not None
    try:
        with patch.object(memory_server.runtime, "time_manager", fake_time_manager), patch.object(
            memory_server.runtime, "_deferred_time_managers", [],
        ), patch.object(
            memory_server.runtime, "recent_history_manager", fake_recent,
        ), patch.object(
            memory_server.runtime, "_config_manager", fake_config,
        ), patch.object(
            memory_server.runtime, "embedding_warmup_worker", None,
        ), patch.object(
            memory_server.post_turn, "_spawn_outbox_post_turn_signals", AsyncMock(),
        ), patch.object(
            memory_server.review, "maybe_spawn_review", AsyncMock(),
        ):
            process_task = asyncio.create_task(
                memory_server.process_conversation(request, name)
            )
            await asyncio.wait_for(write_started.wait(), timeout=1)

            release_task = asyncio.create_task(
                memory_server.runtime.release_character_resources(
                    name,
                    hold_derived_task_admission=True,
                    derived_task_claim_token=claim_token,
                    derived_task_claim_generation=0,
                )
            )

            async def _wait_for_hold():
                while not memory_server.review.is_character_publication_held(name):
                    await asyncio.sleep(0)

            await asyncio.wait_for(_wait_for_hold(), timeout=1)
            assert memory_server.runtime._is_character_engine_admitted(name)
            assert "disposed" not in order
            finish_write.set()
            process_result = await process_task
            memory_server.runtime._end_character_request(name, context_token)
            context_token = None
            release_result = await release_task

            assert process_result == {"status": "processed"}
            assert release_result["status"] == "success"
            assert order == ["write_started", "write_finished", "disposed"]

            assert memory_server.runtime._begin_character_request(name) is None
            assert fake_time_manager.astore_conversation.await_count == 1
    finally:
        if context_token is not None:
            memory_server.runtime._end_character_request(name, context_token)
        await memory_server.review.release_character_derived_task_admission_claim(
            name,
            claim_token,
        )
        memory_server.review._retired_derived_task_names.discard(name)
        memory_server.review._publication_held_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publication_guard_rejects_new_write_with_non_success_status():
    from starlette.requests import Request

    from app import memory_server

    name = "已关闭准入角色"
    memory_server.review._publication_held_derived_task_names.add(name)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": f"/cache/{name}",
            "raw_path": f"/cache/{name}".encode(),
            "query_string": b"",
            "headers": [],
            "server": ("127.0.0.1", 48912),
            "client": ("127.0.0.1", 1),
        }
    )
    call_next = AsyncMock(side_effect=AssertionError("fenced request reached endpoint"))
    try:
        response = await memory_server.runtime.character_publication_guard(
            request,
            call_next,
        )
        assert response.status_code == 409
        assert json.loads(response.body)["status"] == "cancelled"
        call_next.assert_not_awaited()
    finally:
        memory_server.review._publication_held_derived_task_names.discard(name)


def _memory_server_request(path: str):
    """Build the minimal ASGI scope the publication guard reads."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "server": ("127.0.0.1", 48912),
            "client": ("127.0.0.1", 1),
        }
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publication_guard_normalizes_the_path_name_like_the_endpoint():
    """The endpoint strips the name; admission must key on the same value."""
    from app import memory_server

    name = "两端归一角色"
    memory_server.review._publication_held_derived_task_names.add(name)
    call_next = AsyncMock(side_effect=AssertionError("fenced request reached endpoint"))
    try:
        response = await memory_server.runtime.character_publication_guard(
            _memory_server_request(f"/process/  {name} "),
            call_next,
        )
        assert response.status_code == 409
        call_next.assert_not_awaited()
    finally:
        memory_server.review._publication_held_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publication_guard_covers_group_memory_scoped_writes():
    """Scoped writes reach the same SQLite index, so they must be fenced+drained."""
    from starlette.responses import JSONResponse as StarletteJSONResponse

    from app import memory_server

    name = "群记忆写入角色"
    memory_server.review._publication_held_derived_task_names.discard(name)
    observed_active: list[int] = []

    async def _call_next(_request):
        observed_active.append(
            memory_server.runtime._active_character_requests.get(name, 0)
        )
        return StarletteJSONResponse({"status": "ok"})

    admitted = await memory_server.runtime.character_publication_guard(
        _memory_server_request(f"/internal/memory/{name}/scoped_facts"),
        _call_next,
    )
    assert admitted.status_code == 200
    # 排空账本必须看得见它，否则 release 会在它写到一半时 dispose。
    assert observed_active == [1]
    assert memory_server.runtime._active_character_requests.get(name, 0) == 0

    memory_server.review._publication_held_derived_task_names.add(name)
    blocked = AsyncMock(side_effect=AssertionError("fenced request reached endpoint"))
    try:
        response = await memory_server.runtime.character_publication_guard(
            _memory_server_request(f"/internal/memory/{name}/scoped_history"),
            blocked,
        )
        assert response.status_code == 409
        blocked.assert_not_awaited()

        # 只读的 scoped_context 不进围栏（读路径由引擎准入检查兜底）。
        read_next = AsyncMock(return_value=StarletteJSONResponse({"status": "ok"}))
        read_response = await memory_server.runtime.character_publication_guard(
            _memory_server_request(f"/internal/memory/{name}/scoped_context"),
            read_next,
        )
        assert read_response.status_code == 200
        read_next.assert_awaited_once()
    finally:
        memory_server.review._publication_held_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_admission_lease_survives_a_hold_flip_through_the_real_middleware():
    """The lease is a ContextVar set in middleware — prove it reaches the endpoint.

    Calling the guard directly (as the other tests do) runs it in the caller's
    own task, so it cannot show whether Starlette's middleware task spawn
    carries the lease downstream. Drive a real ASGI stack instead.
    """
    import httpx
    from fastapi import FastAPI

    from app import memory_server

    name = "租约穿透角色"
    observed: dict[str, object] = {}

    probe = FastAPI()
    probe.middleware("http")(memory_server.runtime.character_publication_guard)

    @probe.post("/process/{lanlan_name}")
    async def _probe_endpoint(lanlan_name: str):
        observed["active"] = memory_server.runtime._active_character_requests.get(
            lanlan_name, 0
        )
        observed["admitted"] = memory_server.runtime._is_character_engine_admitted(
            lanlan_name
        )
        # release 在这个请求准入之后才设 hold：已准入的写入必须能写完。
        memory_server.review._publication_held_derived_task_names.add(lanlan_name)
        observed["admitted_after_hold"] = (
            memory_server.runtime._is_character_engine_admitted(lanlan_name)
        )
        return {"status": "processed"}

    memory_server.review._publication_held_derived_task_names.discard(name)
    try:
        transport = httpx.ASGITransport(app=probe)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://memory-server-probe",
        ) as client:
            response = await client.post(f"/process/{name}")
            assert response.status_code == 200

            # hold 已生效：下一个请求必须被拒。
            blocked = await client.post(f"/process/{name}")
            assert blocked.status_code == 409
            assert blocked.json()["status"] == "cancelled"
    finally:
        memory_server.review._publication_held_derived_task_names.discard(name)

    assert observed["active"] == 1
    assert observed["admitted"] is True
    assert observed["admitted_after_hold"] is True
    # 请求结束后账本必须归零，否则 release 会永远排空不掉。
    assert memory_server.runtime._active_character_requests.get(name, 0) == 0


@pytest.mark.unit
def test_release_drain_budget_stays_under_the_tightest_caller_timeout():
    """A caller that gives up first starts deleting files while the drain runs."""
    import inspect

    from app import memory_server
    from main_routers.workshop_router import unsubscribe

    parameters = inspect.signature(
        unsubscribe._release_workshop_character_handles
    ).parameters
    drain_budget = memory_server.runtime._CHARACTER_REQUEST_DRAIN_TIMEOUT_SECONDS
    assert drain_budget < parameters["per_call_timeout"].default
    assert drain_budget < parameters["overall_timeout"].default


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_cancels_tracked_post_turn_task():
    from app import memory_server

    name = "待取消后台角色"
    task = asyncio.create_task(asyncio.sleep(30))
    memory_server.post_turn._track_character_post_turn_task(name, task)

    cancelled = await memory_server.post_turn.cancel_character_post_turn_tasks(name)

    assert cancelled == 1
    assert task.cancelled()
    assert name not in memory_server.post_turn._character_post_turn_tasks


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_cancels_post_turn_tasks_before_draining_requests():
    """Post-turn work holds no admission lease, so it must die before the drain."""
    from app import memory_server

    name = "排空前取消后台角色"
    claim_token = "cancel-before-drain-claim"
    memory_server.review._retired_derived_task_names.discard(name)
    memory_server.review._publication_held_derived_task_names.discard(name)
    post_turn_task = asyncio.create_task(asyncio.sleep(30))
    memory_server.post_turn._track_character_post_turn_task(name, post_turn_task)
    fake_time_manager = MagicMock()
    fake_time_manager.dispose_engine.return_value = True

    context_token = memory_server.runtime._begin_character_request(name)
    assert context_token is not None
    release_task = None
    try:
        with patch.object(memory_server.runtime, "time_manager", fake_time_manager), patch.object(
            memory_server.runtime, "_deferred_time_managers", [],
        ):
            release_task = asyncio.create_task(
                memory_server.runtime.release_character_resources(
                    name,
                    hold_derived_task_admission=True,
                    derived_task_claim_token=claim_token,
                    derived_task_claim_generation=0,
                )
            )

            async def _wait_for_post_turn_cancel():
                while not post_turn_task.cancelled():
                    await asyncio.sleep(0)

            await asyncio.wait_for(_wait_for_post_turn_cancel(), timeout=1)
            # 后台任务已经死了，但前台写入还在跑、引擎也还没释放。
            assert memory_server.runtime._active_character_requests.get(name) == 1
            fake_time_manager.dispose_engine.assert_not_called()

            memory_server.runtime._end_character_request(name, context_token)
            context_token = None
            result = await asyncio.wait_for(release_task, timeout=2)
            release_task = None

        assert result["status"] == "success"
        fake_time_manager.dispose_engine.assert_called_once_with(name)
    finally:
        if context_token is not None:
            memory_server.runtime._end_character_request(name, context_token)
        await _cleanup_task(release_task)
        await _cleanup_task(post_turn_task)
        await memory_server.review.release_character_derived_task_admission_claim(
            name, claim_token,
        )
        memory_server.review._retired_derived_task_names.discard(name)
        memory_server.review._publication_held_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_gives_up_instead_of_disposing_after_the_caller_timeout():
    """An undrainable write must fail the release, not dispose behind the caller."""
    from app import memory_server

    name = "排空超时角色"
    claim_token = "drain-timeout-claim"
    memory_server.review._retired_derived_task_names.discard(name)
    memory_server.review._publication_held_derived_task_names.discard(name)
    fake_time_manager = MagicMock()
    fake_time_manager.dispose_engine.return_value = True

    context_token = memory_server.runtime._begin_character_request(name)
    assert context_token is not None
    try:
        with patch.object(memory_server.runtime, "time_manager", fake_time_manager), patch.object(
            memory_server.runtime, "_deferred_time_managers", [],
        ), patch.object(
            memory_server.runtime, "_CHARACTER_REQUEST_DRAIN_TIMEOUT_SECONDS", 0.05,
        ):
            result = await asyncio.wait_for(
                memory_server.runtime.release_character_resources(
                    name,
                    hold_derived_task_admission=True,
                    derived_task_claim_token=claim_token,
                    derived_task_claim_generation=0,
                ),
                timeout=2,
            )
    finally:
        memory_server.runtime._end_character_request(name, context_token)

    assert result.status_code == 503
    fake_time_manager.dispose_engine.assert_not_called()
    # 声明已归还：主进程补偿后新写入必须能重新准入。
    assert not memory_server.review.is_character_derived_task_claim_active(
        name, claim_token,
    )
    assert memory_server.runtime._is_character_publication_admitted(name)
    memory_server.review._retired_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_without_a_publication_hold_never_waits_for_new_writes():
    """Cloudsave / unsubscribe take no hold, so nothing stops new admissions.

    Draining there would just burn the whole budget and report failure while the
    caller goes on deleting storage — worse than releasing the handles at once.
    """
    from app import memory_server

    name = "无围栏释放角色"
    memory_server.review._retired_derived_task_names.discard(name)
    memory_server.review._publication_held_derived_task_names.discard(name)
    fake_time_manager = MagicMock()
    fake_time_manager.dispose_engine.return_value = True

    # 一个永不结束的在途写入：真去排空就一定超时。
    context_token = memory_server.runtime._begin_character_request(name)
    assert context_token is not None
    try:
        with patch.object(memory_server.runtime, "time_manager", fake_time_manager), patch.object(
            memory_server.runtime, "_deferred_time_managers", [],
        ), patch.object(
            memory_server.runtime, "_CHARACTER_REQUEST_DRAIN_TIMEOUT_SECONDS", 0.05,
        ):
            result = await asyncio.wait_for(
                memory_server.runtime.release_character_resources(
                    name,
                    hold_derived_task_admission=False,
                    derived_task_claim_token="no-hold-claim",
                    derived_task_claim_generation=0,
                ),
                timeout=2,
            )
    finally:
        memory_server.runtime._end_character_request(name, context_token)

    assert result["status"] == "success"
    fake_time_manager.dispose_engine.assert_called_once_with(name)
    memory_server.review._retired_derived_task_names.discard(name)
    await memory_server.review.release_character_derived_task_admission_claim(
        name, "no-hold-claim",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_skips_dispose_when_claim_withdrawn_while_draining():
    """The caller's timeout compensation wins; a late dispose must not race it."""
    from app import memory_server

    name = "补偿撤回角色"
    claim_token = "withdrawn-while-draining-claim"
    memory_server.review._retired_derived_task_names.discard(name)
    memory_server.review._publication_held_derived_task_names.discard(name)
    fake_time_manager = MagicMock()
    fake_time_manager.dispose_engine.return_value = True

    context_token = memory_server.runtime._begin_character_request(name)
    assert context_token is not None
    release_task = None
    try:
        with patch.object(memory_server.runtime, "time_manager", fake_time_manager), patch.object(
            memory_server.runtime, "_deferred_time_managers", [],
        ):
            release_task = asyncio.create_task(
                memory_server.runtime.release_character_resources(
                    name,
                    hold_derived_task_admission=True,
                    derived_task_claim_token=claim_token,
                    derived_task_claim_generation=0,
                )
            )

            async def _wait_for_hold():
                while not memory_server.review.is_character_publication_held(name):
                    await asyncio.sleep(0)

            await asyncio.wait_for(_wait_for_hold(), timeout=1)
            # 主进程 5s 超时后的补偿：撤回声明并重新开放准入。
            await memory_server.review.release_character_derived_task_admission_claim(
                name, claim_token,
            )
            memory_server.runtime._end_character_request(name, context_token)
            context_token = None
            result = await asyncio.wait_for(release_task, timeout=2)
            release_task = None

        assert result.status_code == 409
        assert json.loads(result.body)["status"] == "cancelled"
        fake_time_manager.dispose_engine.assert_not_called()
    finally:
        if context_token is not None:
            memory_server.runtime._end_character_request(name, context_token)
        await _cleanup_task(release_task)
        memory_server.review._retired_derived_task_names.discard(name)
        memory_server.review._publication_held_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_negative_keyword_hook_task_is_tracked_as_post_turn_work():
    """The nested hook task must be cancellable through the per-character registry."""
    from app import memory_server
    from app.memory_server import post_turn as post_turn_module

    name = "负面关键词后台角色"
    memory_server.post_turn._character_post_turn_tasks.pop(name, None)
    hook_started = asyncio.Event()

    async def _hook(*_args, **_kwargs):
        hook_started.set()
        await asyncio.sleep(30)

    fake_reflection = MagicMock()
    fake_reflection.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection.aload_surfaced = AsyncMock(return_value=[])
    fake_persona = MagicMock()
    fake_persona.arecord_mentions = AsyncMock(return_value=None)

    with patch.object(
        post_turn_module.gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=True),
    ), patch.object(
        post_turn_module.signal_extraction, "_signal_check_record_turn", MagicMock(),
    ), patch.object(
        post_turn_module.signal_extraction,
        "_amaybe_trigger_negative_keyword_hook",
        MagicMock(side_effect=_hook),
    ), patch.object(
        post_turn_module.runtime, "reflection_engine", fake_reflection,
    ), patch.object(
        post_turn_module.runtime, "persona_manager", fake_persona,
    ), patch.object(
        post_turn_module, "_resolve_corrections_with_subject_locale", AsyncMock(return_value=0),
    ):
        await post_turn_module._run_post_turn_signals(
            [HumanMessage(content="你好喵")], name,
        )
        await asyncio.wait_for(hook_started.wait(), timeout=1)

        assert len(memory_server.post_turn._character_post_turn_tasks.get(name, ())) == 1
        cancelled = await memory_server.post_turn.cancel_character_post_turn_tasks(name)

    assert cancelled == 1
    assert name not in memory_server.post_turn._character_post_turn_tasks


@pytest.mark.unit
def test_cross_generation_release_closes_real_sqlite_pool(tmp_path):
    """The deferred generation must stop holding the database file on Windows."""
    from sqlalchemy import create_engine, text

    from app import memory_server
    from memory.timeindex import TimeIndexedMemory

    name = "真实连接池角色"
    db_path = tmp_path / "time_indexed.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE probe (id INTEGER PRIMARY KEY)"))

    current_manager = TimeIndexedMemory(recent_history_manager=None)
    old_manager = TimeIndexedMemory(recent_history_manager=None)
    old_manager.engines[name] = engine
    old_manager.db_paths[name] = str(db_path)
    deferred = [old_manager]

    with patch.object(memory_server.runtime, "time_manager", current_manager), patch.object(
        memory_server.runtime, "_deferred_time_managers", deferred,
    ):
        scanned_count, released_count = (
            memory_server.runtime._dispose_character_engines_across_generations(name)
        )

        assert memory_server.runtime._deferred_time_managers == deferred

    assert scanned_count == 2
    assert released_count == 1
    assert name not in old_manager.engines
    assert name not in old_manager.db_paths
    db_path.unlink()
    assert not db_path.exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_failure_restores_derived_task_admission():
    """A failed resource release must not permanently retire an active name."""
    from app import memory_server

    name = "释放失败角色"
    memory_server.review._retired_derived_task_names.discard(name)
    memory_server.review._publication_held_derived_task_names.discard(name)
    fake_time_manager = MagicMock()
    fake_time_manager.dispose_engine.side_effect = OSError("busy")
    deferred_manager = MagicMock()
    deferred_manager.dispose_engine.return_value = True

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), patch.object(
        memory_server.runtime, "_deferred_time_managers", [deferred_manager],
    ):
        result = await memory_server.runtime.release_character_resources(
            name,
            hold_derived_task_admission=True,
            derived_task_claim_token="failure-claim",
            derived_task_claim_generation=0,
        )

    assert result.status_code == 500
    deferred_manager.dispose_engine.assert_called_once_with(name)
    assert name not in memory_server.review._retired_derived_task_names
    assert name not in memory_server.review._publication_held_derived_task_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_release_blocks_review_respawn_until_published_identity_reload():
    from app import memory_server

    name = "改名发布窗口角色"
    fake_mgr = MagicMock()
    fake_mgr.aget_recent_history = AsyncMock(return_value=([], ("path", 0)))

    await memory_server.review.cancel_character_derived_tasks(name)
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), patch.object(
        memory_server.gates, "_ais_review_enabled", AsyncMock(return_value=True),
    ):
        await memory_server.review.maybe_spawn_review(name)
        fake_mgr.aget_recent_history.assert_not_awaited()

        await memory_server.review.reconcile_character_derived_task_admission({name})
        await memory_server.review.maybe_spawn_review(name)
        fake_mgr.aget_recent_history.assert_awaited_once_with(
            name, include_admission=True,
        )

    memory_server.review._retired_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publication_hold_survives_unrelated_reload_until_explicit_resume():
    """An unrelated reload must not reopen a lifecycle-held identity."""
    from app import memory_server

    name = "改名发布窗口显式提交"
    fake_mgr = MagicMock()
    fake_mgr.aget_recent_history = AsyncMock(return_value=([], ("path", 0)))

    await memory_server.review.cancel_character_derived_tasks(
        name,
        hold_until_publication=True,
    )
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), patch.object(
        memory_server.gates, "_ais_review_enabled", AsyncMock(return_value=True),
    ):
        await memory_server.review.reconcile_character_derived_task_admission({name})
        await memory_server.review.maybe_spawn_review(name)
        fake_mgr.aget_recent_history.assert_not_awaited()

        await memory_server.review.reconcile_character_derived_task_admission(
            {name},
            resume_names={name},
        )
        await memory_server.review.maybe_spawn_review(name)
        fake_mgr.aget_recent_history.assert_awaited_once_with(
            name, include_admission=True,
        )

    memory_server.review._publication_held_derived_task_names.discard(name)
    memory_server.review._retired_derived_task_names.discard(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_releasing_one_admission_claim_preserves_concurrent_publication_hold():
    """An abort may release only its own claim for a shared character name."""
    from app import memory_server

    name = "并发准入持有角色"
    await memory_server.review.cancel_character_derived_tasks(
        name,
        hold_until_publication=True,
        claim_token="rename-claim",
    )
    await memory_server.review.cancel_character_derived_tasks(
        name,
        claim_token="cloud-claim",
    )

    await memory_server.review.release_character_derived_task_admission_claim(
        name,
        "cloud-claim",
    )

    assert name in memory_server.review._retired_derived_task_names
    assert name in memory_server.review._publication_held_derived_task_names
    assert memory_server.review._derived_task_admission_claims[name] == {
        "rename-claim": (True, 0),
    }

    await memory_server.review.release_character_derived_task_admission_claim(
        name,
        "rename-claim",
    )
    assert name not in memory_server.review._retired_derived_task_names
    assert name not in memory_server.review._publication_held_derived_task_names
    assert name not in memory_server.review._derived_task_admission_claims


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publication_resume_preserves_same_generation_claim():
    """Publishing an identity may clear old claims, never a later new-identity claim."""
    from app import memory_server

    name = "发布后并发准入角色"
    await memory_server.review.cancel_character_derived_tasks(
        name,
        hold_until_publication=True,
        claim_token="old-generation",
        claim_generation=11,
    )
    await memory_server.review.cancel_character_derived_tasks(
        name,
        claim_token="new-generation",
        claim_generation=12,
    )

    await memory_server.review.resume_character_derived_task_admission(
        name,
        published_generation=12,
    )

    assert memory_server.review._derived_task_admission_claims[name] == {
        "new-generation": (False, 12),
    }
    assert name in memory_server.review._retired_derived_task_names
    await memory_server.review.release_character_derived_task_admission_claim(
        name,
        "new-generation",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_released_token_tombstone_aborts_late_release_registration():
    """A compensation arriving first must make a late release endpoint a no-op."""
    from app import memory_server

    name = "乱序释放角色"
    token = "withdrawn-before-register"
    await memory_server.review.release_character_derived_task_admission_claim(
        name,
        token,
    )
    fake_time_manager = MagicMock()

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager):
        result = await memory_server.runtime.release_character_resources(
            name,
            derived_task_claim_token=token,
            derived_task_claim_generation=0,
        )

    assert result.status_code == 409
    fake_time_manager.dispose_engine.assert_not_called()
    assert name not in memory_server.review._retired_derived_task_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_released_registered_token_rejects_every_replay():
    """Withdrawing a registered token must make later duplicate releases no-ops."""
    from app import memory_server

    name = "重复晚到释放角色"
    token = "registered-then-withdrawn"
    await memory_server.review.cancel_character_derived_tasks(
        name,
        claim_token=token,
        claim_generation=0,
    )
    await memory_server.review.release_character_derived_task_admission_claim(
        name,
        token,
    )
    fake_time_manager = MagicMock()

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager):
        first = await memory_server.runtime.release_character_resources(
            name,
            derived_task_claim_token=token,
            derived_task_claim_generation=0,
        )
        second = await memory_server.runtime.release_character_resources(
            name,
            derived_task_claim_token=token,
            derived_task_claim_generation=0,
        )

    assert first.status_code == 409
    assert second.status_code == 409
    fake_time_manager.dispose_engine.assert_not_called()
    assert name not in memory_server.review._retired_derived_task_names


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_failure_spawns_backup():
    from app import memory_server
    name = "测试角色C"
    snapshot = _history(6)
    memory_server.gates._maint_state.pop(name, None)
    memory_server.compress_backup_tasks.pop(name, None)

    async def _slow_compress(*a, **k):
        await asyncio.sleep(30)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = _slow_compress

    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._on_compress_done(name, snapshot, ok=False, detailed=False)
        task = memory_server.compress_backup_tasks.get(name)
        assert task is not None and not task.done()  # 起了后台兜底
        await _cleanup_task(task)

    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_success_cancels_backup():
    from app import memory_server
    name = "测试角色C"
    task = MagicMock()
    task.done.return_value = False
    memory_server.compress_backup_tasks[name] = task

    with patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._on_compress_done(name, [], ok=True, detailed=False)

    task.cancel.assert_called_once()  # 主路径成功 → cancel 在跑的后台
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_in_flight_guard():
    from app import memory_server
    name = "测试角色C"
    memory_server.gates._maint_state.pop(name, None)

    existing = MagicMock()
    existing.done.return_value = False
    memory_server.compress_backup_tasks[name] = existing

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=None)
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._on_compress_done(name, _history(6), ok=False, detailed=False)

    # 同角色已有后台在跑 → 不重复起，仍是原 task
    assert memory_server.compress_backup_tasks[name] is existing
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_deadletter_skips_spawn():
    from app import memory_server
    from memory.recent import build_review_fingerprint
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    name = "测试角色C"
    snapshot = _history(6)
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "compress_backup_fail_fp": build_review_fingerprint(snapshot),
    }

    fake_mgr = MagicMock()
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._on_compress_done(name, snapshot, ok=False, detailed=False)

    # 连续失败 ≥ N 且输入未变 → dead-letter，不再起后台；但仍裁剪兜底
    assert name not in memory_server.compress_backup_tasks
    fake_mgr.enforce_hard_cap.assert_awaited_once()
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_on_compress_done_deadletter_resets_when_input_changed():
    from app import memory_server
    from memory.recent import build_review_fingerprint
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    name = "测试角色C"
    memory_server.compress_backup_tasks.pop(name, None)
    # 退避计数已满，但记录的是「旧输入」的 fingerprint
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "compress_backup_fail_fp": build_review_fingerprint(_history(4)),
    }
    new_snapshot = _history(8)  # 输入变了

    async def _slow_compress(*a, **k):
        await asyncio.sleep(30)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = _slow_compress
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._on_compress_done(name, new_snapshot, ok=False, detailed=False)
        # 输入变了 → 复位放行，起了后台
        task = memory_server.compress_backup_tasks.get(name)
        assert task is not None
        await _cleanup_task(task)

    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_compress_callback_cannot_spawn_after_release_drains_registry():
    """Retirement during an awaited gate must win over fallback spawning."""
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS

    name = "压缩回调退休竞态"
    snapshot = _history(6)
    gate_entered = asyncio.Event()
    release_gate = asyncio.Event()
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "compress_backup_generation": None,
    }

    async def _blocked_gate(*args, **kwargs):
        gate_entered.set()
        await release_gate.wait()
        return "retry"

    spawn = MagicMock()
    with patch.object(
        memory_server.gates,
        "_amutate_maint_state",
        side_effect=_blocked_gate,
    ), patch.object(memory_server.runtime, "_spawn_background_task", spawn):
        callback = asyncio.create_task(
            memory_server._on_compress_done(
                name,
                snapshot,
                ok=False,
                detailed=False,
            )
        )
        await asyncio.wait_for(gate_entered.wait(), timeout=1)
        await memory_server.review.cancel_character_derived_tasks(
            name,
            hold_until_publication=True,
        )
        release_gate.set()
        await asyncio.wait_for(callback, timeout=1)

    spawn.assert_not_called()
    assert name not in memory_server.compress_backup_tasks
    memory_server.review._publication_held_derived_task_names.discard(name)
    memory_server.review._retired_derived_task_names.discard(name)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_backup_compress_failure_bumps_backoff():
    from app import memory_server
    from memory.recent import build_review_fingerprint
    name = "测试角色C"
    snapshot = _history(6)
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=None)
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(name, snapshot, False)

    state = memory_server.gates._maint_state[name]
    assert state["compress_backup_fail_attempts"] == 1
    assert state["compress_backup_fail_fp"] == build_review_fingerprint(snapshot)
    fake_mgr.enforce_hard_cap.assert_awaited_once()  # 后台也压不成 → 裁剪兜底
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_backup_compress_merges_and_clears_backoff(tmp_path):
    from app import memory_server
    from utils import recent_file
    name = "测试角色C"
    snapshot = _history(6)
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    admission_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state[name] = {"compress_backup_fail_attempts": 2}

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=(SystemMessage(content="memo"), "memo"))
    fake_mgr.merge_backup_memo = AsyncMock(return_value="merged")
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(
            name, snapshot, False, admission_generation,
        )

    fake_mgr.merge_backup_memo.assert_awaited_once_with(
        name,
        snapshot,
        SystemMessage(content="memo"),
        expected_generation=admission_generation,
    )
    assert not memory_server.gates._maint_state[name].get("compress_backup_fail_attempts")  # 退避清零
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_backup_failure_does_not_record_or_trim_new_identity(tmp_path):
    from app import memory_server
    from utils import recent_file

    name = "测试角色C"
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    admission_generation = recent_file.capture_recent_generation(recent_path)
    recent_file.activate_recent_paths([recent_path])
    memory_server.gates._maint_state.pop(name, None)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(return_value=None)
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(
            name, _history(6), False, admission_generation,
        )

    assert name not in memory_server.gates._maint_state
    fake_mgr.enforce_hard_cap.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_backup_success_does_not_clear_new_identity_backoff(tmp_path):
    from app import memory_server
    from utils import recent_file

    name = "测试角色C"
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(recent_path)
    recent_file.activate_recent_paths([recent_path])
    new_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": 2,
        "compress_backup_fail_fp": "new-identity-fingerprint",
        "compress_backup_generation": list(new_generation),
    }

    fake_mgr = MagicMock()
    fake_mgr.compress_history = AsyncMock(
        return_value=(SystemMessage(content="stale-memo"), "stale-memo"),
    )
    fake_mgr.merge_backup_memo = AsyncMock(return_value="moot")
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._run_backup_compress(
            name, _history(6), False, old_generation,
        )

    fake_mgr.merge_backup_memo.assert_not_awaited()
    assert memory_server.gates._maint_state[name]["compress_backup_fail_attempts"] == 2
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_deadletter_callback_does_not_trim_new_identity(tmp_path):
    from app import memory_server
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    from memory.recent import build_review_fingerprint
    from utils import recent_file

    name = "测试角色C"
    snapshot = _history(6)
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    admission_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state[name] = {
        "compress_backup_fail_attempts": MEMORY_LIVENESS_MAX_ATTEMPTS,
        "compress_backup_fail_fp": build_review_fingerprint(snapshot),
        "compress_backup_generation": list(admission_generation),
    }
    real_amutate = memory_server.gates._amutate_maint_state

    async def _switch_identity_before_locked_mutation(lanlan_name, mutator):
        recent_file.activate_recent_paths([recent_path])
        return await real_amutate(lanlan_name, mutator)

    fake_mgr = MagicMock()
    fake_mgr.enforce_hard_cap = AsyncMock()
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr), \
         patch.object(
             memory_server.gates,
             "_amutate_maint_state",
             side_effect=_switch_identity_before_locked_mutation,
         ), \
         patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        await memory_server._on_compress_done(
            name,
            snapshot,
            ok=False,
            detailed=False,
            admission_generation=admission_generation,
        )

    assert memory_server.gates._maint_state[name]["compress_backup_fail_attempts"] == (
        MEMORY_LIVENESS_MAX_ATTEMPTS
    )
    fake_mgr.enforce_hard_cap.assert_not_awaited()
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stale_failure_cannot_overwrite_new_generation_backoff(tmp_path):
    from app import memory_server
    from utils import recent_file

    name = "测试角色C"
    recent_path = tmp_path / "recent.json"
    recent_path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(recent_path)
    memory_server.gates._maint_state.pop(name, None)
    old_reached_write = asyncio.Event()
    release_old_write = asyncio.Event()
    real_amutate = memory_server.gates._amutate_maint_state
    calls = 0

    async def _delay_first_write(lanlan_name, mutator):
        nonlocal calls
        calls += 1
        if calls == 1:
            old_reached_write.set()
            await release_old_write.wait()
        return await real_amutate(lanlan_name, mutator)

    with patch.object(
        memory_server.gates, "_amutate_maint_state", side_effect=_delay_first_write,
    ), patch.object(memory_server.gates, "_persist_maint_state_locked", MagicMock()):
        old_record = asyncio.create_task(memory_server._record_compress_backup_failure(
            name, _history(4), old_generation,
        ))
        await asyncio.wait_for(old_reached_write.wait(), timeout=3)
        recent_file.activate_recent_paths([recent_path])
        new_generation = recent_file.capture_recent_generation(recent_path)
        assert await memory_server._record_compress_backup_failure(
            name, _history(6), new_generation,
        ) == 1
        release_old_write.set()
        assert await old_record is None

    state = memory_server.gates._maint_state[name]
    assert state["compress_backup_fail_attempts"] == 1
    assert state["compress_backup_generation"] == list(new_generation)
    memory_server.gates._maint_state.pop(name, None)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reused_identity_replaces_stale_in_flight_backup(tmp_path):
    """A stale name-keyed task must not block the reused identity's fallback."""
    from app import memory_server
    from utils import recent_file

    name = "测试角色C-reused"
    path = tmp_path / "recent.json"
    path.write_text("[]", encoding="utf-8")
    old_generation = recent_file.capture_recent_generation(path)
    recent_file.activate_recent_paths([path])
    new_generation = recent_file.capture_recent_generation(path)

    old_task = asyncio.create_task(asyncio.sleep(30))
    memory_server.compress_backup_tasks[name] = old_task
    memory_server.review.compress_backup_task_generations[name] = old_generation

    async def _slow_compress(*args, **kwargs):
        await asyncio.sleep(30)

    fake_mgr = MagicMock()
    fake_mgr.compress_history = _slow_compress
    with patch.object(memory_server.runtime, "recent_history_manager", fake_mgr):
        await memory_server._on_compress_done(
            name,
            _history(6),
            ok=False,
            detailed=False,
            admission_generation=new_generation,
        )
        new_task = memory_server.compress_backup_tasks[name]
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(old_task, timeout=1)

    assert new_task is not old_task
    assert old_task.cancelled()
    assert memory_server.review.compress_backup_task_generations[name] == new_generation
    await _cleanup_task(new_task)
    memory_server.compress_backup_tasks.pop(name, None)
    memory_server.review.compress_backup_task_generations.pop(name, None)
