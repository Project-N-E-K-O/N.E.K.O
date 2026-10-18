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
from contextvars import ContextVar, Token
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
# Character write endpoints -> allowed methods, driving fencing/draining. Method matters:
# /prompt-locale/{name} GET reads only, while PUT writes.
_CHARACTER_WRITE_ROUTES: tuple[tuple[str, frozenset[str]], ...] = (
    ("/cache/", frozenset({"POST"})),
    ("/process/", frozenset({"POST"})),
    ("/renew/", frozenset({"POST"})),
    ("/settle/", frozenset({"POST"})),
    # Reflection synthesis and surfaced cooldown registration also write reflections.json /
    # surfaced.json under character directories; late post-deletion writes recreate old identities.
    ("/reflect/", frozenset({"POST"})),
    ("/record_surfaced/", frozenset({"POST"})),
    # Only PUT writes; GET reads sidecars and bypasses fencing.
    # Callers already treat 409 as retryable (cloud-save maintenance fences also return it).
    ("/prompt-locale/", frozenset({"PUT"})),
)
# Group-memory writes use /internal/memory/{lanlan_name}/<op>, ultimately writing the character's
# FactStore / TimeIndexedMemory. Missing fences neither reject nor drain these requests during deletion,
# letting checked-out SQLite connections survive disposal.
# scoped_context is read-only and excluded; engine admission protects reads.
_CHARACTER_SCOPED_WRITE_PATH_PREFIX = "/internal/memory/"
_CHARACTER_SCOPED_WRITE_OPS = frozenset({
    "scoped_facts",
    "scoped_history",
    "scoped_forget",
    "scoped_mentions",
    # Visit remember-as-diary fact writes (OD-16 v4).
    "visit_facts",
    # Theater forgetting changes recent and time indexes; it must share the fence during character deletion.
    "theater/forget",
    # Retracting a declined archive capsule writes recent and the time index too.
    "theater/retract",
})
_admitted_character_context: ContextVar[frozenset[str]] = ContextVar(
    "admitted_character_context",
    default=frozenset(),
)
_active_character_requests: dict[str, int] = {}
_active_character_request_events: dict[str, asyncio.Event] = {}
# Drain budget must be below the **tightest** caller timeout, or callers abandon and delete files
# while this handler still drains/disposes, reproducing the Windows handle failure this PR fixes.
# Tightest is unsubscribe: _release_workshop_character_handles per_call_timeout=2.5s
# (rename/delete use release_memory_server_character(), 5s).
_CHARACTER_REQUEST_DRAIN_TIMEOUT_SECONDS = 2.0
_STORAGE_LIMITED_MODE_ALLOWED_PATHS = {
    "/health",
    "/shutdown",
    "/internal/storage/startup/continue",
    "/internal/storage/startup/block",
}


def _character_write_name_from_path(path: str, method: str) -> str | None:
    raw_name = None
    method = (method or "").upper()
    for prefix, methods in _CHARACTER_WRITE_ROUTES:
        if path.startswith(prefix):
            candidate = path[len(prefix):]
            if method in methods and candidate and "/" not in candidate:
                raw_name = candidate
            break
    if (
        raw_name is None
        and method == "POST"
        and path.startswith(_CHARACTER_SCOPED_WRITE_PATH_PREFIX)
    ):
        segments = path[len(_CHARACTER_SCOPED_WRITE_PATH_PREFIX):].split("/")
        # Operation names may contain fixed subpaths but still match complete-string allowlists exactly.
        operation = "/".join(segments[1:])
        if len(segments) >= 2 and operation in _CHARACTER_SCOPED_WRITE_OPS:
            raw_name = segments[0]
    if not raw_name:
        return None
    # Endpoints normalize via validate_lanlan_name before character I/O; admission must use
    # the same key, or /process/ Alice evades all fences except the spaced name,
    # and request leases mismatch the engine's actual Alice key.
    try:
        return validate_lanlan_name(raw_name)
    except HTTPException:
        return None


