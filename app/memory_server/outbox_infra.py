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

"""Generic outbox handler registry + replay infrastructure (P1.c).

Op-type handlers are registered by their owning modules (see ``post_turn``);
``_run_outbox_op`` executes one op with the dead-letter liveness fallback and
``_replay_pending_outbox`` re-runs unfinished ops at startup.
``_replay_semaphore`` is rebound lazily (it is event-loop bound), so access
it as ``outbox_infra._replay_semaphore`` -- a from-import snapshot goes
stale; tests monkeypatch it on this module.
"""

import asyncio
import os
from typing import Awaitable, Callable

from memory.outbox import OP_PERSIST_PROMPT_LOCALE

from . import runtime
from ._shared import logger


# ── Outbox handler registry + replay (P1.c) ────────────────────────

# op_type -> async handler(name: str, payload: dict) -> None. Handlers must be idempotent.
OutboxHandler = Callable[[str, dict], Awaitable[None]]
_OUTBOX_HANDLERS: dict[str, OutboxHandler] = {}

# Startup replay fan-out concurrency limit prevents outbox floods after 24-hour downtime from overwhelming LLM backends.
_REPLAY_CONCURRENCY = 2
_replay_semaphore: asyncio.Semaphore | None = None  # Construct lazily, bound to the event loop.


def register_outbox_handler(op_type: str, handler: OutboxHandler) -> None:
    _OUTBOX_HANDLERS[op_type] = handler


async def _run_outbox_op(name: str, op: dict, sem: asyncio.Semaphore | None = None) -> None:
    """Run a single outbox op and append_done on success. On failure it stays pending and is replayed at the next startup.

    `sem`: the startup replay path passes a shared Semaphore to limit LLM fan-out;
    the everyday single-spawn path passes None for no throttling.

    Liveness fallback (Site 7): on handler failure, append_attempt records one
    failure line. If the cumulative attempt count for the same op_id (including
    this one) is >= ``MEMORY_LIVENESS_MAX_ATTEMPTS``, append_done is written as a
    dead-letter, abandoning the op + WARN. Otherwise a poison op (payload makes the
    handler raise permanently, e.g. LLM safety filter / permanent parse failure)
    would re-run on every restart and never leave pending → ``compact`` blocked
    forever → outbox.ndjson grows linearly. ``op.get('_attempt_count', 0)`` comes
    from the accumulation during the ``pending_ops`` scan; on the everyday spawn
    path the op is constructed ad hoc without this field and starts from 0 (first
    failure → attempt=1, far below N, normally stays pending for replay at
    restart).
    """
    from config import MEMORY_LIVENESS_MAX_ATTEMPTS
    op_id = op.get('op_id')
    op_type = op.get('type')
    payload = op.get('payload') or {}
    from memory.facts import safe_int_field
    prior_attempts = safe_int_field(op, '_attempt_count')
    handler = _OUTBOX_HANDLERS.get(op_type)
    if handler is None:
        logger.warning(f"[Outbox] {name}: 未注册的 op type {op_type}, 跳过 {op_id}")
        return

    # CodeRabbit: for ops already at the dead-letter threshold, append done directly without rerunning the handler.
    # Edge case: a previous ``aappend_attempt`` raised _attempt_count to N successfully, but
    # subsequent ``aappend_done`` failed transiently, leaving the op pending;
    # restart replay sees _attempt_count=N and reruns the handler, fails, then attempts done again.
    # For idempotent handlers this wastes a call; nonidempotent ones violate the required but unenforced outbox contract
    # and duplicate side effects. Short-circuit at entry to guarantee no execution after reaching the threshold.
    if prior_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
        logger.warning(
            f"[Outbox] {name}/{op_type}/{op_id}: 进入时已达 dead-letter 阈值 "
            f"({prior_attempts}/{MEMORY_LIVENESS_MAX_ATTEMPTS})，跳过 handler "
            f"直接补写 done。Why: 上一轮 append_done 可能 IO 失败留 pending，"
            f"避免毒 op 重复执行 + 副作用重放。"
        )
        try:
            await runtime.outbox.aappend_done(name, op_id)
        except Exception as de:
            logger.warning(
                f"[Outbox] {name}/{op_type}/{op_id}: dead-letter "
                f"append_done 仍失败（保持 pending 等下次重放再补 done）: {de}"
            )
        return

    acquired = False
    if sem is not None:
        await sem.acquire()
        acquired = True
    try:
        try:
            await handler(name, payload)
        except Exception as e:
            try:
                await runtime.outbox.aappend_attempt(name, op_id)
                attempt_persisted = True
            except Exception as ae:
                attempt_persisted = False
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id}: append_attempt 失败: {ae}"
                )

            # Codex P1: never trigger dead-letter from an unpersisted increment.
            # If aappend_attempt fails but aappend_done then succeeds,
            # restart sees only prior_attempts attempt rows plus one done row;
            # the op is permanently lost despite disk state showing done after N-1 failures, violating the requirement
            # to give up only after >= N failures. If attempt persistence fails, treat this failure as transient:
            # retain pending so the next retry naturally writes another attempt, without entering dead-letter
            # evaluation.
            if not attempt_persisted:
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id} 执行失败（attempt 持久化"
                    f"失败，按 transient 保留 pending 等下次重放）: {e}"
                )
                return

            total_attempts = prior_attempts + 1
            if total_attempts >= MEMORY_LIVENESS_MAX_ATTEMPTS:
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id}: handler 累计失败 "
                    f"{total_attempts} 次 ≥ {MEMORY_LIVENESS_MAX_ATTEMPTS}，"
                    f"dead-letter 放弃该 op（最近一次失败: {e}）。"
                    f"Why: liveness 兜底，避免毒 payload 让重启 replay 永远卡住 + "
                    f"compact 永久阻塞。"
                )
                try:
                    await runtime.outbox.aappend_done(name, op_id)
                except Exception as de:
                    logger.warning(
                        f"[Outbox] {name}/{op_type}/{op_id}: dead-letter "
                        f"append_done 失败: {de}"
                    )
            else:
                logger.warning(
                    f"[Outbox] {name}/{op_type}/{op_id} 执行失败（保持 pending，"
                    f"attempts={total_attempts}/{MEMORY_LIVENESS_MAX_ATTEMPTS}）: {e}"
                )
            return
        try:
            await runtime.outbox.aappend_done(name, op_id)
        except Exception as e:
            # append_done failure is nonfatal: replay next startup, relying on handler idempotence.
            logger.warning(f"[Outbox] {name}/{op_type}/{op_id}: append_done 失败: {e}")
    finally:
        if acquired and sem is not None:
            sem.release()


