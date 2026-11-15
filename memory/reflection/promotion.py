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
"""Promotion methods for the memory manager."""

from __future__ import annotations





from datetime import datetime


from config import (
    EVIDENCE_CONFIRMED_THRESHOLD,
    EVIDENCE_PROMOTED_THRESHOLD,
)

from memory.evidence import (
    awaits_user_signal,
    evidence_score,
    observed_days,
    scoped_promotion_ready,
)
from memory.introspection import is_permanently_suppressed



from utils.file_utils import (
    robust_json_loads,
)


from utils.token_tracker import set_call_type








from ._shared import (
    logger,
    REFLECTION_TERMINAL_STATUSES,
)

class PromotionMixin:
    def auto_promote_stale(self, lanlan_name: str) -> int:
        """Score-driven pending → confirmed (RFC §3.9.1 / §4.1 PR-1 scope).

        Deprecated sync version — retained for backward-compat callers
        (tests / CLI scripts). Production path is the async twin below.

        - The time-skip branches are deleted (`AUTO_CONFIRM_DAYS` / `AUTO_PROMOTE_DAYS` removed)
        - Only does pending → confirmed: `evidence_score(r, now) >= EVIDENCE_CONFIRMED_THRESHOLD`
        - confirmed → promoted is taken over by PR-3's `_apromote_with_merge`;
          in PR-1 this function carries no promotion responsibility
        Returns number of transitions.
        """
        reflections = self.load_reflections(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []
        facts = None

        for r in reflections:
            if r.get('status') != 'pending':
                continue
            if evidence_score(r, now) < EVIDENCE_CONFIRMED_THRESHOLD:
                continue
            if facts is None:
                try:
                    # Cold facts as stubs: a withdrawn fact still has to count as withdrawn.
                    facts = self._facts_by_id(
                        self._fact_store.load_facts_full(lanlan_name, include_cold_stubs=True)
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"[Reflection] {lanlan_name}: 读取事实失败，按已存的来源计数判断: {e}")
                    facts = {}
            if awaits_user_signal(r, facts):
                continue
            r['status'] = 'confirmed'
            r['confirmed_at'] = now.isoformat()
            confirmed_ids.append(r['id'])
            transitions += 1
            logger.info(
                f"[Reflection] {lanlan_name}: pending→confirmed"
                f" (score driven): {r['text'][:50]}..."
            )

        if transitions:
            self.save_reflections(lanlan_name, reflections)
            if confirmed_ids:
                self._batch_mark_surfaced_handled(
                    lanlan_name, confirmed_ids, 'confirmed',
                )
        return transitions

    @staticmethod
    def _facts_by_id(facts) -> dict:
        return {
            f['id']: f for f in facts or ()
            if isinstance(f, dict) and isinstance(f.get('id'), str)
        }

    def _facts_as_they_stand(self, lanlan_name: str):
        """An async getter of every fact by id, archived ones included.

        The promotion gates count a reflection's sources by these, so a fact
        withdrawn or recognized as the user's own words since the counts were
        saved is counted as it stands now. Read on first use only: most
        passes reach no reflection the gate applies to. An unreadable store
        gives an empty mapping, and the gates use the saved counts.
        """
        cache: list[dict] = []

        async def facts() -> dict:
            if not cache:
                try:
                    cache.append(self._facts_by_id(
                        await self._fact_store.aload_facts_full(
                            lanlan_name, include_cold_stubs=True,
                        )
                    ))
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        f"[Reflection] {lanlan_name}: 读取事实失败，按已存的来源计数判断: {e}"
                    )
                    cache.append({})
            return cache[0]

        return facts

    async def aauto_promote_stale(self, lanlan_name: str) -> int:
        """P2.a.2: serialized by the character-level asyncio.Lock. Score-driven
        pending → confirmed (§3.9.1) + confirmed → promoted via merge-on-promote
        (RFC §3.9.3, PR-3). The promote pass uses `_apromote_with_merge`,
        which dispatches LLM merge decisions and updates throttle state.

        Returns the number of pending→confirmed transitions (the legacy
        contract). Promote attempts are logged but not folded into the
        return count — they're a separate signal kept in
        `last_promote_attempt_at` / `promote_attempt_count` per reflection.
        """
        from memory import organizer
        if not organizer.allowed(lanlan_name):
            # Another device organizes this character; its promotions arrive by snapshot.
            # Group and member partitions never sync, so they keep their lifecycle here.
            await self.aauto_promote_time_driven(lanlan_name, scoped_only=True)
            return 0
        async with self._get_alock(lanlan_name):
            transitions = await self._aauto_promote_stale_locked(lanlan_name)
        # Promote pass runs OUTSIDE the per-character lock because each
        # `_apromote_with_merge` re-acquires the lock internally and also
        # acquires the persona lock. Keeping the outer lock here would
        # serialize the LLM call within the lock and block other reflection
        # writes (e.g. arecord_mentions) for the duration of the network
        # round-trip — RFC §3.3.3 outer-async-inner-sync chain breaks down
        # if we hold across the LLM await.
        await self._aauto_promote_score_driven(lanlan_name)
        # Simplified group memory: scoped reflections produce no evidence, so both score-driven
        # passes skip them. Time-driven passes advance lifecycle, including legacy scoped pending,
        # by age with zero LLM cost. Centralizing here keeps four existing
        # caller（evidence_loops / post_turn / routes / signal_extraction）
        # callers unaware of mode differences.
        await self.aauto_promote_time_driven(lanlan_name, scoped_only=True)
        return transitions

    async def _aauto_promote_score_driven(self, lanlan_name: str) -> int:
        """Score-driven confirmed → promoted (RFC §3.9.1).

        Iterates `confirmed` reflections; for each whose
        `evidence_score >= EVIDENCE_PROMOTED_THRESHOLD`, dispatches to
        `_apromote_with_merge`. Returns the count of attempts (NOT the
        count of successful promotions — the LLM may merge / reject /
        skip per-throttle).

        Skips reflections in `promote_blocked` dead-letter status. Caller
        does NOT need to hold the per-character lock — `_apromote_with_merge`
        re-acquires both reflection and persona locks internally.
        """
        # Read snapshot OUTSIDE the lock — the promote pass tolerates a
        # slightly stale view (any concurrent state change will just mean
        # we attempt promote on a no-longer-confirmed reflection, which
        # the inner lock + status recheck will catch).
        from memory.scopes import is_legacy_private_entry

        reflections = await self._aload_reflections_full(lanlan_name)
        now = datetime.now()
        attempts = 0
        facts = self._facts_as_they_stand(lanlan_name)
        for r in reflections:
            if r.get('status') != 'confirmed':
                continue
            if not is_legacy_private_entry(r):
                # Scoped reflections use simplified time-driven lifecycle, never score-driven
                # promotion without evidence sources. Corrupt partial subject
                # descriptors fail closed, never entering legacy personas.
                continue
            if evidence_score(r, now) < EVIDENCE_PROMOTED_THRESHOLD:
                continue
            if awaits_user_signal(r, await facts()):
                # Rests only on the AI's own words and the user never backed
                # it: score alone does not write it into persona.
                logger.debug(
                    f"[Promote] {lanlan_name}/{r.get('id')}: 只有 AI 自述、"
                    f"没有用户信号，暂不升格"
                )
                continue
            try:
                outcome = await self._apromote_with_merge(lanlan_name, r)
                attempts += 1
                logger.info(
                    f"[Promote] {lanlan_name}/{r.get('id')}: "
                    f"outcome={outcome}"
                )
            except Exception as e:  # noqa: BLE001
                # Per-reflection failures don't sink the loop. The throttle
                # state in the reflection itself gates retries.
                logger.warning(
                    f"[Promote] {lanlan_name}/{r.get('id')}: "
                    f"unhandled error: {e}"
                )
        return attempts

    async def _aauto_promote_stale_locked(self, lanlan_name: str) -> int:
        """Score-driven pending → confirmed only.

        Must run **after** all evidence signals for this tick have been
        applied (signal dispatch emits EVT_REFLECTION_EVIDENCE_UPDATED then
        this loop reads the updated view to decide promotions). The caller
        is responsible for that ordering — see memory_server background loops.
        """
        from memory.scopes import is_legacy_private_entry

        reflections = await self._aload_reflections_full(lanlan_name)
        now = datetime.now()
        transitions = 0
        confirmed_ids: list[str] = []
        facts = self._facts_as_they_stand(lanlan_name)

        for r in reflections:
            if r.get('status') != 'pending':
                continue
            if not is_legacy_private_entry(r):
                # Scoped pending, normally absent because synthesis directly confirms but possible
                # in legacy data, confirms by age in scoped time-driven passes; corrupt
                # descriptors fail closed without advancement.
                continue
            if evidence_score(r, now) < EVIDENCE_CONFIRMED_THRESHOLD:
                continue
            if awaits_user_signal(r, await facts()):
                # Only the AI's own words behind it and no confirmation from
                # the user (an "engaged" reply adds score but not backing):
                # stays pending, still eligible to be brought up.
                continue
            r['status'] = 'confirmed'
            r['confirmed_at'] = now.isoformat()
            confirmed_ids.append(r['id'])
            transitions += 1
            logger.info(
                f"[Reflection] {lanlan_name}: pending→confirmed"
                f" (score driven): {r['text'][:50]}..."
            )

        if transitions:
            # Writeback filters active/nonterminal reflections, matching
            # aload_reflections. Terminal archived/merged entries are handled
            # by _prepare_save_reflections merging.
            active = [
                r for r in reflections
                if r.get('status') not in REFLECTION_TERMINAL_STATUSES
            ]
            await self.asave_reflections(lanlan_name, active)
            if confirmed_ids:
                await self._abatch_mark_surfaced_handled(
                    lanlan_name, confirmed_ids, 'confirmed',
                )
        return transitions

    @staticmethod
    def _time_driven_held_back(
        lanlan_name: str, reflection: dict, now: datetime, facts_by_id=None,
    ) -> bool:
        """Whether the time-driven path must leave ``reflection`` where it is.

        Held back: net evidence below zero (the user turned against it), or
        resting only on the AI's own words with no explicit user signal --
        the two things the score-driven paths never let through either.
        """
        rid = reflection.get('id')
        if evidence_score(reflection, now) < 0:
            logger.debug(
                f"[Reflection] {lanlan_name}/{rid}: 净分为负，不按时间推进"
            )
            return True
        if awaits_user_signal(reflection, facts_by_id):
            logger.debug(
                f"[Reflection] {lanlan_name}/{rid}: 只有 AI 自述、没有用户信号，"
                f"不按时间推进"
            )
            return True
        return False

    @staticmethod
    def _apply_time_driven_promotion(reflection, rid, now, promoted_ids) -> None:
        """Mark a reflection promoted (shared by the write and retry paths)."""
        reflection['status'] = 'promoted'
        reflection['promoted_at'] = now.isoformat()
        if rid:
            promoted_ids.append(rid)

    async def _aretired_from_promotion(self, lanlan_name: str):
        """A predicate: is this reflection retired by introspection, itself or
        behind the persona line it repeats?

        Promoting either would write the retired text back into persona as a
        fresh, unflagged entry (``aadd_fact`` skips retired entries). The
        second follows the render's own rule (``retired_persona_hides``): a
        stale line with only newer facts behind it is the way back and stays
        promotable. Persona unreadable: everything counts as retired, so the
        promotion waits for the next round.
        """
        try:
            persona = await self._persona_manager.aget_persona(lanlan_name)
        except Exception as e:
            logger.warning(f"[Promote] {lanlan_name}: 读取人格失败，本轮不升格: {e}")
            return lambda _reflection: True
        hides = self._persona_manager.retired_persona_hides(persona or {})
        return lambda reflection: is_permanently_suppressed(reflection) or bool(hides(reflection))

    async def aauto_promote_time_driven(
        self, lanlan_name: str, *, scoped_only: bool = False,
    ) -> int:
        """Time-driven lifecycle: the "strong memory OFF" fallback, and the
        one-and-only lifecycle for scoped (group/member) reflections.

        Zero LLM cost. Advances the lifecycle by time:
          - pending for ``WEAK_MEMORY_AUTO_CONFIRM_DAYS`` days (by created_at)
            → status='confirmed', confirmed_at=now, auto_confirmed=True
          - a private reflection confirmed for ``WEAK_MEMORY_AUTO_PROMOTE_DAYS``
            days (by confirmed_at), or a scoped one seen in others' words on
            enough days over long enough (``scoped_promotion_ready``) → call
            ``persona.aadd_fact`` directly via the simple merge-in path,
            **not** the merge LLM. aadd_fact's internal heuristic dedup +
            character-card contradiction check are the safety net.

        Neither step takes a reflection the user turned against (net evidence
        below zero) or one resting only on the AI's own words with no
        explicit user signal (``awaits_user_signal``); the score-driven paths
        hold both back too, and the time-driven path must not be the way
        around them.

        Returns: total number of pending→confirmed + confirmed→promoted transitions.

        ``scoped_only=True`` is the strong-memory caller
        (``aauto_promote_stale``): both passes cover scoped reflections only,
        legacy entries stay score-driven. ``scoped_only=False`` is the
        weak-memory caller: legacy reflections advance by age, scoped ones
        only from pending to confirmed and never into persona, because what
        cleans up after a scoped promotion (correction queue, scoped refine,
        introspection) runs with strong memory only. Corrupt partial subject
        descriptors are excluded in both modes (fail-closed, never written to
        any persona).
        """
        from memory import organizer
        # Private reflections belong to the organizing device. This is a separate
        # question from scoped_only: on a weak-memory device that mode must stay
        # as the caller gave it, or scoped reflections would start entering persona.
        private_here = organizer.allowed(lanlan_name)
        from config import (
            WEAK_MEMORY_AUTO_CONFIRM_DAYS,
            WEAK_MEMORY_AUTO_PROMOTE_DAYS,
        )

        from memory.scopes import is_legacy_private_entry, subject_from_entry

        from memory.persona import PersonaManager
        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            now = datetime.now()
            transitions = 0
            confirmed_ids: list[str] = []
            promoted_ids: list[str] = []
            denied_ids: list[str] = []
            facts = self._facts_as_they_stand(lanlan_name)

            # Pass 1: pending → confirmed by created_at age
            for r in reflections:
                if r.get('status') != 'pending':
                    continue
                subj = subject_from_entry(r)
                if (scoped_only or not private_here) and subj is None:
                    continue
                if subj is None and not is_legacy_private_entry(r):
                    # Corrupt partial subjects fail closed in both modes,
                    # never advancing by age, matching scopes.py read exclusions.
                    continue
                created_iso = r.get('created_at')
                if not created_iso:
                    continue
                try:
                    created = datetime.fromisoformat(created_iso)
                except (ValueError, TypeError):
                    continue
                age_days = (now - created).total_seconds() / 86400
                if age_days < WEAK_MEMORY_AUTO_CONFIRM_DAYS:
                    continue
                if self._time_driven_held_back(lanlan_name, r, now, await facts()):
                    continue
                r['status'] = 'confirmed'
                r['confirmed_at'] = now.isoformat()
                r['auto_confirmed'] = True
                rid = r.get('id')
                if rid:
                    confirmed_ids.append(rid)
                transitions += 1
                logger.info(
                    f"[Reflection] {lanlan_name}: pending→confirmed "
                    f"(time-driven, {int(age_days)}d): "
                    + (f"{rid}" if subj is not None else f"{r.get('text', '')[:50]}...")
                )

            # Pass 2: confirmed → promoted/denied by confirmed_at age
            # Newly confirmed Pass-1 entries have confirmed_at=now, age zero, below
            # fourteen-day thresholds; no reflection pending->promoted in one pass.
            retired = await self._aretired_from_promotion(lanlan_name)
            for r in reflections:
                if r.get('status') != 'confirmed':
                    continue
                if retired(r):
                    # Same rule as the score-driven path: promotion would
                    # copy the retired text into persona unflagged.
                    continue
                promote_subject = subject_from_entry(r)
                if (scoped_only or not private_here) and promote_subject is None:
                    continue
                if promote_subject is None and not is_legacy_private_entry(r):
                    # Like Pass 1, corrupt descriptors cannot enter any persona through zero-cost promotion.
                    continue
                if promote_subject is not None:
                    if not scoped_only:
                        # Weak memory: nothing would dedupe or arbitrate a
                        # scoped persona entry afterwards, so none is written.
                        continue
                    # Seen in others' words on enough days, the first long
                    # enough ago. A merge resets confirmed_at but keeps the
                    # days, so it does not restart the wait.
                    if not scoped_promotion_ready(r, now):
                        continue
                    # For the log: days since it was first seen.
                    age_days = float(
                        (now.date() - datetime.fromisoformat(observed_days(r)[0]).date()).days
                    )
                else:
                    confirmed_iso = r.get('confirmed_at')
                    if not confirmed_iso:
                        continue
                    try:
                        confirmed_at = datetime.fromisoformat(confirmed_iso)
                    except (ValueError, TypeError):
                        continue
                    age_days = (now - confirmed_at).total_seconds() / 86400
                    if age_days < WEAK_MEMORY_AUTO_PROMOTE_DAYS:
                        continue
                if self._time_driven_held_back(lanlan_name, r, now, await facts()):
                    continue

                # Direct persona.aadd_fact, simple pre-RFC merge, with three returns:
                #   FACT_ADDED -> promoted reflection
                #   FACT_REJECTED_CARD -> denied, contradicting character cards
                #   FACT_QUEUED_CORRECTION -> scoped (strong memory, the queue is
                #     consumed): the arbitration decides what reaches persona,
                #     so the reflection is done (promoted, promoted_via=
                #     'correction_queue'); left confirmed it would be queued
                #     again after every resolution, one LLM call per round
                #     forever. Legacy (weak memory, the queue is never
                #     consumed): stays confirmed and retries next pass.
                rid = r.get('id')
                already_applied = bool(
                    promote_subject is not None
                    and rid
                    and await self._ascoped_promotion_already_applied(
                        lanlan_name, rid, promote_subject,
                    )
                )
                if already_applied:
                    # Persona persisted last pass but reflection persistence failed: retries must
                    # **not** aadd_fact again. Identical text may conflict with itself,
                    # durably queuing self-correction; later correction LLMs may rewrite entries
                    # or erase source_id provenance. Check before writing.
                    code = PersonaManager.FACT_ADDED
                    logger.info(
                        f"[Reflection] {lanlan_name}/{rid}: scoped 提升已存在，"
                        f"跳过重复写入"
                    )
                    self._apply_time_driven_promotion(
                        r, rid, now, promoted_ids,
                    )
                    transitions += 1
                    continue
                try:
                    promote_kwargs = (
                        {'subject': promote_subject}
                        if promote_subject is not None else {}
                    )
                    code = await self._persona_manager.aadd_fact(
                        lanlan_name, r.get('text', ''),
                        entity=r.get('entity', 'relationship'),
                        source='reflection_time_driven',
                        source_id=rid,
                        speaker_provenance=r,
                        **promote_kwargs,
                    )
                except Exception as e:
                    logger.warning(
                        f"[Promote] {lanlan_name}/{rid}: time-driven aadd_fact 失败: {e}"
                    )
                    continue

                if (
                    code == PersonaManager.FACT_QUEUED_CORRECTION
                    and promote_subject is not None
                    and rid
                    and await self._ascoped_promotion_already_applied(
                        lanlan_name, rid, promote_subject,
                    )
                ):
                    # Idempotent retry: persona source_id already exists,
                    # reflection save failed leaving confirmed. Keeping confirmed
                    # would repeatedly queue contradictions against itself,
                    # causing infinite self-correction. Treat already promoted.
                    code = PersonaManager.FACT_ADDED
                    logger.info(
                        f"[Reflection] {lanlan_name}/{rid}: scoped 提升已存在，"
                        f"按幂等完成处理"
                    )
                if code == PersonaManager.FACT_ADDED:
                    self._apply_time_driven_promotion(r, rid, now, promoted_ids)
                    transitions += 1
                    logger.info(
                        f"[Reflection] {lanlan_name}: confirmed→promoted "
                        f"(time-driven, {int(age_days)}d, no LLM): "
                        + (
                            f"{rid}" if promote_subject is not None
                            else f"{r.get('text', '')[:50]}..."
                        )
                    )
                elif code == PersonaManager.FACT_REJECTED_CARD:
                    r['status'] = 'denied'
                    r['denied_reason'] = 'rejected_by_persona_card_time_driven'
                    if rid:
                        denied_ids.append(rid)
                    transitions += 1
                    logger.info(
                        f"[Reflection] {lanlan_name}: confirmed→denied "
                        f"(time-driven, 角色卡矛盾): {rid}"
                    )
                elif (
                    code == PersonaManager.FACT_QUEUED_CORRECTION
                    and promote_subject is not None
                ):
                    self._apply_time_driven_promotion(r, rid, now, promoted_ids)
                    r['promoted_via'] = 'correction_queue'
                    transitions += 1
                    logger.info(
                        f"[Reflection] {lanlan_name}: confirmed→promoted "
                        f"(time-driven, 已送矛盾审视，以裁决结果为准): {rid}"
                    )
                # Legacy FACT_QUEUED_CORRECTION stays confirmed for next-pass retry.

            # Save once using full reflection lists, including newly promoted/denied terminal
            # entries. **Never filter REFLECTION_TERMINAL_STATUSES**: previous
            # filtering made terminal entries inactive, then
            # _prepare_save_reflections deemed them stale and omitted them, losing promoted/
            # denied transitions. asave_reflections' internal _prepare_save_reflections
            # naturally archives old terminal entries after _REFLECTION_ARCHIVE_DAYS;
            # no extra filtering needed here.
            # Any confirmed/promoted/denied transition triggers save; gating only on
            # promoted_ids loses denied changes for pure FACT_REJECTED_CARD batches.
            if transitions:
                await self.asave_reflections(lanlan_name, reflections)
                handled_ids = confirmed_ids + promoted_ids + denied_ids
                if handled_ids:
                    await self._abatch_mark_surfaced_handled(
                        lanlan_name, handled_ids, 'confirmed',
                    )

        return transitions

    async def _ascoped_promotion_already_applied(
        self, lanlan_name: str, reflection_id: str, subject,
    ) -> bool:
        """True when this reflection already produced a scoped persona entry.

        The persona write and the reflection status flip are two stores; a
        failure between them leaves a durable entry whose retry would be
        rejected as a self-contradiction forever."""
        try:
            persona = await self._persona_manager.aensure_persona(lanlan_name)
        except Exception:
            return False
        if not isinstance(persona, dict):
            return False
        from memory.scopes import entry_matches_subject

        for section in persona.values():
            if not isinstance(section, dict):
                continue
            for fact in section.get('facts') or []:
                if not isinstance(fact, dict):
                    continue
                # Scoped refinement merges multiple promoted entries into one; output
                # source_id inherits only the first, storing other reflection IDs in
                # merged_source_ids. Check both for idempotence, or half-commits where persona
                # persisted but reflection status failed will repromote duplicates
                # after merging.
                if (
                    fact.get('source_id') != reflection_id
                    and reflection_id not in (fact.get('merged_source_ids') or [])
                ):
                    continue
                if entry_matches_subject(fact, subject):
                    return True
        return False

    async def _abump_reflection_recheck_attempts(
        self, lanlan_name: str, rid: str, reason: str,
    ) -> None:
        """Increment the given reflection's ``recheck_attempts`` counter.

        Once failures reach the ``MEMORY_RECHECK_MAX_ATTEMPTS`` cap, the
        candidates filter excludes the entry so the loop gives its slot to
        other v1 entries. The counter is persisted to the main file and
        survives restarts (so the same bad entry can't keep starving others by
        burning LLM quota).
        Best-effort — save failures don't raise; the next run tries again.
        """
        try:
            async with self._get_alock(lanlan_name):
                current = await self._aload_reflections_full(lanlan_name)
                for r in current:
                    if r.get('id') == rid:
                        r['recheck_attempts'] = (r.get('recheck_attempts') or 0) + 1
                        # Stamp failure times for dead-letter time healing, cooldown_elapsed.
                        r['last_recheck_attempt_at'] = datetime.now().isoformat()
                        # Pass active-only, matching _abump_refine_attempts / arecord_mentions
                        # save conventions, archiving promoted/denied terminal entries normally,
                        # not retaining another cycle (CodeRabbit consistency nitpick).
                        active = [
                            x for x in current
                            if x.get('status') not in REFLECTION_TERMINAL_STATUSES
                        ]
                        await self.asave_reflections(lanlan_name, active)
                        logger.debug(
                            f"[Recheck-Reflection] {lanlan_name} {rid}: "
                            f"recheck_attempts → {r['recheck_attempts']} ({reason})"
                        )
                        return
        except Exception as e:
            logger.debug(
                f"[Recheck-Reflection] {lanlan_name} {rid}: bump attempts 失败: {e}"
            )

    async def arecheck_one_legacy_reflection(self, lanlan_name: str) -> bool:
        """Schema v1 → v2 slow recheck (processes only 1 entry per call).

        Finds the character's oldest non-archived reflection with
        schema_version < CURRENT, asks the LLM to re-label temporal_scope
        (pattern/state/episode) + event_when (relative offsets); the system
        resolves them into ISO against reflection.created_at and writes them
        back.

        Returns: True when one entry was processed successfully; False when no
        candidate was found or processing failed.
        The caller (memory_server._periodic_slow_memory_recheck_loop) may
        proceed to the next item without sleeping on True (in practice it still
        paces at one per 30s).

        Skip conditions:
        - schema_version >= CURRENT (already upgraded)
        - status in REFLECTION_TERMINAL_STATUSES (archived/merged etc., terminal)
        - already outside the main reflections.json (archive shards aren't
          loaded by load_reflections)
        """
        from memory import organizer
        if not organizer.allowed(lanlan_name):
            return False
        from config import (
            MEMORY_SCHEMA_VERSION_CURRENT as _SCHEMA_V,
            MEMORY_RECHECK_MAX_ATTEMPTS as _MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS as _SELF_HEAL,
        )
        from config.prompts.prompts_memory import (
            MEMORY_RECHECK_REFLECTION_PROMPT,
        )
        from memory.temporal import (
            normalize_event_when as _norm_when,
            compute_event_timestamps as _compute_ts,
            cooldown_elapsed,
            ACTIVE_TEMPORAL_SCOPES,
        )

        # ── Unlocked: choose candidates + LLM ──
        reflections = await self.aload_reflections(lanlan_name)
        candidates = [
            r for r in reflections
            if (r.get('schema_version') or 1) < _SCHEMA_V
            and r.get('status') not in REFLECTION_TERMINAL_STATUSES
            # Retry budgets: entries repeatedly returning invalid temporal_scope or raising
            # stop blocking queues after limits (Codex review PR #1316 P2).
            # Time healing permits one probe after _SELF_HEAL for capped entries.
            and (
                (r.get('recheck_attempts') or 0) < _MAX_ATTEMPTS
                or cooldown_elapsed(r.get('last_recheck_attempt_at'), _SELF_HEAL)
            )
        ]
        if not candidates:
            return False
        # Oldest-first FIFO migration, ID tie-break for stability.
        candidates.sort(key=lambda r: (r.get('created_at', ''), r.get('id', '')))
        # Skip malformed candidates (missing id / created_at) instead of
        # aborting the whole call — otherwise a single bad legacy entry at
        # head of FIFO order would starve every later v1 reflection forever
        # (Codex review on PR #1316 P2 catch).
        target: dict | None = None
        rid = ''
        created_at_iso = ''
        for c in candidates:
            cid = c.get('id')
            cts = c.get('created_at', '')
            if not cid or not cts:
                logger.debug(
                    f"[Recheck-Reflection] {lanlan_name}: skip malformed legacy "
                    f"reflection (id={cid!r} created_at={cts!r})"
                )
                continue
            target = c
            rid = cid
            created_at_iso = cts
            break
        if target is None:
            return False

        # Fetch source facts, at most five texts to bound prompts.
        source_fact_ids = target.get('source_fact_ids') or []
        source_facts_text = ""
        if source_fact_ids:
            try:
                all_facts = await self._fact_store.aload_facts(lanlan_name)
                fact_by_id = {f.get('id'): f for f in all_facts}
                lines = []
                for fid in source_fact_ids[:5]:
                    f = fact_by_id.get(fid)
                    if f and f.get('text'):
                        lines.append(f"- {f['text']}")
                source_facts_text = "\n".join(lines) if lines else "（无）"
            except Exception:
                source_facts_text = "（无）"
        else:
            source_facts_text = "（无）"

        prompt = MEMORY_RECHECK_REFLECTION_PROMPT.format(
            REFLECTION_TEXT=target.get('text', ''),
            CREATED_AT=created_at_iso,
            SOURCE_FACTS=source_facts_text,
        )

        failure_reason: str | None = None
        new_scope: str | None = None
        event_when_raw: dict | None = None
        try:
            from utils.llm_client import create_chat_llm_async
            set_call_type("memory_recheck_reflection")
            api_config = await self._config_manager.aget_model_api_config('summary')
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            # Disable thinking, like memory_recheck_fact: annotate two fields on one legacy entry.
            # New reflections get identical temporal_scope/event_when during synthesis;
            # backfill is not worth excessive-thinking truncation/timeouts that exhaust retries and leave v1 forever.
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=60, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; the answer is one small two-field JSON
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # prompt assembled from token-capped memory components (REFLECTION_*/RECALL_* budgets in the prompt builder).
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                failure_reason = "non-dict response"
            else:
                new_scope = result.get('temporal_scope')
                if new_scope not in ACTIVE_TEMPORAL_SCOPES:
                    failure_reason = f"invalid temporal_scope={new_scope!r}"
                else:
                    event_when_raw = _norm_when(result.get('event_when'))
        except Exception as e:
            failure_reason = f"LLM call failed: {e}"

        # Unified failures increment recheck_attempts, excluding repeated failures from
        # candidate filters after MAX (Codex review PR #1316 P2).
        if failure_reason is not None:
            logger.debug(
                f"[Recheck-Reflection] {lanlan_name} {rid}: 跳过本轮 ({failure_reason})"
            )
            await self._abump_reflection_recheck_attempts(lanlan_name, rid, failure_reason)
            return False

        # Resolve ISO timestamps against created_at.
        _needs_end = new_scope in ('state', 'episode')
        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            created_at_iso,
            fallback_start=True,
            fallback_end=_needs_end,
        )

        # ── Locked: reload + locate ID + update + save ──
        save_failed_reason: str | None = None
        async with self._get_alock(lanlan_name):
            current = await self._aload_reflections_full(lanlan_name)
            found = None
            for r in current:
                if r.get('id') == rid:
                    found = r
                    break
            if found is None:
                logger.debug(f"[Recheck-Reflection] {lanlan_name} {rid}: 锁内已不存在")
                return False
            # Recheck schema_version against concurrent duplicate processing.
            if (found.get('schema_version') or 1) >= _SCHEMA_V:
                return False
            found['temporal_scope'] = new_scope
            found['event_when_raw'] = event_when_raw
            found['event_start_at'] = event_start_at
            found['event_end_at'] = event_end_at
            found['schema_version'] = _SCHEMA_V
            try:
                await self.asave_reflections(lanlan_name, current)
            except Exception as e:
                # Persistence failures also count recheck_attempts; otherwise cloudsave maintenance /
                # readonly filesystems / permissions repeatedly rejudge identical reflections every 30s,
                # never tripping breakers. Match LLM-failure increments above.
                # Increment helpers lock themselves; asyncio.Lock is nonreentrant, so call after releasing this lock.
                save_failed_reason = f"save failed: {e}"

        if save_failed_reason is not None:
            logger.warning(
                f"[Recheck-Reflection] {lanlan_name} {rid}: {save_failed_reason}"
            )
            await self._abump_reflection_recheck_attempts(
                lanlan_name, rid, save_failed_reason,
            )
            return False

        logger.info(
            f"[Recheck-Reflection] {lanlan_name} {rid}: v1→v{_SCHEMA_V} "
            f"scope={new_scope} when={event_when_raw}"
        )
        return True

    async def areset_confirmed_at_to_now(self, lanlan_name: str) -> int:
        """On→off migration: reset every confirmed reflection's confirmed_at to now,
        making the time-driven fallback run the full 14-day clock.

        Avoids the jarring experience of "user enabled it for a while, turned
        it off, and old confirmed entries immediately hit the 14-day mark and
        promote in bulk". Returns the number of affected entries.

        Scoped reflections are exempt: their promotion gate counts the days
        they were seen, not confirmed_at, and they never promote with strong
        memory off (see ``aauto_promote_time_driven``), so the on→off
        rationale does not apply to them. Corrupt partial descriptors are exempt too —
        they are excluded from every lifecycle pass, so mutating them here
        would be a pointless write to otherwise-quarantined rows.
        """
        from memory.scopes import is_legacy_private_entry

        async with self._get_alock(lanlan_name):
            reflections = await self._aload_reflections_full(lanlan_name)
            now_iso = datetime.now().isoformat()
            count = 0
            for r in reflections:
                if r.get('status') != 'confirmed':
                    continue
                if not is_legacy_private_entry(r):
                    # Scoped reflections gate on observation days, not
                    # confirmed_at, so a reset would change nothing. Leave corrupt descriptors
                    # untouched too, already isolated by all lifecycle passes.
                    continue
                r['confirmed_at'] = now_iso
                count += 1
            if count:
                active = [
                    r for r in reflections
                    if r.get('status') not in REFLECTION_TERMINAL_STATUSES
                ]
                await self.asave_reflections(lanlan_name, active)
        return count