def _begin_character_request(
    lanlan_name: str,
) -> Token[frozenset[str]] | None:
    """Atomically admit one foreground request before its first await."""
    if not _is_character_publication_admitted(lanlan_name):
        return None
    _active_character_requests[lanlan_name] = (
        _active_character_requests.get(lanlan_name, 0) + 1
    )
    return _admitted_character_context.set(
        _admitted_character_context.get() | {lanlan_name}
    )


def _end_character_request(
    lanlan_name: str,
    context_token: Token[frozenset[str]],
) -> None:
    _admitted_character_context.reset(context_token)
    remaining = _active_character_requests.get(lanlan_name, 0) - 1
    if remaining > 0:
        _active_character_requests[lanlan_name] = remaining
        return
    _active_character_requests.pop(lanlan_name, None)
    event = _active_character_request_events.pop(lanlan_name, None)
    if event is not None:
        event.set()


async def _acquire_within_deadline(lock: asyncio.Lock, deadline: float) -> bool:
    """Take one lock without outliving the release budget.

    ``/new_dialog`` and the background review hold the per-character settle lock
    across whole prompt assemblies, and they are deliberately not fenced. An
    unbounded acquire here would keep this handler running long after the caller
    gave up, so a lock that cannot be taken in time fails the release instead.
    """
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        return False
    try:
        await asyncio.wait_for(lock.acquire(), timeout=remaining)
    except asyncio.TimeoutError:
        return False
    return True


