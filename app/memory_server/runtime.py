# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runtime state host for the memory_server package.

Owns the FastAPI ``app``, every memory-component singleton (constructed in
``ensure_memory_server_runtime_initialized``, atomically swapped by
``reload_memory_components``), the storage-limited-mode middleware, the
process lifecycle endpoints (/health, /shutdown, /release_character,
/reload, /internal/storage/*, /internal/memory/reset_confirmed_at plus the
startup/shutdown hooks), the background-task registry
(``_spawn_background_task``) and the per-character settle locks.

Mutable state is deliberately co-located with its owners (the
``main_routers/game_router`` runtime pattern). Sibling modules must read it
as ``runtime.<attr>`` module attributes -- never from-import a snapshot:
``ensure_memory_server_runtime_initialized`` / ``reload_memory_components``
rebind the singletons in place. Tests monkeypatch ``runtime.<attr>`` for
the same reason.
"""

import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from memory import (
    CompressedRecentHistoryManager, ImportantSettingsManager, TimeIndexedMemory,
    FactStore, PersonaManager, ReflectionEngine,
)
from memory.cursors import CursorStore
from memory.event_log import EventLog, Reconciler
from memory.evidence_handlers import register_evidence_handlers as _register_evidence_handlers
from memory.outbox import Outbox
from config import (
    EVIDENCE_SIGNAL_CHECK_ENABLED,
    MEMORY_RECHECK_ENABLED,
)
from utils.cloudsave_runtime import (
    MaintenanceModeError,
    ROOT_MODE_NORMAL,
    bootstrap_local_cloudsave_environment,
    is_cloudsave_disabled,
    maintenance_error_payload,
    set_root_mode,
    should_write_root_mode_normal_after_startup,
)
from utils.config_manager import get_config_manager
from utils.root_state_lock import root_state_transaction
from utils.storage_location_bootstrap import get_storage_startup_blocking_reason
from utils.asgi_body_limit import InboundBodySizeLimitMiddleware
from utils.host_origin_guard import HostOriginGuardMiddleware

from . import gates, locale_state
from ._shared import logger, validate_lanlan_name


class ContinueStorageStartupRequest(BaseModel):
    reason: str = ""


app = FastAPI()
_STORAGE_LIMITED_MODE_ALLOWED_PATHS = {
    "/health",
    "/shutdown",
    "/internal/storage/startup/continue",
    "/internal/storage/startup/block",
}


@app.middleware("http")
async def storage_limited_mode_guard(request: Request, call_next):
    if _memory_runtime_init_completed and not _memory_storage_blocked_after_init:
        return await call_next(request)

    if request.url.path in _STORAGE_LIMITED_MODE_ALLOWED_PATHS:
        return await call_next(request)

    blocking_reason = get_storage_startup_blocking_reason(_config_manager)
    if blocking_reason or _memory_storage_blocked_after_init:
        blocking_reason = blocking_reason or "storage_startup_blocked_after_init"
        logger.info(
            "[Memory] limited-mode blocks request path=%r reason=%s",
            request.url.path,
            blocking_reason,
        )
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error_code": "storage_startup_blocked",
                "blocking_reason": blocking_reason,
                "limited_mode": True,
                "error": "Memory server 正处于存储受限启动状态，请等待存储位置选择、迁移或恢复完成。",
            },
        )
    runtime_blocking_reason = "runtime_initializing"
    logger.info(
        "[Memory] limited-mode blocks request path=%r reason=%s",
        request.url.path,
        runtime_blocking_reason,
    )
    return JSONResponse(
        status_code=409,
        content={
            "ok": False,
            "error_code": "storage_startup_blocked",
            "blocking_reason": runtime_blocking_reason,
            "limited_mode": True,
            "error": "Memory server 正处于存储受限启动状态，请等待存储位置选择、迁移或恢复完成。",
        },
    )


# 全局入站 body 体积守门（issue #1586）：与 main_server 对偶，memory_server 的
# 端点（对话缓存 / reflection 记录等）都是小 JSON，统一加上同一守门保持一致。
# agent_server 因 /openfang-llm-proxy 透明转发大 LLM 请求（vision/长上下文 JSON
# 可超 16M）有意不装，见 PR 说明。
app.add_middleware(InboundBodySizeLimitMiddleware)
app.add_middleware(HostOriginGuardMiddleware)


@app.exception_handler(MaintenanceModeError)
async def handle_maintenance_mode_error(_request, exc: MaintenanceModeError):
    return JSONResponse(status_code=409, content=maintenance_error_payload(exc))


# ── 健康检查 / 指纹端点 ──────────────────────────────────────────
@app.get("/health")
async def health():
    """Return a health response carrying the N.E.K.O signature so the launcher/frontend
    can distinguish this service from a random process squatting on the port."""
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response("memory", instance_id=INSTANCE_ID)


# 所有依赖 cloudsave 目录结构的初始化都推迟到 startup 钩子（见 startup_event_handler）：
#   1. bootstrap_local_cloudsave_environment 在磁盘满/只读 FS 等场景会 raise OSError，
#      裸调会让 module import 阶段就崩，FastAPI 根本起不来；
#   2. bootstrap 内部的 import_legacy_runtime_root_if_needed 可能把 legacy 扁平布局的
#      memory/{type}_{name}.ext 文件带进 target root，必须在 migrate_to_character_dirs
#      之前跑（不然 legacy 数据留在扁平布局、components 只认 per-character 布局，数据不可达）；
#   3. 因此 bootstrap → migrate → 组件实例化 三步必须保持顺序且都放在 startup 里。
# Components 先声明为 None，startup hook 赋值。FastAPI 在 startup 钩子 await 完成后
# 才开始接请求，所以 route handler 不会看到 None。
_config_manager = get_config_manager()

recent_history_manager: CompressedRecentHistoryManager | None = None
settings_manager: ImportantSettingsManager | None = None
time_manager: TimeIndexedMemory | None = None
fact_store: FactStore | None = None
persona_manager: PersonaManager | None = None
reflection_engine: ReflectionEngine | None = None
cursor_store: CursorStore | None = None
outbox: Outbox | None = None
# memory-evidence-rfc §3.3 基础设施：EventLog + Reconciler 单例。
# 初始化时机同 persona_manager 等——startup hook 里建，reload 时重建。
event_log: EventLog | None = None
reconciler: Reconciler | None = None

# memory-enhancements P2: vector embedding warmup + backfill worker.
# Lazily constructed in startup hook; held at module scope so
# /process / /renew handlers can call notify_first_process() to
# unblock the warmup wait early. None when vectors are disabled or
# the worker bootstrap raised.
embedding_warmup_worker = None
# memory-enhancements P2: fact vector dedup resolver. Shares the
# FactStore with the embedding worker (worker enqueues candidates,
# the idle-maintenance loop resolves them). None when bootstrap
# fails or the embedding service is permanently disabled.
fact_dedup_resolver = None

# 用于保护重新加载操作的锁
_reload_lock = asyncio.Lock()
_deferred_time_managers: list[TimeIndexedMemory] = []
_memory_runtime_init_lock = asyncio.Lock()
_memory_runtime_init_completed = False
_memory_storage_blocked_after_init = False
_memory_background_tasks_started = False


def _share_subject_forget_state(old_component, new_component) -> None:
    """Keep scoped-erasure fences alive across a component hot reload."""
    if old_component is None:
        return
    for attr in (
        "_subject_forget_generations",
        "_subject_forget_epochs",
        "_active_subject_forgets",
        "_subject_forget_transaction_locks",
    ):
        if hasattr(old_component, attr) and hasattr(new_component, attr):
            setattr(new_component, attr, getattr(old_component, attr))


def _share_fact_store_write_state(old_component, new_component) -> None:
    """Keep full-file fact writers serialized and cache-coherent on reload."""
    if old_component is None:
        return
    for attr in ("_locks", "_locks_guard", "_persist_alocks", "_facts"):
        if hasattr(old_component, attr) and hasattr(new_component, attr):
            setattr(new_component, attr, getattr(old_component, attr))


def _share_reflection_write_locks(old_component, new_component) -> None:
    """Keep reflection and surfaced full-file writers serialized on reload."""
    if old_component is None:
        return
    for attr in ("_alocks", "_alocks_guard"):
        if hasattr(old_component, attr) and hasattr(new_component, attr):
            setattr(new_component, attr, getattr(old_component, attr))


def _share_persona_write_state(old_component, new_component) -> None:
    """Keep persona writes and their cache coherent across a hot reload."""
    if old_component is None:
        return
    for attr in ("_alocks", "_resolve_alocks", "_alocks_guard", "_personas"):
        if hasattr(old_component, attr) and hasattr(new_component, attr):
            setattr(new_component, attr, getattr(old_component, attr))


def _defer_time_manager_cleanup(manager: TimeIndexedMemory | None) -> None:
    """Defer cleanup of the old TimeIndexedMemory until process shutdown, so concurrent requests in the switchover window don't hit a released handle."""
    if manager is None:
        return
    if any(existing is manager for existing in _deferred_time_managers):
        return
    _deferred_time_managers.append(manager)
    logger.info("[MemoryServer] 旧的 TimeIndexedMemory 已加入延迟清理队列")

async def reload_memory_components(
    *,
    resume_derived_task_names: set[str] | None = None,
    resume_derived_task_generations: dict[str, int] | None = None,
    release_derived_task_claims: dict[str, set[str]] | None = None,
):
    """Reload memory component config (used after a new character is created)

    The reload is protected by a lock to guarantee an atomic swap and avoid race
    conditions. All new instances are created first, then the references are swapped
    atomically.

    Note: during reload, async tasks already started by the old cursor_store may
    concurrently read/write the same cursors.json as the new instance. The whole
    architecture assumes a single writer per character; reload is an admin operation
    (character creation) and won't conflict at high frequency with the background
    rebuttal_loop; atomic_write_json guarantees each write is atomic, and in the
    extreme last-writer-wins case at most one cursor advance is lost — the next tick
    recovers it.
    """
    global recent_history_manager, settings_manager, time_manager, fact_store, persona_manager, reflection_engine, cursor_store, outbox, event_log, reconciler, fact_dedup_resolver
    requested_resume_names = set(resume_derived_task_names or ())
    requested_resume_generations = dict(resume_derived_task_generations or {})
    requested_claim_releases = {
        name: set(tokens)
        for name, tokens in (release_derived_task_claims or {}).items()
        if name and tokens
    }
    async with _reload_lock:
        logger.info("[MemoryServer] 开始重新加载记忆组件配置...")
        old_time_manager = time_manager
        try:
            locale_state.invalidate_prompt_locale_caches()
            # 先创建所有新实例
            new_recent = CompressedRecentHistoryManager()
            new_settings = ImportantSettingsManager()
            new_time = TimeIndexedMemory(new_recent)
            new_facts = FactStore(time_indexed_memory=new_time)
            # EventLog 复用（per-character lock dict 没有必要跨 reload 丢弃），
            # 但每次 reload 重建 Reconciler 以便 handlers 指向新 manager 实例。
            new_event_log = event_log if event_log is not None else EventLog()
            new_persona = PersonaManager(event_log=new_event_log)
            new_reflection = ReflectionEngine(new_facts, new_persona, event_log=new_event_log)
            # Requests may have captured the old managers before entering a
            # long out-of-lock LLM call.  Sharing these process-local fences
            # lets a forget on the replacement generation invalidate those
            # late writes and keeps restore/forget mutually exclusive across
            # the swap.
            _share_subject_forget_state(fact_store, new_facts)
            _share_subject_forget_state(reflection_engine, new_reflection)
            _share_fact_store_write_state(fact_store, new_facts)
            _share_reflection_write_locks(reflection_engine, new_reflection)
            _share_persona_write_state(persona_manager, new_persona)
            new_cursor_store = CursorStore()
            new_outbox = Outbox()
            new_reconciler = Reconciler(new_event_log)
            _register_evidence_handlers(new_reconciler, new_persona, new_reflection)
            # P2 step 2: rebind the existing fact_dedup_resolver to the
            # NEW FactStore in place rather than constructing a new
            # resolver. Going via rebind_fact_store preserves the
            # per-character ``_alocks`` dict, so a mid-reload
            # ``aresolve`` still in flight on the old instance and a
            # fresh ``aenqueue_candidates`` arriving on the new
            # instance serialise on the same asyncio.Lock (CodeRabbit
            # PR-956 Major; Codex PR-957 P2). Falls back to fresh
            # construction only if there was no prior resolver
            # (extremely cold-path during reload — startup never ran).
            try:
                from memory.fact_dedup import FactDedupResolver
                if fact_dedup_resolver is not None:
                    fact_dedup_resolver.rebind_fact_store(new_facts)
                    new_fact_dedup_resolver = fact_dedup_resolver
                else:
                    new_fact_dedup_resolver = FactDedupResolver(new_facts)
            except Exception as e:
                logger.warning(f"[MemoryServer] reload: fact_dedup_resolver 重建失败: {e}")
                new_fact_dedup_resolver = None

            # 然后原子性地交换引用
            recent_history_manager = new_recent
            settings_manager = new_settings
            time_manager = new_time
            fact_store = new_facts
            persona_manager = new_persona
            reflection_engine = new_reflection
            cursor_store = new_cursor_store
            outbox = new_outbox
            event_log = new_event_log
            reconciler = new_reconciler
            fact_dedup_resolver = new_fact_dedup_resolver

            if old_time_manager is not None and old_time_manager is not new_time:
                _defer_time_manager_cleanup(old_time_manager)

            # /release_character 会在改名/删除发布前退休旧名的派生任务入口。
            # 只有 reload 真正读到某个名字仍在 characters.json（回滚或后续复用）
            # 才重新开放；成功改名后的旧名在这段窗口里不能被 /process 重生。
            from . import review

            try:
                characters = await asyncio.to_thread(_config_manager.load_characters)
                active_names = set((characters.get("猫娘") or {}).keys())
                await review.reconcile_character_derived_task_admission(
                    active_names,
                    resume_names=requested_resume_names,
                    resume_generations=requested_resume_generations,
                )
            except Exception as reconcile_exc:
                # 组件引用已经完成原子替换；协调失败不能把真实成功的 reload
                # 伪装成失败，调用方据此回滚会与当前已生效实例产生二次分叉。
                logger.warning(
                    "[MemoryServer] reload: 派生任务准入协调失败（组件替换已生效）: %s",
                    reconcile_exc,
                )
            
            logger.info("[MemoryServer] ✅ 记忆组件配置重新加载完成")
            return True
        except Exception as e:
            logger.error(f"[MemoryServer] ❌ 重新加载记忆组件配置失败: {e}", exc_info=True)
            return False
        finally:
            if requested_claim_releases or requested_resume_names:
                # claim 释放与新身份显式 resume 都是 release 事务的收尾，不能
                # 依赖 manager reload 成功。claim 必须按 token 释放，不能误清
                # 同名并发事务仍持有的 publication hold。
                from . import review

                try:
                    for name in sorted(requested_claim_releases):
                        for token in sorted(requested_claim_releases[name]):
                            await review.release_character_derived_task_admission_claim(
                                name,
                                token,
                            )
                    for name in sorted(requested_resume_names):
                        await review.resume_character_derived_task_admission(
                            name,
                            requested_resume_generations.get(name),
                        )
                except Exception as resume_exc:
                    logger.error(
                        "[MemoryServer] reload 收尾恢复派生任务准入失败: names=%s claims=%s err=%s",
                        sorted(requested_resume_names),
                        sorted(requested_claim_releases),
                        resume_exc,
                        exc_info=True,
                    )


@app.post("/release_character/{lanlan_name}")
async def release_character_resources(
    lanlan_name: str,
    hold_derived_task_admission: bool = False,
    derived_task_claim_token: str | None = None,
    derived_task_claim_generation: int | None = None,
):
    """Proactively release the corresponding SQLite handles before a character rename/delete."""
    try:
        lanlan_name = validate_lanlan_name(lanlan_name)
    except HTTPException as exc:
        logger.warning("[MemoryServer] 拒绝释放非法角色名的 SQLite 引擎: %s", lanlan_name)
        return JSONResponse(
            {"status": "error", "character_name": lanlan_name, "message": str(exc.detail)},
            status_code=exc.status_code,
        )

    # 改名/删除前先排空基于旧角色名快照生成的 review / backup-compress。
    # 仅 reload manager 不会清这些按名字注册的 task；让它们在改名后继续提交，
    # 会沿 recent redirect 把旧名字生成的 memo/correction 写进新角色。
    from . import review

    if not derived_task_claim_token or derived_task_claim_generation is None:
        return JSONResponse(
            {
                "status": "error",
                "character_name": lanlan_name,
                "message": "derived task claim token and generation are required",
            },
            status_code=400,
        )

    cancelled_tasks = await review.cancel_character_derived_tasks(
        lanlan_name,
        hold_until_publication=hold_derived_task_admission,
        claim_token=derived_task_claim_token,
        claim_generation=derived_task_claim_generation,
    )
    if cancelled_tasks is None:
        return JSONResponse(
            {
                "status": "cancelled",
                "character_name": lanlan_name,
                "message": "release claim was already withdrawn",
            },
            status_code=409,
        )
    async with _reload_lock:
        try:
            time_manager.dispose_engine(lanlan_name)
            logger.info(
                "[MemoryServer] 已主动释放角色 %s 的 SQLite 引擎并排空 %d 个派生任务",
                lanlan_name,
                cancelled_tasks,
            )
            return {
                "status": "success",
                "character_name": lanlan_name,
                "cancelled_derived_tasks": cancelled_tasks,
                "derived_task_claim_token": derived_task_claim_token,
            }
        except Exception as exc:
            if derived_task_claim_token:
                await review.release_character_derived_task_admission_claim(
                    lanlan_name,
                    derived_task_claim_token,
                )
            logger.warning("[MemoryServer] 释放角色 %s 的 SQLite 引擎失败: %s", lanlan_name, exc)
            return JSONResponse(
                {"status": "error", "character_name": lanlan_name, "message": str(exc)},
                status_code=500,
            )


# 全局变量用于控制服务器关闭
shutdown_event = asyncio.Event()
# 全局变量控制是否响应退出请求
enable_shutdown = False


# 每角色结算锁：首轮摘要期间阻塞 /new_dialog，确保热切换后读到最新数据
_settle_locks: dict[str, asyncio.Lock] = {}


# 强引用注册表：防止 fire-and-forget task 被 GC
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _reset_confirmed_at_for_all_characters() -> int:
    """On→off migration: reset the confirmed_at anchor of every character's confirmed reflections.

    Called by update_powerful_memory_config in main_routers/memory_router.py — only
    runs on the prev=True, new=False transition. Lets the time-driven fallback run
    the full 14-day clock, avoiding the jarring "old confirmed entries get bulk
    promoted immediately after switching off" experience.

    Returns the real number of migrated entries. **Raises on unrecoverable failures
    (reflection_engine not initialized / character list load failure)** so the
    caller endpoint can distinguish "genuinely 0 entries" (characters loaded but
    nothing needed resetting) from "never ran" (early failure). CodeRabbit PR #997
    feedback: previously both early-failure paths returned 0 → the endpoint wrapped
    it as ok=true, count=0 → upstream memory_router misread it as success →
    persisted powerful_memory_enabled=False → old confirmed_at permanently missed
    migration.
    """
    if reflection_engine is None:
        raise RuntimeError(
            "reflection_engine 未初始化（memory_server limited-mode 或 startup 未完成）"
        )
    character_data = await _config_manager.aload_characters()
    catgirl_names = list(character_data.get('猫娘', {}).keys())
    # 角色列表为空（没配过猫娘）是合法的"0 条要迁移" case，正常返回 0。
    total = 0
    for name in catgirl_names:
        try:
            count = await reflection_engine.areset_confirmed_at_to_now(name)
            total += count
        except Exception as e:
            # 单角色失败不致命——记录后继续。最终 count 反映成功的 N 条。
            logger.warning(f"[Memory] migration {name} 重置失败（其他角色继续）: {e}")
    return total


def _get_settle_lock(lanlan_name: str) -> asyncio.Lock:
    """Get the settle lock for the given character (lazily created)"""
    if lanlan_name not in _settle_locks:
        _settle_locks[lanlan_name] = asyncio.Lock()
    return _settle_locks[lanlan_name]


def _spawn_background_task(coro) -> asyncio.Task:
    """Create a background task with strong reference + exception logging."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)

    def _on_done(t: asyncio.Task):
        _BACKGROUND_TASKS.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                logger.warning(f"[MemoryServer] 后台任务异常: {exc}")

    task.add_done_callback(_on_done)
    return task


@app.post("/shutdown")
async def shutdown_memory_server():
    """Receive the shutdown signal from main_server"""
    global enable_shutdown
    if not enable_shutdown:
        logger.warning("收到关闭信号，但当前模式不允许响应退出请求")
        return {"status": "shutdown_disabled", "message": "当前模式不允许响应退出请求"}
    
    try:
        logger.info("收到来自main_server的关闭信号")
        shutdown_event.set()
        return {"status": "shutdown_signal_received"}
    except Exception as e:
        logger.error(f"处理关闭信号时出错: {e}")
        return {"status": "error", "message": str(e)}


async def _bootstrap_embedding_worker() -> None:
    """Bootstrap the vector warmup / dedup worker in the background after ready.

    The heavy import (``memory.embedding_worker`` pulls in the embedding stack
    ~0.6s) and service construction (``get_embedding_service()`` may probe/load a
    model) all run inside ``to_thread`` without blocking the event loop;
    ``start()`` is lightweight (just ``create_task``) and is called back on the
    loop. The worker has its own warmup delay and degrades gracefully when vectors
    are unavailable, so moving it off the memory startup critical path has zero
    impact on greeting.

    ⚠️ Deliberately does **not** pass the manager as a parameter: the worker's
    getters (``lambda: persona_manager`` etc.) must resolve to the module globals,
    so that after /reload rebinds the globals the next sweep sees the new
    instances. Passing parameters would let the closure capture the startup-era
    old instances, bypassing the worker's designed reload-staleness protection.
    """
    global embedding_warmup_worker
    try:
        def _build():
            from memory.embedding_worker import EmbeddingWarmupWorker
            from config import VECTORS_WARMUP_DELAY_SECONDS

            def _current_catgirl_names() -> list[str]:
                try:
                    data = _config_manager.load_characters()
                    return list((data or {}).get('猫娘', {}).keys())
                except Exception:
                    return []

            worker = EmbeddingWarmupWorker(
                get_persona_manager=lambda: persona_manager,
                get_reflection_engine=lambda: reflection_engine,
                get_fact_store=lambda: fact_store,
                get_character_names=_current_catgirl_names,
                warmup_delay_seconds=VECTORS_WARMUP_DELAY_SECONDS,
                get_dedup_resolver=lambda: fact_dedup_resolver,
                # The same barrier brackets reload and scoped forget. A sweep
                # that captured old managers must finish saving before either
                # operation can erase through replacement instances.
                get_sweep_barrier=lambda: _reload_lock,
            )
            return worker

        worker = await asyncio.to_thread(_build)
        # worker 与 resolver 都用 getter 读全局，天然 reload-safe。
        embedding_warmup_worker = worker
        embedding_warmup_worker.start()
    except Exception as e:
        logger.warning(f"[Memory] embedding worker bootstrap failed: {e}")
        embedding_warmup_worker = None
        # Resolver 已在核心初始化中发布；可选 worker 失败不得影响删除队列。


async def ensure_memory_server_runtime_initialized(*, reason: str = "") -> bool:
    from . import evidence_loops, outbox_infra, refine_loops, routes, signal_extraction

    global recent_history_manager, settings_manager, time_manager, fact_store
    global persona_manager, reflection_engine, cursor_store, outbox, event_log, reconciler
    global embedding_warmup_worker, fact_dedup_resolver
    global _memory_runtime_init_completed, _memory_background_tasks_started

    if _memory_runtime_init_completed:
        return False

    async with _memory_runtime_init_lock:
        if _memory_runtime_init_completed:
            return False

        bootstrap_ok = False
        if is_cloudsave_disabled():
            logger.warning("[Memory] 跳过 cloudsave 环境 bootstrap：cloudsave 已为本次会话禁用")
        else:
            try:
                bootstrap_local_cloudsave_environment(_config_manager)
                bootstrap_ok = True
            except Exception as e:
                logger.warning(f"[Memory] cloudsave 环境 bootstrap 失败，后续 cloudsave 相关操作可能降级: {e}")

        try:
            from memory import migrate_to_character_dirs

            _config_manager.ensure_memory_directory()
            _char_data = await _config_manager.aload_characters()
            _catgirl_names = list(_char_data.get('猫娘', {}).keys())
            await asyncio.to_thread(migrate_to_character_dirs, _config_manager.memory_dir, _catgirl_names)
        except Exception as _e:
            logger.warning(f"[Memory] 目录迁移失败: {_e}")

        recent_history_manager = CompressedRecentHistoryManager()
        settings_manager = ImportantSettingsManager()
        time_manager = TimeIndexedMemory(recent_history_manager)
        fact_store = FactStore(time_indexed_memory=time_manager)
        # Queue erasure is part of the privacy runtime, not the optional
        # vector worker. Construct the lightweight resolver before ready so
        # scoped_forget remains available while embedding bootstrap is still
        # running or when that best-effort bootstrap fails.
        from memory.fact_dedup import FactDedupResolver
        fact_dedup_resolver = FactDedupResolver(fact_store)
        event_log = EventLog()
        persona_manager = PersonaManager(event_log=event_log)
        reflection_engine = ReflectionEngine(fact_store, persona_manager, event_log=event_log)
        cursor_store = CursorStore()
        outbox = Outbox()
        reconciler = Reconciler(event_log)
        _register_evidence_handlers(reconciler, persona_manager, reflection_engine)

        try:
            from utils.token_tracker import TokenTracker, install_hooks

            install_hooks()
            TokenTracker.get_instance().start_periodic_save()
            # process 字段进 session_start / session_end 维度，跨进程诊断必须区分
            TokenTracker.get_instance().record_app_start(process="memory_server")
        except Exception as e:
            logger.warning(f"[Memory] Token tracker init failed: {e}")

        # GeoIP 预热：memory_server 是独立进程，主进程的预热只暖主进程的类级缓存。
        # 不预热的话，下面 outbox 补跑 / 首个记忆更新的免费路由 LLM 调用会读到临时
        # 大陆快照（本进程的探测那时才刚起）。放在 cloudsave bootstrap 之后——与
        # main_server 同一时序原则：配置成型后再预热。自配 API 用户零等待（不起探
        # 测，快照无区域敏感 URL 时直接返回 True）。
        try:
            if not await _config_manager.awarmup_region_check():
                logger.info("[GeoIP] memory_server 预热未拿到区域结论，后续调用按退避重试")
        except Exception:
            logger.debug("[GeoIP] memory_server 预热失败，留给后续调用重试", exc_info=True)

        await gates._aload_maint_state()

        # Speaker-trust pool. Must come after the cloudsave bootstrap and
        # ``ensure_memory_directory`` above, because ``pool_path()`` reads
        # ``memory_dir``. It is a MODULE-LEVEL singleton in ``memory.trust_store``
        # and deliberately not hung off the runtime globals, so
        # ``reload_memory_components()`` does not touch it and no
        # ``_share_trust_write_state`` shim is needed (contrast
        # ``_share_fact_store_write_state``).
        from memory import trust_store
        await trust_store.aload_pool()

        catgirl_names: list[str] = []
        try:
            character_data = await _config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
            if catgirl_names:
                results = await asyncio.gather(
                    *(persona_manager.aensure_persona(n) for n in catgirl_names),
                    return_exceptions=True,
                )
                for name, result in zip(catgirl_names, results):
                    if isinstance(result, Exception):
                        logger.warning(
                            f"[Memory] Persona 迁移检查失败: {name}: {result}",
                            exc_info=result,
                        )
            logger.info(f"[Memory] Persona 迁移检查完成，角色数: {len(catgirl_names)}")
        except Exception as e:
            logger.warning(f"[Memory] Persona 迁移检查失败: {e}")

        async def _reconcile_one(n: str):
            try:
                applied = await reconciler.areconcile(n)
                if applied:
                    logger.info(f"[Memory] reconciler {n}: 重放 {applied} 条事件")
            except Exception as e:
                logger.warning(f"[Memory] reconciler {n} replay 失败: {e}")

        # 顺序要求：reconcile 必须整体跑完，才允许 outbox 补跑起任何 op。
        # 两边都写同一批 view 文件，但它们的读点不对称：reconcile 的 handler
        # 是在 EventLog 锁内 load→改→save 的，而 reflection/persona 的 live
        # writer 是在锁**外**先把整份快照读进内存，再交给 record_and_save 写回
        # （见 memory/reflection/evidence_flow.py）。所以只要两者重叠，就存在
        # 「live writer 用重放之前的快照整覆盖掉刚修好的结果」的窗口 —— 而哨兵
        # 此时已经越过那条事件，之后再也不会重放，是静默永久丢失。
        # 补跑侧是 fire-and-forget 的后台 task（_replay_pending_outbox 只 await
        # 扫描），reconcile 这边是 await 到底的：先跑 reconcile 就没有重叠。
        # 反向依赖不存在：补跑的 op 走 record_and_save，append+apply+save 一体，
        # 不需要 reconcile 再补应用一次；RFC 也明写了「不得依赖 outbox 副作用先
        # 可见」（docs/design/memory-event-log-rfc.md 启动恢复一节）。
        # 顺带的好处：补跑 op 读到的是已经修复完的 view，而不是崩溃残留态。
        if catgirl_names:
            await asyncio.gather(
                *(_reconcile_one(n) for n in catgirl_names),
                return_exceptions=True,
            )

        try:
            await outbox_infra._replay_pending_outbox()
        except Exception as e:
            logger.warning(f"[Outbox] 启动补跑顶层失败: {e}")

        async def _migrate_one(n: str):
            try:
                await evidence_loops._aone_shot_migration_if_needed(n)
            except Exception as e:
                logger.warning(f"[Memory] {n} evidence 迁移失败: {e}")
            try:
                await evidence_loops._aone_shot_archive_migration_if_needed(n)
            except Exception as e:
                logger.warning(f"[Memory] {n} archive 迁移失败: {e}")

        if catgirl_names:
            await asyncio.gather(
                *(_migrate_one(n) for n in catgirl_names),
                return_exceptions=True,
            )

        if bootstrap_ok:
            # ⚠️ 判定和写必须在同一个锁内事务里。它们以前靠"中间没有 await"隐式原子，
            # 但那只挡得住同一条事件循环上的协程 —— merged 模式下存储变更路由跟这段同
            # 进程，而它的写现在跑在工作线程上，完全可以插在判定和写之间提交
            # ROOT_MODE_MAINTENANCE_READONLY，随后被这里无条件写回 NORMAL，留下一个
            # 没有写闸的待迁移。
            with root_state_transaction():
                current_root_state = _config_manager.load_root_state()
                if should_write_root_mode_normal_after_startup(current_root_state):
                    try:
                        set_root_mode(
                            _config_manager,
                            ROOT_MODE_NORMAL,
                            current_root=str(_config_manager.app_docs_dir),
                            last_known_good_root=str(_config_manager.app_docs_dir),
                            last_successful_boot_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        )
                    except Exception as e:
                        logger.warning(f"[Memory] 写入启动成功标记失败: {e}")
                else:
                    logger.info(
                        "[Memory] 跳过 ROOT_MODE_NORMAL 写入，当前仍处于阻断态: %s",
                        current_root_state.get("mode") or ROOT_MODE_NORMAL,
                    )
        else:
            logger.warning("[Memory] 跳过 ROOT_MODE_NORMAL 写入：cloudsave bootstrap 未成功")

        if not _memory_background_tasks_started:
            _spawn_background_task(evidence_loops._periodic_rebuttal_loop())
            _spawn_background_task(evidence_loops._periodic_auto_promote_loop())
            _spawn_background_task(evidence_loops._periodic_idle_maintenance_loop())
            if EVIDENCE_SIGNAL_CHECK_ENABLED:
                _spawn_background_task(signal_extraction._periodic_signal_extraction_loop())
            _spawn_background_task(evidence_loops._periodic_archive_sweep_loop())
            _spawn_background_task(routes._periodic_new_dialog_qps_log_loop())
            if MEMORY_RECHECK_ENABLED:
                _spawn_background_task(evidence_loops._periodic_slow_memory_recheck_loop())
            # Phase A-4 / A-5: MemoryRefineEngine cron 接入
            _spawn_background_task(refine_loops._periodic_persona_refine_loop())
            _spawn_background_task(refine_loops._periodic_reflection_refine_loop())
            _spawn_background_task(refine_loops._periodic_reflection_synthesis_loop())
            # 群记忆系列 5/7: scoped 轻量 refine cron
            _spawn_background_task(refine_loops._periodic_scoped_refine_loop())
            _memory_background_tasks_started = True

        # memory-enhancements P2: vector embedding warmup + backfill worker.
        # 这块的 import（embedding 栈 ~0.6s）+ 服务构造原本同步跑在 startup
        # handler 里，uvicorn 要等 handler 返回才开端口，于是把 memory 端口
        # 就绪足足推后 ~1.3s（合并单进程下又被串行放大）。worker 本身是可选的、
        # 自带 warmup 延迟，greeting 不依赖向量——所以挪到后台 task，重活全程
        # 在 to_thread 里跑，绝不阻塞 event loop / 拖慢端口就绪。
        _spawn_background_task(_bootstrap_embedding_worker())

        _memory_runtime_init_completed = True
        logger.info("[Memory] 运行态初始化完成 (reason=%s)", reason or "manual")
        return True


@app.on_event("startup")
async def startup_event_handler():
    """Initialization at application startup"""
    blocking_reason = get_storage_startup_blocking_reason(_config_manager)
    if blocking_reason:
        logger.info(
            "[Memory] 检测到存储启动阻断态，先保持 limited-mode，等待网页端放行: %s",
            blocking_reason,
        )
        return

    await ensure_memory_server_runtime_initialized(reason="startup")


@app.post("/internal/storage/startup/continue")
async def continue_storage_startup(payload: ContinueStorageStartupRequest | None = None):
    global _memory_storage_blocked_after_init
    blocking_reason = get_storage_startup_blocking_reason(_config_manager)
    if blocking_reason:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error_code": "storage_startup_blocked",
                "blocking_reason": blocking_reason,
                "error": "当前存储状态仍需选择、迁移或恢复，暂时不能释放 memory server 启动闸门。",
            },
        )

    try:
        initialized = await ensure_memory_server_runtime_initialized(
            reason=str(getattr(payload, "reason", "") or "storage_selection_continue_current_session"),
        )
        _memory_storage_blocked_after_init = False
        return {
            "ok": True,
            "initialized": bool(initialized),
        }
    except Exception as e:
        logger.error(f"[Memory] 释放 limited-mode 启动失败: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
            },
        )


@app.post("/internal/storage/startup/block")
async def block_storage_startup(payload: ContinueStorageStartupRequest | None = None):
    global _memory_storage_blocked_after_init
    reason = str(getattr(payload, "reason", "") or "").strip()
    _memory_storage_blocked_after_init = True
    logger.warning("[Memory] limited-mode restored after main_server startup failure: %s", reason or "-")
    return {
        "ok": True,
        "limited_mode": True,
        "reason": reason,
    }


@app.post("/internal/memory/reset_confirmed_at")
async def internal_reset_confirmed_at():
    """Powerful-memory ON→OFF migration: reset the confirmed_at anchor of every
    character's confirmed reflections to now.

    main_routers/memory_router.py triggers this endpoint over HTTP — the helper
    ``_reset_confirmed_at_for_all_characters`` depends on this process's
    ``reflection_engine`` global, and must run inside the memory_server process to
    get the correct instance (the main_server process can import the
    memory_server module, but that's a fresh copy where ``reflection_engine`` is
    None, making the call a no-op).
    """
    try:
        count = await _reset_confirmed_at_for_all_characters()
        return {"ok": True, "count": count}
    except Exception as e:
        logger.warning(f"[Memory] reset_confirmed_at migration 失败: {e}")
        return {"ok": False, "error": str(e), "count": 0}


@app.on_event("shutdown")
async def shutdown_event_handler():
    """Cleanup at application shutdown"""
    logger.info("Memory server正在关闭...")
    try:
        from utils.token_tracker import TokenTracker
        TokenTracker.get_instance().save()
    except Exception:
        # Best-effort final flush — the shutdown path must never fail on
        # tracker IO, and the periodic save loop already persisted recent data.
        pass
    # P2 vector worker: kick off stop() as a task before we touch the
    # reload lock so its bounded 2s wait overlaps with manager cleanup
    # below instead of serializing in front of it.
    worker_stop_task: asyncio.Task | None = None
    if embedding_warmup_worker is not None:
        worker_stop_task = asyncio.create_task(embedding_warmup_worker.stop())

    managers_to_cleanup: list[TimeIndexedMemory] = []
    async with _reload_lock:
        managers_to_cleanup.extend(_deferred_time_managers)
        _deferred_time_managers.clear()
        # time_manager 在 startup 钩子里才实例化；若启动过程中就触发 shutdown 可能为 None
        if time_manager is not None and all(existing is not time_manager for existing in managers_to_cleanup):
            managers_to_cleanup.append(time_manager)

    async def _cleanup_one(m: TimeIndexedMemory) -> None:
        try:
            await asyncio.to_thread(m.cleanup)
        except Exception as cleanup_exc:
            logger.warning("[MemoryServer] 延迟释放 SQLite 引擎失败: %s", cleanup_exc)

    async def _await_worker_stop() -> None:
        try:
            await worker_stop_task  # type: ignore[arg-type]
        except Exception as e:
            logger.warning(f"[Memory] embedding worker stop 失败: {e}")

    shutdown_coros: list = [_cleanup_one(m) for m in managers_to_cleanup]
    if worker_stop_task is not None:
        shutdown_coros.append(_await_worker_stop())
    if shutdown_coros:
        await asyncio.gather(*shutdown_coros)
    # The worker is stopped before releasing the process-scoped singleton, so
    # no background inference can retain or race the ONNX/tokenizer instances.
    try:
        from memory.embeddings import release_embedding_service

        await release_embedding_service()
    except Exception as e:
        logger.warning("[Memory] embedding service release 失败: %s", e)
    logger.info("Memory server已关闭")


@app.post("/reload")
async def reload_config(request: Request):
    """Reload the memory server config (used after a new character is created)"""
    try:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        raw_resume_names = payload.get("resume_derived_task_names", []) if isinstance(payload, dict) else []
        resume_names = {
            name for name in raw_resume_names
            if isinstance(name, str) and name
        } if isinstance(raw_resume_names, list) else set()
        raw_resume_generations = (
            payload.get("resume_derived_task_generations", {})
            if isinstance(payload, dict)
            else {}
        )
        resume_generations = {
            name: generation
            for name, generation in raw_resume_generations.items()
            if (
                isinstance(name, str)
                and name
                and isinstance(generation, int)
                and generation >= 0
            )
        } if isinstance(raw_resume_generations, dict) else {}
        raw_claim_releases = (
            payload.get("release_derived_task_claims", {})
            if isinstance(payload, dict)
            else {}
        )
        claim_releases = {
            name: {
                token for token in tokens
                if isinstance(token, str) and token
            }
            for name, tokens in raw_claim_releases.items()
            if (
                isinstance(name, str)
                and name
                and isinstance(tokens, list)
            )
        } if isinstance(raw_claim_releases, dict) else {}
        success = await reload_memory_components(
            resume_derived_task_names=resume_names,
            resume_derived_task_generations=resume_generations,
            release_derived_task_claims=claim_releases,
        )
        if success:
            return {"status": "success", "message": "配置已重新加载"}
        else:
            return {"status": "error", "message": "配置重新加载失败"}
    except Exception as e:
        logger.error(f"重新加载配置时出错: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}
