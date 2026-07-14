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
"""Evidence flow methods for the memory manager."""

from __future__ import annotations

import asyncio

import json

import os


from datetime import datetime




from utils.cloudsave_runtime import assert_cloudsave_writable


from utils.file_utils import (
    atomic_write_json,
    read_json_async,
)









from memory._reflection.transitions import (
    find_reflection,
)

from ._shared import (
    logger,
)

class EvidenceFlowMixin:
    @staticmethod
    def _find_reflection_in_list(reflections: list[dict], rid: str) -> dict | None:
        return find_reflection(reflections, rid)

    @staticmethod
    def _compute_evidence_after_delta(
        entry: dict, delta: dict, now_iso: str, source: str = 'unknown',
    ) -> dict:
        from memory.evidence import compute_evidence_snapshot
        return compute_evidence_snapshot(entry, delta, now_iso, source)

    async def aapply_signal(
        self, lanlan_name: str, reflection_id: str, delta: dict, source: str,
    ) -> bool:
        """Mutate one reflection's evidence via EVT_REFLECTION_EVIDENCE_UPDATED.

        record_and_save contract (RFC §3.3.3):
          load → append event → mutate view → save view → advance sentinel.

        Returns True if applied; False if reflection not found (LLM may point
        at a stale id; signals are best-effort).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aapply_signal] event_log 未注入；"
                "ReflectionEngine() 构造时须传入 event_log"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {lanlan_name}: aapply_signal 找不到 reflection_id={reflection_id}"
                )
                return False

            now_iso = datetime.now().isoformat()
            snapshot = self._compute_evidence_after_delta(
                entry, delta, now_iso, source,
            )
            payload = {
                'reflection_id': reflection_id,
                'reinforcement': snapshot['reinforcement'],
                'disputation': snapshot['disputation'],
                'rein_last_signal_at': snapshot['rein_last_signal_at'],
                'disp_last_signal_at': snapshot['disp_last_signal_at'],
                'sub_zero_days': snapshot['sub_zero_days'],
                'user_fact_reinforce_count': snapshot['user_fact_reinforce_count'],
                'source': source,
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                entry['reinforcement'] = snapshot['reinforcement']
                entry['disputation'] = snapshot['disputation']
                entry['rein_last_signal_at'] = snapshot['rein_last_signal_at']
                entry['disp_last_signal_at'] = snapshot['disp_last_signal_at']
                entry['sub_zero_days'] = snapshot['sub_zero_days']
                entry['user_fact_reinforce_count'] = snapshot['user_fact_reinforce_count']

            def _sync_save(n: str, view):
                # Gate write behind the same cloudsave check as
                # save_reflections/asave_reflections — the evidence mutation
                # path must honour read-only/maintenance mode (CodeRabbit PR #929).
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return True

    async def aincrement_sub_zero(
        self, lanlan_name: str, reflection_id: str, now: datetime,
    ) -> int | None:
        """Increment one reflection's `sub_zero_days` via EVT_REFLECTION_EVIDENCE_UPDATED.

        Called by the periodic archive sweep (`memory_server._periodic_archive_sweep_loop`).
        Goes through `arecord_and_save` so the increment is audit-logged
        and replayable — derived state but worth recording.

        Returns the new `sub_zero_days` value if incremented, or None if
        no increment happened (entry not found / protected / already
        incremented today / score >= 0).
        """
        from memory.event_log import EVT_REFLECTION_EVIDENCE_UPDATED
        from memory.evidence import maybe_mark_sub_zero
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aincrement_sub_zero] event_log 未注入"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                return None
            # Coderabbit PR #934 round-2 Major #2: probe on a staged copy
            # so the in-memory list is NOT mutated until inside the
            # locked record_and_save critical section. If the event
            # append or save raises, the live entry stays clean (no
            # orphan sub_zero_days increment lacking an audit-log row).
            staged_entry = dict(entry)
            if not maybe_mark_sub_zero(staged_entry, now):
                return None

            new_count = int(staged_entry.get('sub_zero_days', 0) or 0)
            new_date = staged_entry.get('sub_zero_last_increment_date')

            payload = {
                'reflection_id': reflection_id,
                'reinforcement': float(entry.get('reinforcement', 0.0) or 0.0),
                'disputation': float(entry.get('disputation', 0.0) or 0.0),
                'rein_last_signal_at': entry.get('rein_last_signal_at'),
                'disp_last_signal_at': entry.get('disp_last_signal_at'),
                'sub_zero_days': new_count,
                'sub_zero_last_increment_date': new_date,
                'user_fact_reinforce_count': int(
                    entry.get('user_fact_reinforce_count', 0) or 0,
                ),
                'source': 'archive_sweep',
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(_view):
                # Apply staged values to the cached entry only after
                # event append succeeded (record_and_save orders
                # append → mutate → save, so a failure earlier never
                # reaches this callback).
                entry['sub_zero_days'] = new_count
                entry['sub_zero_last_increment_date'] = new_date

            def _sync_save(n: str, view):
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_EVIDENCE_UPDATED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            return new_count

    async def aarchive_reflection(self, lanlan_name: str, reflection_id: str) -> bool:
        """Move one reflection from main view to a sharded archive file.

        RFC §3.5.6: archiving reuses the ``EVT_REFLECTION_STATE_CHANGED`` event
        (no new event types). Payload carries `from`/`to`/`archive_shard_path`
        so the reconciler can replay the full transition.

        Flow (coderabbit PR #934 round-1 + round-2):
          1. Pre-pick the shard path (deterministic basename for the
             event payload).
          2. record_and_save — atomically removes the entry from the
             active view + appends the state_changed event whose
             payload carries `entry_snapshot` so a crash later is
             recoverable.
          3. aappend_to_shard — writes the snapshot into the shard
             file. If this raises, the next reconciler boot's archive
             handler self-heals by recreating the shard from
             `entry_snapshot` (idempotent, see
             `make_reflection_archive_handler`).

        Returns True if archived; False if `reflection_id` not found or
        the entry is `protected`.
        """
        from memory.archive_shards import aappend_to_shard
        from memory.event_log import EVT_REFLECTION_STATE_CHANGED
        if self._event_log is None:
            raise RuntimeError(
                "[Reflection.aarchive_reflection] event_log 未注入；"
                "ReflectionEngine() 构造时须传入 event_log"
            )

        async with self._get_alock(lanlan_name):
            reflections_full = await self._aload_reflections_full(lanlan_name)
            entry = self._find_reflection_in_list(reflections_full, reflection_id)
            if entry is None:
                logger.warning(
                    f"[Reflection] {lanlan_name}: aarchive_reflection 找不到 "
                    f"reflection_id={reflection_id}"
                )
                return False
            if entry.get('protected'):
                logger.debug(
                    f"[Reflection] {lanlan_name}: aarchive_reflection 跳过 protected "
                    f"reflection_id={reflection_id}"
                )
                return False

            prev_status = entry.get('status', 'pending')
            now = datetime.now()
            now_iso = now.isoformat()

            # Predict shard path BEFORE writing so we can stamp the
            # entry's own `archive_shard_path` field to match its on-disk
            # location. We deep-copy the entry so the in-memory original
            # remains untouched until the event log gates the mutation;
            # otherwise a crash between shard-write and event-append
            # would leave `archived_at` on a still-active entry.
            from memory.archive_shards import apick_today_shard_path
            archive_dir = self._reflections_archive_dir(lanlan_name)
            shard_path = await apick_today_shard_path(archive_dir, now=now)
            shard_basename = os.path.basename(shard_path)
            archive_entry = dict(entry)
            archive_entry['archived_at'] = now_iso
            archive_entry['status'] = 'archived'
            archive_entry['archive_shard_path'] = shard_basename

            payload = {
                'reflection_id': reflection_id,
                'from': prev_status,
                'to': 'archived',
                'archive_shard_path': shard_basename,
                'archived_at': now_iso,
                # Symmetry with aarchive_persona_entry — the reflection
                # archive handler in evidence_handlers.py reads this on
                # every replay and idempotently recreates the shard if
                # it's missing (coderabbit PR #934 round-2 Major #3).
                # Crash between record_and_save and the shard append
                # below is healed on the next reconciler boot.
                'entry_snapshot': archive_entry,
            }

            def _sync_load(_n: str):
                return reflections_full

            def _sync_mutate(view):
                # Drop the archived entry from active list (in-place
                # mutate, since `view` IS `reflections_full`).
                view[:] = [r for r in view if r.get('id') != reflection_id]

            def _sync_save(n: str, view):
                assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    self._reflections_path(n), view, indent=2, ensure_ascii=False,
                )

            # ORDER (coderabbit review #934 round-1 + round-2):
            # 1. record_and_save first (commits event + active-view
            #    removal atomically). Avoids the "shard duplicate +
            #    still-active entry" race where a crash between shard
            #    write and view save would let the next sweep
            #    re-archive into a second shard slot.
            # 2. aappend_to_shard second. If THIS step crashes (or
            #    raises), the active view has already lost the entry
            #    but the shard never got it. Self-heal: the
            #    state_changed handler in evidence_handlers.py reads
            #    `entry_snapshot` from the payload and re-creates the
            #    shard on the next reconciler boot — the event log is
            #    the source of truth (RFC §3.11) and the snapshot
            #    keeps the data recoverable.
            await self._event_log.arecord_and_save(
                lanlan_name, EVT_REFLECTION_STATE_CHANGED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            await aappend_to_shard(archive_dir, [archive_entry], now=now)
            logger.info(
                f"[Reflection] {lanlan_name}: 归档 reflection {reflection_id} "
                f"(score-driven, prev_status={prev_status}) → {shard_basename}"
            )
            return True

    async def aone_shot_archive_migration(self, lanlan_name: str) -> bool:
        """Migrate legacy flat ``reflections_archive.json`` → sharded dir.

        RFC §3.5.5: idempotent one-shot, runs at startup. Returns True if
        a migration actually happened, False if it was a no-op (file
        absent or sentinel already present).

        Failure logs but does NOT raise — boot must not stall on archive
        migration. Worst case: the flat file stays in place and the next
        boot re-tries.
        """
        from memory.archive_shards import amigrate_flat_archive_to_shards
        flat_path = self._reflections_legacy_archive_path(lanlan_name)
        archive_dir = self._reflections_archive_dir(lanlan_name)
        try:
            migrated, n_entries, n_shards = await amigrate_flat_archive_to_shards(
                flat_path, archive_dir,
            )
        except Exception as e:
            logger.warning(
                f"[Reflection] {lanlan_name}: 旧 reflections_archive 迁移失败 "
                f"(保留原文件 fallback): {e}"
            )
            return False
        if migrated:
            logger.info(
                f"[Reflection] {lanlan_name}: 旧 reflections_archive 迁移完成 "
                f"({n_entries} 条 → {n_shards} 分片)"
            )
        return migrated

    async def _aload_reflections_full(self, name: str) -> list[dict]:
        """Like aload_reflections(include_archived=True) but also keeps
        `merged` entries. Needed for aapply_signal + score-driven promote
        paths — we need to reach any non-active reflection by id as well."""
        path = self._reflections_path(name)
        if not await asyncio.to_thread(os.path.exists, path):
            return []
        try:
            data = await read_json_async(path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Reflection] 加载失败: {e}")
            return []
        if not isinstance(data, list):
            return []
        return [
            self._normalize_reflection(item)
            for item in data if isinstance(item, dict) and 'id' in item
        ]
