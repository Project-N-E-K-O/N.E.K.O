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
"""Promotion merge methods for the memory manager."""

from __future__ import annotations





from datetime import datetime


from config import (
    EVIDENCE_PROMOTE_MAX_RETRIES,
    EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES,
    EVIDENCE_PROMOTED_THRESHOLD,
    EVIDENCE_PROMOTION_MERGE_MODEL_TIER,
)

from memory.evidence import evidence_score

from utils.cloudsave_runtime import assert_cloudsave_writable


from utils.file_utils import (
    atomic_write_json,
    robust_json_loads,
)


from utils.token_tracker import set_call_type







from memory._reflection.transitions import (
    compute_merged_evidence,
)

from ._shared import (
    logger,
)

class PromotionMergeMixin:
    @staticmethod
    def _compute_merged_evidence(
        target: dict, reflection: dict,
    ) -> tuple[float, float]:
        """Conservative max-rule for merging two evidence pairs (RFC §3.9.5).

        Why max not sum: sum would inflate evidence whenever the reflection
        and the target persona entry were independently reinforced from the
        same underlying user signals (Stage-2 + check_feedback dual-pickup
        is the canonical case). max represents "the strongest user assertion
        across either witness" — never invents evidence the user never gave.
        """
        return compute_merged_evidence(target, reflection)

    @staticmethod
    def _within_backoff(reflection: dict, now: datetime | None = None) -> bool:
        """Throttle gate (RFC §3.9.2): True if last attempt was within
        EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES of `now`."""
        last = reflection.get('last_promote_attempt_at')
        if not last:
            return False
        if now is None:
            now = datetime.now()
        try:
            last_ts = datetime.fromisoformat(last)
        except (ValueError, TypeError):
            return False
        elapsed_min = (now - last_ts).total_seconds() / 60.0
        return elapsed_min < EVIDENCE_PROMOTE_RETRY_BACKOFF_MINUTES

    @staticmethod
    def _exceeds_max_retries(reflection: dict) -> bool:
        return (
            int(reflection.get('promote_attempt_count', 0) or 0)
            >= EVIDENCE_PROMOTE_MAX_RETRIES
        )

    async def _arecord_promote_attempt(
        self, name: str, reflection_id: str, now_iso: str,
    ) -> None:
        """Public wrapper: acquires the per-character lock then delegates
        to `_arecord_promote_attempt_locked`. See that method for details.
        """
        async with self._get_alock(name):
            await self._arecord_promote_attempt_locked(
                name, reflection_id, now_iso,
            )

    async def _arecord_promote_attempt_locked(
        self, name: str, reflection_id: str, now_iso: str,
    ) -> None:
        """Increment `promote_attempt_count` and stamp
        `last_promote_attempt_at` via EVT_REFLECTION_EVIDENCE_UPDATED.

        Caller MUST hold `self._get_alock(name)`. This locked variant
        exists so `_apromote_with_merge` can fuse throttle-check +
        attempt-record into a single critical section (CodeRabbit PR
        #936 round-5 Major #3) — otherwise two concurrent invocations
        could both pass the pre-lock backoff check and both record
        attempts, defeating the throttle.

        Why an evidence event for a counter (not a state change): the
        throttle counters live alongside evidence on each reflection and
        must be event-sourced so reconciler replay restores them after
        crash. Reusing the existing evidence handler avoids inventing a
        third event type — the handler whitelists keys it copies, and
        `promote_attempt_count` / `last_promote_attempt_at` are now on the
        whitelist (see evidence_handlers._EVIDENCE_SNAPSHOT_KEYS).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection._arecord_promote_attempt] event_log 未注入"
            )

        reflections_full = await self._aload_reflections_full(name)
        entry = self._find_reflection_in_list(reflections_full, reflection_id)
        if entry is None:
            logger.warning(
                f"[Reflection] {name}: _arecord_promote_attempt 找不到 "
                f"reflection_id={reflection_id}"
            )
            return

        new_count = int(entry.get('promote_attempt_count', 0) or 0) + 1
        payload = {
            'reflection_id': reflection_id,
            # Full-snapshot evidence values (unchanged by this event,
            # but present so the handler+replay see the consistent view).
            'reinforcement': float(entry.get('reinforcement', 0.0) or 0.0),
            'disputation': float(entry.get('disputation', 0.0) or 0.0),
            'rein_last_signal_at': entry.get('rein_last_signal_at'),
            'disp_last_signal_at': entry.get('disp_last_signal_at'),
            'sub_zero_days': int(entry.get('sub_zero_days', 0) or 0),
            'user_fact_reinforce_count':
                int(entry.get('user_fact_reinforce_count', 0) or 0),
            # New throttle fields — whitelisted in the handler.
            'last_promote_attempt_at': now_iso,
            'promote_attempt_count': new_count,
            'source': 'promote_attempt',
        }

        def _sync_load(_n: str):
            return reflections_full

        def _sync_mutate(_view):
            entry['last_promote_attempt_at'] = now_iso
            entry['promote_attempt_count'] = new_count

        def _sync_save(n: str, view):
            assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{n}/reflections.json",
            )
            atomic_write_json(
                self._reflections_path(n), view,
                indent=2, ensure_ascii=False,
            )

        await self._event_log.arecord_and_save(
            name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
            sync_load_view=_sync_load,
            sync_mutate_view=_sync_mutate,
            sync_save_view=_sync_save,
        )

    async def _arecord_state_change(
        self, name: str, reflection_id: str, from_status: str, to_status: str,
        *, absorbed_into: str | None = None, reason: str | None = None,
        reject_explanation: str | None = None,
    ) -> None:
        """Mutate a reflection's `status` and emit
        EVT_REFLECTION_STATE_CHANGED for audit + reconciler replay.

        Replay path: `make_reflection_state_changed_composite` in
        `memory/evidence_handlers.py` dispatches on the `to` field —
        `'archived'` routes to the archive handler (PR-2, RFC §3.5),
        any other status routes to `make_reflection_state_changed_handler`
        which re-applies `status`, `<status>_at` timestamp,
        `absorbed_into`, `promote_blocked_reason` (when to='promote_blocked'),
        `denied_reason` (when to='denied'), and `reject_reason` onto the
        reflection entry keyed by `reflection_id`. The persisted view
        updated in this same record_and_save call is therefore
        redundant with the replay path — both paths are kept (view for
        live reads, event for crash recovery). See RFC §3.9.6.
        """
        from memory.event_log import EVT_REFLECTION_STATE_CHANGED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection._arecord_state_change] event_log 未注入"
            )

        async with self._get_alock(name):
            reflections_full = await self._aload_reflections_full(name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {name}: _arecord_state_change 找不到 "
                    f"reflection_id={reflection_id}"
                )
                return

            # Compare-and-swap on `from_status`. _apromote_with_merge runs
            # the LLM call OUTSIDE the per-character lock (LLM is slow); a
            # concurrent rebuttal / archive / parallel promote can flip the
            # status during that window. Without this guard the late writer
            # silently overwrites a newer terminal status (e.g. promote
            # clobbers a freshly-set `denied` from rebuttal), losing the
            # newer signal AND emitting a misleading state-change event
            # whose `from` no longer matches the on-disk view.
            current_status = entry.get('status')
            if current_status != from_status:
                logger.warning(
                    f"[Reflection] {name}/{reflection_id}: "
                    f"_arecord_state_change CAS miss — expected from={from_status!r} "
                    f"but current status is {current_status!r}; dropping "
                    f"transition to {to_status!r} (newer signal wins)"
                )
                return

            now_iso = datetime.now().isoformat()
            payload: dict = {
                'reflection_id': reflection_id,
                'from': from_status,
                'to': to_status,
                'ts': now_iso,
            }
            if absorbed_into is not None:
                payload['absorbed_into'] = absorbed_into
            if reason is not None:
                payload['reason'] = reason
            if reject_explanation is not None:
                payload['reject_explanation'] = reject_explanation

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                entry['status'] = to_status
                entry[f'{to_status}_at'] = now_iso
                if absorbed_into is not None:
                    entry['absorbed_into'] = absorbed_into
                if reason is not None:
                    # Route the `reason` audit string into a status-specific
                    # field so the semantics of each terminal-state field
                    # stay clean (RFC §3.9.2 / §3.9.7):
                    #   - promote_blocked → `promote_blocked_reason` (the
                    #     throttle/dead-letter cause; consumed by the dead-
                    #     letter retry path).
                    #   - denied → `denied_reason` (the rejection category;
                    #     audit only, no recovery path).
                    #   - merged → `absorbed_into` already captures
                    #     provenance; no separate reason needed and the
                    #     callers don't pass one.
                    # Without this gate any non-None reason on a denied/
                    # merged transition would pollute promote_blocked_reason,
                    # making dead-letter scans see false-positive blocks.
                    # Mirror of the reconciler handler in
                    # `make_reflection_state_changed_handler`, which already
                    # gates the same way on replay.
                    if to_status == 'promote_blocked':
                        entry['promote_blocked_reason'] = reason
                    elif to_status == 'denied':
                        entry['denied_reason'] = reason
                if reject_explanation is not None:
                    # Keep audit trail off the throttle field — separate key
                    # so promote_blocked vs llm_merge_rejected stays distinct.
                    entry['reject_reason'] = reject_explanation

            def _sync_save(n: str, view):
                # Save via _prepare_save_reflections so terminal entries
                # (merged / promote_blocked) round-trip correctly.
                # asave_reflections handles the merge with on-disk state.
                # We're already inside the per-character lock, so call the
                # raw save path (`asave_reflections` re-acquires no lock).
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view,
                    indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                name, EVT_REFLECTION_STATE_CHANGED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )

    async def _amark_promote_blocked(
        self, name: str, reflection: dict, reason: str,
    ) -> None:
        """Move reflection to promote_blocked dead-letter (RFC §3.9.2).

        Distinct from `_arecord_state_change` to keep the throttle-vs-merge
        decision points readable in `_apromote_with_merge`.
        """
        await self._arecord_state_change(
            name, reflection['id'], 'confirmed', 'promote_blocked',
            reason=reason,
        )

    async def _allm_call_promotion_merge(
        self, R: dict, persona_pool: list[tuple[str, dict]],
        reflection_pool: list[dict], lanlan_name: str, master_name: str,
    ) -> dict:
        """LLM call producing the merge decision JSON (RFC §3.9.7 prompt).

        Returns the parsed dict on success. Raises on any LLM / parse
        failure — caller (`_apromote_with_merge`) catches and treats as
        skip_retry_pending per §3.9.4.
        """
        from config.prompts.prompts_memory import get_promotion_merge_prompt
        from utils.language_utils import get_global_language
        from utils.llm_client import create_chat_llm_async

        now = datetime.now()
        # Build the impression pool block with stable ordering — protected
        # persona entries first, then non-protected by score DESC, then
        # confirmed/promoted reflections by score DESC.
        pool_lines: list[str] = []
        for ek, e in persona_pool:
            pool_lines.append(
                f"[persona.{ek}.{e.get('id')}] \"{e.get('text', '')}\""
                f" (evidence_score={evidence_score(e, now):.2f})"
            )
        for r in reflection_pool:
            pool_lines.append(
                f"[reflection.{r.get('id')}] \"{r.get('text', '')}\""
                f" (evidence_score={evidence_score(r, now):.2f})"
            )
        pool_text = "\n".join(pool_lines) if pool_lines else "(印象池为空)"
        # Cap the impression pool at PERSONA_MERGE_POOL_MAX_TOKENS — same
        # entity 长期累积下来 persona+reflection 池可能超 8k tokens；
        # 这里整段截尾（按 score DESC 已排序，超出的是低分项，可丢）。
        from config import PERSONA_MERGE_POOL_MAX_TOKENS
        from utils.tokenize import truncate_to_tokens
        pool_text = truncate_to_tokens(pool_text, PERSONA_MERGE_POOL_MAX_TOKENS)

        prompt = get_promotion_merge_prompt(get_global_language()).format(
            AI_NAME=lanlan_name,
            MASTER_NAME=master_name,
            R_TEXT=R.get('text', ''),
            R_SCORE=f"{evidence_score(R, now):.2f}",
            IMPRESSION_POOL=pool_text,
        )

        set_call_type("memory_promote_merge")
        api_config = self._config_manager.get_model_api_config(
            EVIDENCE_PROMOTION_MERGE_MODEL_TIER,
        )
        # timeout=90: 开 thinking 后 promote merge 决策（merge_into / promote_fresh /
        # reject + target_id 选择 + 重写 merged_text）值得思考——后果不可逆
        # （persona pollution），已有 throttle/backoff/dead-letter 兜底，开
        # thinking 完全在收益侧。LLM 调用本身在锁外（pre/post 短临界区分别拿
        # reflection 锁做 stamp 和 CAS），所以 90s 不阻塞同角色其他 reflection 写。
        # max_retries=0: 禁 SDK 自动重试，由 throttle/dead-letter 兜底。
        # extra_body=None: 显式开 thinking。
        from config import LLM_OUTPUT_GUARD_MAX_TOKENS
        llm = await create_chat_llm_async(
            api_config['model'],
            api_config['base_url'], api_config['api_key'],
            timeout=90, max_retries=0,
            max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; generous so variable-length JSON (incl. thinking) isn't truncated
            extra_body=None,
            provider_type=api_config.get('provider_type'),
        )
        try:
            resp = await llm.ainvoke(prompt)
        finally:
            await llm.aclose()
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.replace("```json", "").replace("```", "").strip()
        decision = robust_json_loads(raw)
        if not isinstance(decision, dict):
            raise ValueError(
                f"LLM merge decision is not a dict: {type(decision).__name__}"
            )
        return decision

    async def _apromote_with_merge(
        self, lanlan_name: str, R: dict,
    ) -> str:
        """Score-driven confirmed → promoted via LLM merge decision
        (RFC §3.9.3).

        Returns one of:
          'promote_fresh' | 'merge_into' | 'reject' | 'reject_by_persona'
          | 'queued_correction' | 'skip_retry_pending' | 'blocked'
          | 'invalid_target' | 'noop' | 'no_longer_eligible'

        Throttling: backoff window before retry, max retries → dead-letter.
        LLM failures → skip_retry_pending (NOT promote_fresh — RFC §3.9.4
        explicitly forbids silent fresh-fallback because that breaks
        dedup semantics during outages).
        """
        # Pre-lock advisory throttle check — cheap early-exit when the
        # reflection is obviously inside its backoff window. The
        # authoritative throttle + attempt-record fuse happens under
        # the lock below (CodeRabbit PR #936 round-5 Major #3); this
        # pre-check just saves a lock acquisition in the common case.
        now = datetime.now()
        if self._within_backoff(R, now):
            return 'skip_retry_pending'
        if self._exceeds_max_retries(R):
            await self._amark_promote_blocked(
                lanlan_name, R, 'llm_unavailable',
            )
            return 'blocked'

        # Revalidate snapshot + throttle gate + record attempt — ALL
        # inside the same lock section. CodeRabbit PR #936 round-5
        # Major #3: without this fusion, two concurrent invocations
        # can both pass the pre-lock backoff check, both take the lock
        # serially, and both record attempts — defeating the throttle
        # and double-bumping promote_attempt_count toward the
        # max-retries dead-letter.
        #
        # The caller (`_aauto_promote_score_driven`) iterates a stale
        # snapshot collected outside the per-character lock; by the
        # time we reach this point another coroutine may have demoted,
        # denied, merged or otherwise disqualified `R`. Bumping
        # `promote_attempt_count` for an already-merged reflection
        # both wastes a retry slot and emits a misleading evidence
        # event. Re-read the on-disk view, find the same id, and
        # short-circuit if it's no longer eligible.
        async with self._get_alock(lanlan_name):
            current_list = await self._aload_reflections_full(lanlan_name)
            current = self._find_reflection_in_list(current_list, R['id'])
            if current is None or current.get('status') != 'confirmed':
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: no longer eligible "
                    f"(current status={current.get('status') if current else 'gone'!r}); "
                    f"skip"
                )
                return 'no_longer_eligible'
            if evidence_score(current, now) < EVIDENCE_PROMOTED_THRESHOLD:
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: evidence_score "
                    f"dropped below threshold under lock; skip"
                )
                return 'no_longer_eligible'
            # Re-check throttle against the freshly-read entry — this
            # closes the race where another coroutine recorded an
            # attempt between our pre-lock check and our lock
            # acquisition.
            if self._within_backoff(current, now):
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: another attempt "
                    f"recorded during lock wait; skip_retry_pending"
                )
                return 'skip_retry_pending'
            if self._exceeds_max_retries(current):
                # Must release lock before calling _amark_promote_blocked
                # (it re-acquires the same lock via _arecord_state_change).
                R = current
                break_for_blocked = True
            else:
                break_for_blocked = False
                # Use the freshly-read entry from here on so downstream
                # merge math sees the latest evidence values.
                R = current
                # Record the attempt INSIDE the lock so the throttle
                # stamp lands atomically with the eligibility decision.
                # `_arecord_promote_attempt_locked` expects the caller
                # to hold the lock — we do.
                now_iso = datetime.now().isoformat()
                await self._arecord_promote_attempt_locked(
                    lanlan_name, R['id'], now_iso,
                )

        if break_for_blocked:
            await self._amark_promote_blocked(
                lanlan_name, R, 'llm_unavailable',
            )
            return 'blocked'

        # Build candidate pool (RFC §3.9.3 step 1) — outside the lock
        # because same_entity_persona / same_entity_reflections are
        # advisory inputs to the LLM; stale reads cost at most a
        # suboptimal merge decision, not correctness.
        persona_view = await self._persona_manager.aget_persona(lanlan_name)
        target_entity = R.get('entity')
        same_entity_persona: list[tuple[str, dict]] = []
        if target_entity and isinstance(persona_view, dict):
            section = persona_view.get(target_entity)
            if isinstance(section, dict):
                for e in section.get('facts', []):
                    if isinstance(e, dict) and not e.get('protected'):
                        same_entity_persona.append((target_entity, e))
        all_reflections = await self._aload_reflections_full(lanlan_name)
        same_entity_reflections = [
            r for r in all_reflections
            if r.get('entity') == target_entity
            and r.get('status') in ('confirmed', 'promoted')
            and r.get('id') != R.get('id')
        ]

        try:
            _, _, _, _, name_mapping, _, _, _, _ = (
                await self._config_manager.aget_character_data()
            )
            master_name = name_mapping.get('human', '主人')
            decision = await self._allm_call_promotion_merge(
                R, same_entity_persona, same_entity_reflections,
                lanlan_name, master_name,
            )
        except Exception as e:  # noqa: BLE001
            # RFC §3.9.4: LLM failure does NOT downgrade to promote_fresh.
            # Reflection stays confirmed; throttle state was already bumped
            # above. Next cycle (after backoff) will retry; max-retries
            # tips the reflection into promote_blocked dead-letter.
            logger.warning(
                f"[Promote] {lanlan_name}/{R['id']}: LLM merge call failed: "
                f"{e}; reflection stays confirmed for retry"
            )
            return 'skip_retry_pending'

        action = decision.get('action')

        # Post-LLM revalidation (round-2 review). The pre-LLM check above
        # only fences the snapshot up to the LLM await; the LLM call itself
        # is multi-second. During that window another coroutine (rebuttal,
        # parallel promote of a duplicate signal, archive sweep) can flip
        # status to denied / merged / promote_blocked. Without re-checking
        # here, the post-LLM `aadd_fact` / `amerge_into` will write to
        # persona FIRST, and only then will `_arecord_state_change`'s CAS
        # guard refuse the status flip — leaving persona polluted with a
        # fact whose source reflection is no longer eligible. Re-read the
        # status under the lock; bail out cleanly if the gate has closed.
        async with self._get_alock(lanlan_name):
            current_list2 = await self._aload_reflections_full(lanlan_name)
            current2 = self._find_reflection_in_list(current_list2, R['id'])
            if current2 is None or current2.get('status') != 'confirmed':
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: status changed "
                    f"during LLM await (now "
                    f"{current2.get('status') if current2 else 'gone'!r}); "
                    f"discarding LLM decision={action!r} without persona write"
                )
                return 'no_longer_eligible'
            if evidence_score(current2, datetime.now()) < EVIDENCE_PROMOTED_THRESHOLD:
                logger.info(
                    f"[Promote] {lanlan_name}/{R['id']}: evidence_score "
                    f"dropped below threshold during LLM await; "
                    f"discarding LLM decision={action!r}"
                )
                return 'no_longer_eligible'
            # Refresh R from the freshly-read view so any merge-evidence
            # math below sees current values, not the pre-LLM snapshot.
            R = current2

        if action == 'promote_fresh':
            result = await self._persona_manager.aadd_fact(
                lanlan_name, R.get('text', ''),
                entity=target_entity or 'master',
                source='reflection', source_id=R['id'],
            )
            if result == self._persona_manager.FACT_ADDED:
                await self._arecord_state_change(
                    lanlan_name, R['id'], 'confirmed', 'promoted',
                )
                return 'promote_fresh'
            # FACT_REJECTED_CARD: contradicts character_card → reflection
            # is permanently denied (no recovery path; the card is fixed).
            if result == self._persona_manager.FACT_REJECTED_CARD:
                await self._arecord_state_change(
                    lanlan_name, R['id'], 'confirmed', 'denied',
                    reason=f'rejected_by_persona_add:{result}',
                )
                return 'reject_by_persona'
            # FACT_QUEUED_CORRECTION: contradicts an EXISTING non-card fact;
            # PersonaManager has queued an async LLM correction. The user's
            # confirming intent (which got the reflection to confirmed) is
            # NOT denied — the correction queue may resolve in either
            # direction once the LLM weighs in. Keep the reflection in
            # `confirmed` so a future promote cycle can revisit it after
            # backoff (or after the correction queue has resolved); the
            # throttle counter we already bumped this round prevents a
            # tight retry loop.
            logger.info(
                f"[Promote] {lanlan_name}/{R['id']}: aadd_fact returned "
                f"{result} (correction queued); reflection stays confirmed "
                f"for retry after correction resolves"
            )
            return 'queued_correction'

        if action == 'merge_into':
            target_id = decision.get('target_id')
            merged_text = decision.get('merged_text')
            if (not isinstance(target_id, str)
                    or not target_id.startswith('persona.')
                    or not isinstance(merged_text, str)
                    or not merged_text.strip()):
                # LLM returned malformed merge_into — RFC §3.9.7 constrains
                # target_id to persona.* prefix; reject silently as parse fail.
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: invalid merge_into "
                    f"target_id={target_id!r} merged_text empty/non-str; "
                    f"treating as skip_retry_pending"
                )
                return 'invalid_target'
            # `target_id` is fully-qualified (persona.<entity>.<entry_id>);
            # PersonaManager keys entries by entry_id alone so strip the
            # prefix here. Format: persona.<entity_key>.<entry_id_with_dots_ok>
            #   "persona.master.card_master_abc" -> "card_master_abc"
            #   "persona.master.prom_ref_xyz" -> "prom_ref_xyz"
            parts = target_id.split('.', 2)
            if len(parts) < 3:
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: target_id "
                    f"{target_id!r} missing entity/entry segments"
                )
                return 'invalid_target'
            target_entry_id = parts[2]

            # Pre-flight existence check (under PersonaManager's read
            # path). Note: the canonical re-lookup AND the conservative
            # max-rule evidence aggregation now both happen INSIDE
            # `amerge_into` under the per-character lock — see CodeRabbit
            # PR #936 round-6 Major #2. We pass the reflection's own
            # evidence values; `amerge_into` does the max() against the
            # locked target entry's current evidence so a concurrent
            # `aapply_signal` (or another merge) cannot be rolled back
            # by a stale snapshot computed up here.
            persona_view2 = await self._persona_manager.aget_persona(lanlan_name)
            target_entity_key, target_entry = (
                self._persona_manager._find_entry_with_section(
                    persona_view2, target_entry_id,
                )
            )
            if target_entry is None:
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: merge target "
                    f"{target_entry_id} not found in persona; skip"
                )
                return 'invalid_target'

            merge_outcome = await self._persona_manager.amerge_into(
                lanlan_name, target_entry_id, merged_text,
                reflection_evidence={
                    'reinforcement': float(R.get('reinforcement', 0.0) or 0.0),
                    'disputation': float(R.get('disputation', 0.0) or 0.0),
                },
                source_reflection_id=R['id'],
                merged_from_ids=[R['id']],
            )
            if merge_outcome == 'not_found':
                logger.warning(
                    f"[Promote] {lanlan_name}/{R['id']}: amerge_into reported "
                    f"not_found mid-flight; treating as invalid_target"
                )
                return 'invalid_target'
            # 'merged' or 'noop' → both leave the persona in the desired
            # post-merge state, so reflection should flip to merged either way.
            await self._arecord_state_change(
                lanlan_name, R['id'], 'confirmed', 'merged',
                absorbed_into=target_entry_id,
            )
            # Surface tracking: treat merge as a confirm-equivalent terminal
            await self._abatch_mark_surfaced_handled(
                lanlan_name, [R['id']], 'confirmed',
            )
            return 'merge_into'

        if action == 'reject':
            await self._arecord_state_change(
                lanlan_name, R['id'], 'confirmed', 'denied',
                reason='llm_merge_rejected',
                reject_explanation=decision.get('reason'),
            )
            return 'reject'

        # Unknown action — treat as parse failure per §3.9.4 (do NOT
        # downgrade to promote_fresh). Throttle counter already bumped.
        logger.warning(
            f"[Promote] {lanlan_name}/{R['id']}: LLM returned unknown action "
            f"{action!r}; skip_retry_pending"
        )
        return 'skip_retry_pending'