async def _replay_pending_outbox() -> list[asyncio.Task]:
    """Scan the outbox at startup and replay unfinished ops. Returns the list of spawned Tasks.

    The return value lets the caller (or tests) await all tasks to completion,
    instead of relying on weak guarantees like a `_BACKGROUND_TASKS` snapshot +
    `asyncio.sleep(0)`.

    Scan scope = character names in the current config ∪ subdirectories under
    memory_dir that have an `outbox.ndjson`. Scanning only the config would miss
    "characters that were once in use, later removed from config, but still have
    pending ops", leaving those ops never replayed.
    """
    global _replay_semaphore
    spawned: list[asyncio.Task] = []
    names: set[str] = set()
    try:
        character_data = await runtime._config_manager.aload_characters()
        names.update(character_data.get('猫娘', {}).keys())
    except Exception as e:
        logger.warning(f"[Outbox] 启动补跑：加载角色列表失败: {e}")
        # Even if config loading fails, allow disk-scan fallback; crash recovery must still work
        # after config changes.

    try:
        memory_dir = runtime._config_manager.memory_dir
        if memory_dir and os.path.isdir(memory_dir):
            for entry in os.listdir(memory_dir):
                candidate = os.path.join(memory_dir, entry, 'outbox.ndjson')
                if os.path.isfile(candidate):
                    names.add(entry)
    except Exception as e:
        logger.warning(f"[Outbox] 启动补跑：扫描 memory_dir 失败: {e}")

    if not names:
        return spawned

    # Construct Semaphore inside the event loop, never at module level.
    if _replay_semaphore is None:
        _replay_semaphore = asyncio.Semaphore(_REPLAY_CONCURRENCY)

    for name in sorted(names):
        try:
            pending = await runtime.outbox.apending_ops(name)
        except Exception as e:
            logger.warning(f"[Outbox] {name}: 读取 pending ops 失败: {e}")
            continue
        if not pending:
            # Opportunistic compaction handles accumulated done rows. Failure affects only space reclamation,
            # not the main flow; log at debug for observability.
            try:
                dropped = await runtime.outbox.amaybe_compact(name)
                if dropped:
                    logger.info(f"[Outbox] {name}: compact 丢弃 {dropped} 行")
            except Exception as e:
                logger.debug(f"[Outbox] {name}: 机会性 compact 失败（可忽略）: {e}")
            continue
        logger.info(f"[Outbox] {name}: 补跑 {len(pending)} 条未完成 op")
        for op in pending:
            # Locale intents are local sidecar writes and deliberately wait
            # out a cloud-apply fence.  Do not let those long-lived retries
            # occupy the bounded LLM replay slots.
            semaphore = (
                None
                if op.get("type") == OP_PERSIST_PROMPT_LOCALE
                else _replay_semaphore
            )
            # Register by character so release can drain long replays before rename/deletion;
            # otherwise replay keeps writing under the old identity after dispose and recreates directories.
            from . import post_turn

            spawned.append(
                post_turn._track_character_post_turn_task(
                    name,
                    runtime._spawn_background_task(
                        _run_outbox_op(name, op, semaphore),
                    ),
                )
            )
    return spawned
