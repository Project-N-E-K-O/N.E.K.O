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

"""MemoryRefineEngine cron loops (Phase A-4 / A-5) and the reflection
synthesis loop: persona refine, reflection refine, and the periodic
unabsorbed-facts → pending-reflections synthesis pass.
"""

import asyncio

from config import (
    MEMORY_REFINE_CRON_INTERVAL_SECONDS,
    MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS,
    SCOPED_REFINE_CRON_INTERVAL_SECONDS,
)

from . import gates, runtime
from ._shared import logger
from .locale_state import (
    aget_subject_prompt_locale,
    run_with_character_prompt_locale,
)
from .gates import (
    _INITIAL_DELAY_PERSONA_REFINE,
    _INITIAL_DELAY_REFLECTION_REFINE,
    _INITIAL_DELAY_REFLECTION_SYNTHESIS,
    _INITIAL_DELAY_SCOPED_REFINE,
)


# ── Phase A-4 / A-5: MemoryRefineEngine 接 cron ─────────────────────


async def _run_persona_refine_for_character(character: str) -> None:
    """Single-character persona refine pass. Embedding unavailable / all
    cluster_hash skipped / not enough candidates → the whole pass is a no-op."""
    from config import (
        MEMORY_LIVENESS_MAX_ATTEMPTS,
        MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
    )
    from memory.facts import safe_int_field
    from memory.temporal import cooldown_elapsed
    from memory.refine import (
        MemoryRefineEngine,
        REFINE_ENTITY_KEY,
        annotate_entry,
    )

    pm = runtime.persona_manager
    if pm is None:
        return
    persona = await pm.aensure_persona(character)
    candidates_by_entity: dict[str, list[dict]] = {}
    for entity in ('master', 'neko', 'relationship'):
        section = pm._get_section_facts(persona, entity)
        # Liveness 过滤：refine_attempts ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 的
        # entry 不再进 cluster gather。Site 4 dead-letter——同 entry 在多
        # cluster 反复 LLM 失败后被 frozen，避免持续占用 starvation-first
        # ordering 名额空跑 LLM。recovery 路径：apply_refine_actions 在
        # stamp 成功时会清回 0；或人工编辑 persona.json；或时间自愈——
        # 冻结后过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS 放行一次 probe，让
        # 一次性 correction 模型宕机恢复后自愈（不再永久冻死无辜 entry）。
        entries = [
            annotate_entry(e, type_='persona', entity=entity)
            for e in section
            if isinstance(e, dict)
            and not e.get('protected')
            and e.get('id')
            and (
                safe_int_field(e, 'refine_attempts') < MEMORY_LIVENESS_MAX_ATTEMPTS
                or cooldown_elapsed(
                    e.get('last_refine_attempt_at'),
                    MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
                )
            )
        ]
        if entries:
            candidates_by_entity[entity] = entries
    if not candidates_by_entity:
        return

    engine = MemoryRefineEngine(runtime._config_manager)

    async def _apply(cluster, actions, cluster_hash):
        # cluster 内成员同 entity（engine 强制），从第一个非空成员读
        ent = next(
            (e.get(REFINE_ENTITY_KEY) for e in cluster
             if isinstance(e, dict) and e.get(REFINE_ENTITY_KEY)),
            'master',
        )
        await pm.apply_refine_actions(character, ent, cluster, actions, cluster_hash)

    async def _failure(cluster, cluster_hash):
        await pm._abump_refine_attempts(character, cluster, cluster_hash)

    result = await engine.refine_pass(
        candidates_by_entity,
        apply_fn=_apply,
        scope_label=f"persona/{character}",
        failure_fn=_failure,
    )
    if result['clusters_resolved'] or result['clusters_failed']:
        logger.info(
            f"[PersonaRefine] {character}: seen={result['clusters_seen']}, "
            f"skipped={result['clusters_skipped']}, "
            f"resolved={result['clusters_resolved']}, "
            f"failed={result['clusters_failed']}"
        )