async def _wait_for_character_requests(
    lanlan_name: str,
    deadline: float | None = None,
) -> bool:
    """Drain admitted foreground writes; report whether they all finished.

    The caller (``release_memory_server_character``) gives up after 5s and
    compensates by reopening admission. An unbounded wait here would keep this
    handler running past that point and dispose engines behind the caller's
    back, so the drain stays strictly inside the caller's window.
    """
    loop = asyncio.get_running_loop()
    if deadline is None:
        deadline = loop.time() + _CHARACTER_REQUEST_DRAIN_TIMEOUT_SECONDS
    while _active_character_requests.get(lanlan_name, 0):
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        event = _active_character_request_events.setdefault(
            lanlan_name,
            asyncio.Event(),
        )
        event.clear()
        if not _active_character_requests.get(lanlan_name, 0):
            break
        try:
            await asyncio.wait_for(event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            return False
    return True


@app.middleware("http")
async def character_publication_guard(request: Request, call_next):
    lanlan_name = _character_write_name_from_path(
        request.url.path, request.method,
    )
    if lanlan_name is None:
        return await call_next(request)
    context_token = _begin_character_request(lanlan_name)
    if context_token is None:
        return JSONResponse(
            {"status": "cancelled", "message": "character release in progress"},
            status_code=409,
        )
    try:
        return await call_next(request)
    finally:
        _end_character_request(lanlan_name, context_token)


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


# Global inbound-body guard (issue #1586): mirror main_server; memory_server endpoints
# (chat cache / reflections, etc.) use small JSON, so apply the same consistent guard.
app.add_middleware(InboundBodySizeLimitMiddleware)
from utils.instance_access import InstanceAccessMiddleware
app.add_middleware(InstanceAccessMiddleware)
app.add_middleware(HostOriginGuardMiddleware)


@app.exception_handler(MaintenanceModeError)
async def handle_maintenance_mode_error(_request, exc: MaintenanceModeError):
    return JSONResponse(status_code=409, content=maintenance_error_payload(exc))


# ── Health / fingerprint endpoints ──────────────────────────────────────────
@app.get("/health")
async def health():
    """Return a health response carrying the N.E.K.O signature so the launcher/frontend
    can distinguish this service from a random process squatting on the port."""
    from utils.port_utils import build_health_response
    from config import INSTANCE_ID
    return build_health_response("memory", instance_id=INSTANCE_ID)


# Defer initialization depending on cloud-save directories to startup (see startup_event_handler):
#   1. bootstrap_local_cloudsave_environment may raise OSError on full/read-only disks;
#      unguarded calls fail module import, preventing FastAPI startup entirely.
#   2. Bootstrap's import_legacy_runtime_root_if_needed may bring flat legacy
#      memory/{type}_{name}.ext into the target root; run before migrate_to_character_dirs,
#      or legacy data remains flat and unreachable to per-character components.
#   3. Preserve startup order: bootstrap -> migrate -> component instantiation.
# Declare components None, then assign in startup. FastAPI accepts requests only after
# awaited startup completes, so handlers never observe None.
_config_manager = get_config_manager()

recent_history_manager: CompressedRecentHistoryManager | None = None
settings_manager: ImportantSettingsManager | None = None
time_manager: TimeIndexedMemory | None = None
fact_store: FactStore | None = None
persona_manager: PersonaManager | None = None
reflection_engine: ReflectionEngine | None = None
cursor_store: CursorStore | None = None
outbox: Outbox | None = None
# memory-evidence-rfc §3.3 infrastructure: EventLog + Reconciler singletons.
# Like persona_manager, construct in startup and rebuild on reload.
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

# Lock protecting reloads.
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


def _is_character_publication_admitted(lanlan_name: str) -> bool:
    """Keep a released identity from reopening storage before publication."""
    from . import review

    return not review.is_character_publication_held(lanlan_name)


def _is_character_engine_admitted(lanlan_name: str) -> bool:
    """Allow a request admitted before the publication hold to finish."""
    return (
        lanlan_name in _admitted_character_context.get()
        or _is_character_publication_admitted(lanlan_name)
    )


def _dispose_character_engines_across_generations(
    lanlan_name: str,
) -> tuple[int, int]:
    """Release one character from the current and every deferred manager.

    Hot reload deliberately keeps old managers alive for requests that already
    captured them.  A character rename/delete must nevertheless close that
    character's idle SQLite pools in every retained generation before touching
    its files.  Other characters and the deferred-manager lifecycle are left
    unchanged.
    """
    managers: list[TimeIndexedMemory] = []
    seen: set[int] = set()
    for manager in (time_manager, *_deferred_time_managers):
        if manager is None or id(manager) in seen:
            continue
        seen.add(id(manager))
        managers.append(manager)

    released_count = 0
    errors: list[tuple[int, Exception]] = []
    for index, manager in enumerate(managers):
        try:
            if manager.dispose_engine(lanlan_name, retain_on_failure=True):
                released_count += 1
        except Exception as exc:
            # Continue so one broken generation cannot hide the manager that
            # actually owns the Windows file handle.
            errors.append((index, exc))

    if errors:
        summary = ", ".join(
            f"manager[{index}]={type(exc).__name__}: {exc}"
            for index, exc in errors
        )
        raise RuntimeError(
            f"failed to dispose character engines: {summary}"
        ) from errors[0][1]

    return len(managers), released_count


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
            # Create all replacement instances first.
            new_recent = CompressedRecentHistoryManager()
            new_settings = ImportantSettingsManager()
            new_time = TimeIndexedMemory(
                new_recent,
                engine_admission_check=_is_character_engine_admitted,
            )
            new_facts = FactStore(time_indexed_memory=new_time)
            # Reuse EventLog (retain per-character lock dict across reload),
            # but rebuild Reconciler so handlers point to new managers.
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
                # Reverse reference: Stage-2 FTS near-duplicates enter the same arbitration queue.
                # Rebind updates only resolver->store; the new FactStore is a fresh object,
                # so missing reverse wiring silently discards near-duplicate candidates after reload.
                new_facts.attach_dedup_resolver(new_fact_dedup_resolver)
            except Exception as e:
                logger.warning(f"[MemoryServer] reload: fact_dedup_resolver 重建失败: {e}")
                new_fact_dedup_resolver = None

            # Then swap references atomically.
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

            # /release_character retires old-name derived-task entry points before rename/delete publication.
            # Reopen only when reload actually sees a name in characters.json (rollback or later reuse);
            # /process cannot resurrect successfully renamed old names in this window.
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
                # Component references already swapped atomically; coordination failures cannot disguise
                # successful reload as failure, prompting caller rollback and a second divergence from active instances.
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
                # Claim release and explicit new-identity resume finalize release transactions and cannot
                # depend on manager reload success. Release by token, preserving same-name concurrent
                # transactions' publication holds.
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

    # Before rename/delete, drain review / backup-compress generated from old-name snapshots.
    # Manager reload alone does not clear these name-registered tasks; later commits would follow
    # recent redirects and write old-persona memo/corrections to the new character.
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
    # The publication hold above prevents queued/new work from reopening the
    # engine. Requests admitted before the hold keep their request-local lease;
    # drain them and their spawned post-turn tasks before disposing any manager.
    from . import post_turn

    # One budget for the whole release: drain plus both lock waits must fit caller
    # timeouts, or abandoned/compensated calls still dispose in the background.
    deadline = (
        asyncio.get_running_loop().time()
        + _CHARACTER_REQUEST_DRAIN_TIMEOUT_SECONDS
    )
    # Draining and post-turn cancellation both require publication hold blocking new admission.
    # Callers without holds (cloud downloads, unsubscribe) leave characters alive; draining cannot
    # reach zero amid new requests, and cancellation cannot prevent new tasks a millisecond later.
    # Net effect only interrupts in-flight outbox operations. Preserve pre-PR semantics for both paths.
    holding_publication = review.is_character_publication_held(lanlan_name)
    cancelled_post_turn_tasks = 0
    drained = True
    if holding_publication:
        # Post-turn tasks lack foreground leases and still write fact / mention / reflection for old
        # names during foreground drain. Cancel registered tasks first, drain foreground requests,
        # then cancel again because admitted requests can spawn tasks during draining.
        cancelled_post_turn_tasks = await post_turn.cancel_character_post_turn_tasks(
            lanlan_name
        )
        drained = await _wait_for_character_requests(lanlan_name, deadline)
        cancelled_post_turn_tasks += (
            await post_turn.cancel_character_post_turn_tasks(lanlan_name)
        )

    async def _fail_release(message: str, status_code: int):
        await review.release_character_derived_task_admission_claim(
            lanlan_name,
            derived_task_claim_token,
        )
        logger.warning("[MemoryServer] 释放角色 %s 中止：%s", lanlan_name, message)
        return JSONResponse(
            {
                "status": "error",
                "character_name": lanlan_name,
                "message": message,
            },
            status_code=status_code,
        )

    if not drained:
        return await _fail_release(
            "timed out draining admitted character writes", 503,
        )
    # Bind the same object: _get_settle_lock is a function; separate acquire/release calls risk
    # different instances (tests sometimes patch it to return fresh locks).
    settle_lock = _get_settle_lock(lanlan_name)
    if not await _acquire_within_deadline(settle_lock, deadline):
        return await _fail_release("timed out acquiring the settle lock", 503)
    try:
        if not await _acquire_within_deadline(_reload_lock, deadline):
            return await _fail_release("timed out acquiring the reload lock", 503)
        try:
            # Drain/lock waits may exceed caller timeout, after main-process compensation releases
            # this claim and reopens admission; do not secretly dispose afterward.
            if not review.is_character_derived_task_claim_active(
                lanlan_name, derived_task_claim_token,
            ):
                logger.warning(
                    "[MemoryServer] 角色 %s 的释放声明已被撤回，跳过 dispose",
                    lanlan_name,
                )
                return JSONResponse(
                    {
                        "status": "cancelled",
                        "character_name": lanlan_name,
                        "message": "release claim was withdrawn while draining",
                    },
                    status_code=409,
                )
            try:
                scanned_managers, released_managers = (
                    _dispose_character_engines_across_generations(lanlan_name)
                )
                logger.info(
                    "[MemoryServer] 已扫描角色 %s 的 %d 个 TimeIndexedMemory 实例，"
                    "实际释放 %d 个 SQLite 引擎并排空 %d 个派生任务",
                    lanlan_name,
                    scanned_managers,
                    released_managers,
                    cancelled_tasks + cancelled_post_turn_tasks,
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
        finally:
            _reload_lock.release()
    finally:
        settle_lock.release()


# Global controlling server shutdown.
shutdown_event = asyncio.Event()
# Global controlling responses to exit requests.
enable_shutdown = False


# Per-character settlement locks block /new_dialog during initial summaries, ensuring fresh data after hot switching.
_settle_locks: dict[str, asyncio.Lock] = {}


# Strong-reference registry prevents fire-and-forget task garbage collection.
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
    # Empty character lists are valid zero-migration cases; return zero normally.
    total = 0
    for name in catgirl_names:
        try:
            count = await reflection_engine.areset_confirmed_at_to_now(name)
            total += count
        except Exception as e:
            # One character's failure is nonfatal: log and continue; final count reflects successful entries.
            logger.warning(f"[Memory] migration {name} 重置失败（其他角色继续）: {e}")
    return total


def _get_settle_lock(lanlan_name: str) -> asyncio.Lock:
    """Get the settle lock for the given character (lazily created)"""
    if lanlan_name not in _settle_locks:
        _settle_locks[lanlan_name] = asyncio.Lock()
    return _settle_locks[lanlan_name]


def _spawn_background_task(coro) -> asyncio.Task:
    """Create a background task with strong reference + exception logging.

    Detached work never inherits the caller's admission lease. That lease means
    "an admitted request is still running and release will wait for it"; a
    fire-and-forget task outlives the request and is not waited for, so keeping
    the lease would let it reopen the engine after disposal.
    """
    context_token = _admitted_character_context.set(frozenset())
    try:
        task = asyncio.create_task(coro)
    finally:
        _admitted_character_context.reset(context_token)
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
        # Workers/resolvers read globals via getters, inherently reload-safe.
        embedding_warmup_worker = worker
        embedding_warmup_worker.start()
    except Exception as e:
        logger.warning(f"[Memory] embedding worker bootstrap failed: {e}")
        embedding_warmup_worker = None
        # Resolver already published during core initialization; optional-worker failure cannot affect deletion queues.


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
        time_manager = TimeIndexedMemory(
            recent_history_manager,
            engine_admission_check=_is_character_engine_admitted,
        )
        fact_store = FactStore(time_indexed_memory=time_manager)
        # Queue erasure is part of the privacy runtime, not the optional
        # vector worker. Construct the lightweight resolver before ready so
        # scoped_forget remains available while embedding bootstrap is still
        # running or when that best-effort bootstrap fails.
        from memory.fact_dedup import FactDedupResolver
        fact_dedup_resolver = FactDedupResolver(fact_store)
        # Reverse reference: FactStore Stage-2 near-duplicates enter this LLM arbitration queue.
        fact_store.attach_dedup_resolver(fact_dedup_resolver)
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
            # process dimension distinguishes session_start/session_end diagnostics across processes.
            TokenTracker.get_instance().record_app_start(process="memory_server")
        except Exception as e:
            logger.warning(f"[Memory] Token tracker init failed: {e}")

        # GeoIP warming: memory_server is separate; main-process warming affects only its class cache.
        # Without warming, outbox recovery / first memory-update free-route LLM calls read temporary mainland
        # snapshots while this process's probe just starts. Run after cloud-save bootstrap, matching
        # main_server's configuration-before-warming order. Custom API users wait zero time (no
        # probe; return True directly when snapshots lack region-sensitive URLs).
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

        # Reconcile must finish completely before outbox recovery starts any operation.
        # Both write the same views but read asymmetrically: reconcile handlers
        # load-modify-save under EventLog locks, while live reflection/persona writers
        # first read entire snapshots **outside** locks before record_and_save writes
        # (memory/reflection/evidence_flow.py). Overlap lets live writers overwrite repaired
        # views with pre-replay snapshots after the sentinel already passed that event;
        # it never replays again, causing silent permanent loss.
        # Recovery operations are fire-and-forget tasks (_replay_pending_outbox awaits only
        # scanning); reconcile is fully awaited, so running it first prevents overlap.
        # No reverse dependency: record_and_save performs append+apply+save together,
        # requiring no reconcile reapplication. RFC explicitly forbids depending on outbox effects
        # being visible first (startup recovery in docs/design/memory-event-log-rfc.md).
        # Bonus: recovered operations read repaired views, not crash leftovers.
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
            # ⚠ Decide and write within one locked transaction. Former implicit atomicity from no await
            # blocks only same-loop coroutines. In merged mode storage routes share this process,
            # but write in worker threads, allowing MAINTENANCE_READONLY commits
            # between decision and write, then unconditional NORMAL overwrite here,
            # leaving pending migration without a write gate.
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
            # Phase A-4 / A-5: MemoryRefineEngine cron integration.
            _spawn_background_task(refine_loops._periodic_persona_refine_loop())
            _spawn_background_task(refine_loops._periodic_reflection_refine_loop())
            _spawn_background_task(refine_loops._periodic_reflection_synthesis_loop())
            # Group-memory series 5/7: lightweight scoped refine cron.
            _spawn_background_task(refine_loops._periodic_scoped_refine_loop())
            # Keyed-write auxiliary data: expired staged leftovers and tombstones (key records retained permanently).
            # Background work does not block startup.
            from . import idempotency
            _spawn_background_task(idempotency.cleanup_expired(list(catgirl_names)))
            _memory_background_tasks_started = True

        # memory-enhancements P2: vector embedding warmup + backfill worker.
        # Imports (embedding stack ~0.6s) and construction formerly ran synchronously in startup;
        # uvicorn waits for handler completion before opening ports, delaying memory readiness
        # ~1.3s (further amplified by merged-process serialization). Worker is optional,
        # already delays warm-up, and greetings need no vectors. Move to background tasks,
        # with heavy work entirely in to_thread, never blocking event loop / port readiness.
        _spawn_background_task(_bootstrap_embedding_worker())

        # Public knowledge: own root, own DB, own tasks; started in the
        # background so a slow or broken knowledge root never delays memory.
        try:
            from . import knowledge_routes
            knowledge_dir = getattr(_config_manager, "knowledge_dir", None)
            if knowledge_dir is not None:
                knowledge_routes.spawn_knowledge_runtime(knowledge_dir)
        except Exception as e:
            logger.warning(f"[Memory] knowledge runtime 启动失败（不影响记忆）: {e}")

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
    # Knowledge borrows the same EmbeddingService; stop its background work
    # alongside the worker so both are quiet before the service is released.
    from . import knowledge_routes
    knowledge_stop_task = asyncio.create_task(knowledge_routes.stop_knowledge_runtime())

    managers_to_cleanup: list[TimeIndexedMemory] = []
    async with _reload_lock:
        managers_to_cleanup.extend(_deferred_time_managers)
        _deferred_time_managers.clear()
        # time_manager is instantiated in startup; shutdown during startup may still see None.
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

    async def _await_knowledge_stop() -> None:
        try:
            await knowledge_stop_task
        except Exception as e:
            logger.warning("[Memory] knowledge stop 失败: %s", e)

    shutdown_coros: list = [_cleanup_one(m) for m in managers_to_cleanup]
    if worker_stop_task is not None:
        shutdown_coros.append(_await_worker_stop())
    shutdown_coros.append(_await_knowledge_stop())
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