async def _periodic_persona_refine_loop():
    """Run one PERSONA_REFINE round per character every N seconds.

    Embedding service off / powerful memory off → no-op; the engine's cluster_hash
    skip makes "just reviewed" clusters zero-cost to skip, so high-frequency
    triggering doesn't waste LLM tokens. Initial delay staggered 100s from
    reflection refine."""
    await asyncio.sleep(_INITIAL_DELAY_PERSONA_REFINE)
    interval = MEMORY_REFINE_CRON_INTERVAL_SECONDS
    while True:
        if not await gates._ais_powerful_memory_enabled():
            await asyncio.sleep(interval)
            continue
        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[PersonaRefine] 加载角色列表失败: {e}")
            await asyncio.sleep(interval)
            continue
        for name in catgirl_names:
            try:
                await run_with_character_prompt_locale(
                    name,
                    _run_persona_refine_for_character,
                    name,
                )
            except Exception as e:
                logger.warning(f"[PersonaRefine] {name} cron 异常: {e}")
        await asyncio.sleep(interval)


async def _run_reflection_refine_for_character(
    character: str,
    *,
    subject=None,
) -> None:
    """Single-character reflection refine pass. The cluster may mix in absorbed
    facts of the same entity as a read-only information source (facts cannot be
    split/discarded/modified; the apply layer enforces this as a backstop)."""
    from config import (
        MEMORY_LIVENESS_MAX_ATTEMPTS,
        MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
    )
    from memory.facts import safe_int_field
    from memory.scopes import entry_matches_subject
    from memory.temporal import cooldown_elapsed
    from memory.refine import (
        MemoryRefineEngine,
        REFINE_ENTITY_KEY,
        annotate_entry,
    )

    # 用 `engine_ref` 而不是 `re` —— 后者遮蔽 Python 内置 `re` 模块
    # （CodeRabbit nitpick #1392）。
    engine_ref = runtime.reflection_engine
    fs = runtime.fact_store
    if engine_ref is None or fs is None:
        return

    refls = await engine_ref.aload_reflections(character, include_archived=False)
    if not refls:
        return
    facts = await fs.aload_facts(character)

    candidates_by_entity: dict[str, list[dict]] = {}
    for entity in ('master', 'neko', 'relationship'):
        # Liveness 过滤：refine_attempts ≥ MEMORY_LIVENESS_MAX_ATTEMPTS 的
        # reflection 不再进 cluster gather（同 persona refine）。fact 不算
        # ——fact 是 readonly 信息源，不会被 refine 改，自然不会 bump
        # attempts。时间自愈：冻结后过 MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS
        # 放行一次 probe，让一次性宕机恢复后自愈。
        entity_refls = [
            annotate_entry(r, type_='reflection', entity=entity)
            for r in refls
            if isinstance(r, dict)
            and r.get('entity') == entity
            and r.get('id')
            and entry_matches_subject(r, subject)
            and (
                safe_int_field(r, 'refine_attempts') < MEMORY_LIVENESS_MAX_ATTEMPTS
                or cooldown_elapsed(
                    r.get('last_refine_attempt_at'),
                    MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
                )
            )
        ]
        entity_facts = [
            annotate_entry(f, type_='fact', entity=entity)
            for f in facts
            if isinstance(f, dict) and f.get('entity') == entity
            and f.get('absorbed') and f.get('id')
            and entry_matches_subject(f, subject)
        ]
        if entity_refls:  # 至少要有 reflection；fact 是只读补料
            candidates_by_entity[entity] = entity_refls + entity_facts
    if not candidates_by_entity:
        return

    engine = MemoryRefineEngine(runtime._config_manager)

    async def _apply(cluster, actions, cluster_hash):
        ent = next(
            (e.get(REFINE_ENTITY_KEY) for e in cluster
             if isinstance(e, dict) and e.get(REFINE_ENTITY_KEY)),
            'master',
        )
        await engine_ref.apply_refine_actions(character, ent, cluster, actions, cluster_hash)

    async def _failure(cluster, cluster_hash):
        await engine_ref._abump_refine_attempts(character, cluster, cluster_hash)

    subject_label = (
        "legacy"
        if subject is None
        else f"{subject.key}/{subject.scope}"
    )
    result = await engine.refine_pass(
        candidates_by_entity,
        apply_fn=_apply,
        scope_label=f"reflection/{character}/{subject_label}",
        failure_fn=_failure,
    )
    if result['clusters_resolved'] or result['clusters_failed']:
        logger.info(
            f"[ReflectionRefine] {character}: seen={result['clusters_seen']}, "
            f"skipped={result['clusters_skipped']}, "
            f"resolved={result['clusters_resolved']}, "
            f"failed={result['clusters_failed']}"
        )


async def _run_reflection_refine_with_subject_locales(character: str) -> None:
    engine_ref = runtime.reflection_engine
    if engine_ref is None:
        return
    reflections = await engine_ref.aload_reflections(
        character,
        include_archived=False,
    )
    if not reflections:
        return

    from memory.scopes import is_legacy_private_entry, subject_from_entry
    from utils.language_utils import language_context

    subjects = []
    seen_domains: set[tuple[str | None, str | None]] = set()
    for reflection in reflections:
        if not isinstance(reflection, dict):
            continue
        subject = subject_from_entry(reflection)
        if subject is not None:
            domain = (subject.key, subject.scope)
        elif is_legacy_private_entry(reflection):
            domain = (None, None)
        else:
            continue
        if domain in seen_domains:
            continue
        seen_domains.add(domain)
        subjects.append(subject)

    for subject in subjects:
        selected_locale = None
        if subject is not None:
            selected_locale = await aget_subject_prompt_locale(
                character,
                subject,
            )
        if selected_locale:
            with language_context(selected_locale):
                await _run_reflection_refine_for_character(
                    character,
                    subject=subject,
                )
        else:
            await _run_reflection_refine_for_character(
                character,
                subject=subject,
            )


async def _periodic_reflection_refine_loop():
    """Run one REFLECTION_REFINE round per character every N seconds. The candidate
    pool contains active reflections + absorbed facts of the same entity (facts read-only)."""
    await asyncio.sleep(_INITIAL_DELAY_REFLECTION_REFINE)
    interval = MEMORY_REFINE_CRON_INTERVAL_SECONDS
    while True:
        if not await gates._ais_powerful_memory_enabled():
            await asyncio.sleep(interval)
            continue
        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ReflectionRefine] 加载角色列表失败: {e}")
            await asyncio.sleep(interval)
            continue
        for name in catgirl_names:
            try:
                await run_with_character_prompt_locale(
                    name,
                    _run_reflection_refine_with_subject_locales,
                    name,
                )
            except Exception as e:
                logger.warning(f"[ReflectionRefine] {name} cron 异常: {e}")
        await asyncio.sleep(interval)


# scoped refine 的进程内轮转游标（对偶 synthesize_scoped_reflections 的
# _scoped_synth_cursor：重启从头轮即可，无须持久化）。
_scoped_refine_cursor: dict[str, tuple] = {}
_scoped_refine_character_cursor: str | None = None


async def _run_scoped_refine_for_character(character: str) -> bool:
    """Single-character scoped lite refine pass (at most ONE cluster).

    Buckets scoped persona entries + active scoped reflections by
    (subject.key, scope) per store; only subjects whose per-store pool
    reaches SCOPED_REFINE_MIN_ENTRIES are eligible. Embedding unavailable /
    all clusters hash-fresh / no eligible bucket → whole pass is a no-op.
    """
    from memory.scoped_refine import (
        STORE_PERSONA,
        ScopedLiteRefineEngine,
        abump_scoped_persona_refine_attempts,
        abump_scoped_reflection_refine_attempts,
        apply_scoped_persona_merge,
        apply_scoped_reflection_merge,
        gather_scoped_refine_buckets,
    )

    pm = runtime.persona_manager
    engine_ref = runtime.reflection_engine
    if pm is None or engine_ref is None:
        return False
    persona = await pm.aensure_persona(character)
    refls = await engine_ref.aload_reflections(character, include_archived=False)
    buckets = gather_scoped_refine_buckets(persona, refls)
    if not buckets:
        return False

    lite = ScopedLiteRefineEngine(runtime._config_manager)

    async def _apply(bucket, cluster, actions, cluster_hash):
        # 返回应用成功的 action 数：engine 用它把「非空 actions 全被拒」
        # 判成 cluster 失败（计 refine_attempts，防毒 cluster 无限重打）。
        if bucket.store == STORE_PERSONA:
            return await apply_scoped_persona_merge(
                pm, character, bucket.subject, cluster, actions, cluster_hash,
            )
        return await apply_scoped_reflection_merge(
            engine_ref, character, bucket.subject, cluster, actions,
            cluster_hash,
        )

    async def _failure(bucket, cluster, cluster_hash):
        if bucket.store == STORE_PERSONA:
            await abump_scoped_persona_refine_attempts(
                pm, character, bucket.subject, cluster, cluster_hash,
            )
        else:
            await abump_scoped_reflection_refine_attempts(
                engine_ref, character, bucket.subject, cluster, cluster_hash,
            )

    result = await lite.refine_pass(
        buckets,
        apply_fn=_apply,
        scope_label=f"scoped/{character}",
        failure_fn=_failure,
        start_after=_scoped_refine_cursor.get(character),
        # trust_of 留空：系列 7/7（发言人信赖度）在这里接 speaker_trust。
        trust_of=None,
    )
    if result.get('served') is not None:
        _scoped_refine_cursor[character] = result['served']
    if result['resolved'] or result['failed']:
        logger.info(
            f"[ScopedRefine] {character}: buckets={result['buckets_seen']}, "
            f"clusters_seen={result['clusters_seen']}, "
            f"skipped={result['clusters_skipped']}, "
            f"resolved={result['resolved']}, failed={result['failed']}"
        )
    return result.get('served') is not None


async def _run_scoped_refine_round(catgirl_names: list[str]) -> None:
    """Scan characters fairly and stop after one character serves a cluster."""
    global _scoped_refine_character_cursor
    names = list(dict.fromkeys(catgirl_names))
    if not names:
        return
    start = 0
    if _scoped_refine_character_cursor in names:
        start = (names.index(_scoped_refine_character_cursor) + 1) % len(names)
    rotation = names[start:] + names[:start]
    for name in rotation:
        try:
            served = await _run_scoped_refine_for_character(name)
        except Exception as e:
            logger.warning(f"[ScopedRefine] {name} cron 异常: {e}")
            continue
        if served:
            _scoped_refine_character_cursor = name
            break


async def _periodic_scoped_refine_loop():
    """Run one globally cost-capped SCOPED_REFINE round every N seconds.

    Cost contract (the reason this engine exists separately from the two
    legacy refine crons): across all characters per round at most ONE
    summary-tier no-thinking LLM call, and only when some subject's pool
    crossed the entry threshold. Gated on powerful_memory like the rest of
    the refine / dedup family — with it off the queue of mergeable entries
    just waits.
    """
    await asyncio.sleep(_INITIAL_DELAY_SCOPED_REFINE)
    interval = SCOPED_REFINE_CRON_INTERVAL_SECONDS
    while True:
        if not await gates._ais_powerful_memory_enabled():
            await asyncio.sleep(interval)
            continue
        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ScopedRefine] 加载角色列表失败: {e}")
            await asyncio.sleep(interval)
            continue
        await _run_scoped_refine_round(catgirl_names)
        await asyncio.sleep(interval)


async def _periodic_reflection_synthesis_loop():
    """Run one reflection synthesis round per character every N seconds.

    Dual to the other 9 ``_periodic_*_loop``s — signal_extraction distills
    conversations into facts, this loop synthesizes unabsorbed facts into pending
    reflections, and auto_promote_loop pushes pending on to confirmed/promoted.
    The whole chain runs long-lived inside the memory_server process, independent
    of the ``/api/proactive_chat`` HTTP trigger (and thus no longer dependent on a
    frontend browser being open).

    History (why this loop exists): reflection synthesis used to hang solely off
    the proactive_chat handler in ``main_routers/system_router.py``
    (``_mem_client.post('/reflect/{name}')``, tacked on in PR #1015), which meant:
      - frontend closed / proactive never fires / any frontend gate false →
        ``/reflect`` is never called → ``reflections.json`` never grows
      - the reflection lifecycle was effectively hard-coupled to a frontend
        setTimeout, violating the design intent of "the long-running backend
        service guarantees the memory ecosystem on its own"

    Gating relies entirely on what's built into
    ``reflection_engine.synthesize_reflections``:
      - ``len(unabsorbed) < MIN_FACTS_FOR_REFLECTION (=5)`` → returns [] directly
      - same batch of source_fact_ids → same-rid idempotent short-circuit; no LLM
        call when there are no new facts
      - ``REFLECTION_SYNTHESIS_FACTS_MAX (=20)`` caps single-run input size
    So this loop only schedules and adds no duplicate gates; the interval constant
    ``MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS`` bounds the max call rate.

    Relation to the powerful_memory switch: synthesize_reflections is not one of
    the new LLM paths introduced by the evidence RFC — it is a synthesis mechanism
    that predates the RFC (pending reflections existed before the RFC; evidence
    merely added state progression), so it is **not** affected by powerful_memory
    being off. That differs from refine / signal_extraction, and aligns with how
    the "history compression / review" subtasks in idle_maintenance are handled.
    """
    await asyncio.sleep(_INITIAL_DELAY_REFLECTION_SYNTHESIS)
    interval = MEMORY_REFLECTION_SYNTHESIS_INTERVAL_SECONDS
    while True:
        try:
            character_data = await runtime._config_manager.aload_characters()
            catgirl_names = list(character_data.get('猫娘', {}).keys())
        except Exception as e:
            logger.debug(f"[ReflectionSynth] 加载角色列表失败: {e}")
            await asyncio.sleep(interval)
            continue
        for name in catgirl_names:
            try:
                results = []
                try:
                    results += await run_with_character_prompt_locale(
                        name,
                        runtime.reflection_engine.synthesize_reflections,
                        name,
                    )
                except Exception as legacy_error:
                    # 两遍隔离：legacy 侧的持久失败（例如手改出的无 id
                    # 事实让 sorted(f['id']) 抛错）不得饿死 scoped 侧——
                    # 否则该角色的群/成员反思永远不产出。
                    logger.warning(
                        f"[ReflectionSynth] {name} legacy 合成异常: {legacy_error}"
                    )
                # Cost-bounded scoped pass: at most one ready group/member
                # subject per maintenance tick. Legacy-private synthesis above
                # keeps its original behaviour and cadence.
                scoped_synth = getattr(
                    runtime.reflection_engine, 'synthesize_scoped_reflections', None,
                )
                if callable(scoped_synth):
                    results += await run_with_character_prompt_locale(
                        name,
                        scoped_synth,
                        name,
                        max_subjects=1,
                        subject_locale_resolver=aget_subject_prompt_locale,
                    )
                if results:
                    logger.info(
                        f"[ReflectionSynth] {name}: 合成 {len(results)} 条新 reflection"
                        "（legacy=pending / scoped=confirmed）"
                    )
            except Exception as e:
                # 单角色合成失败不阻塞其他角色 / 下轮重试
                logger.warning(f"[ReflectionSynth] {name} 合成异常: {e}")
        await asyncio.sleep(interval)
