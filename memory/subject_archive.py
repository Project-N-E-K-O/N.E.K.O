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

"""Time-driven archival for scoped (group / participant) memory subjects.

Scoped entries are structurally unreachable by the score-driven evidence
archival: scoped facts are written with ``signal_processed=True``, scoped
reflections skip the pending/surfacing feedback loop, and persona entries
start at reinforcement=0 / disputation=0 — so ``evidence_score`` never goes
below zero and ``sub_zero_days`` never accumulates. Without this module a
subject that went silent forever keeps its facts in recall and its persona
sections in the render candidate pool.

The staleness ledger is THE DATA ITSELF: a subject's last write time is
derived per sweep as ``max(created_at / confirmed_at)`` over every row that
carries the subject's stamp, across the full fact pool (active + archive),
the full reflection list, and the persona sections. There is deliberately
no sidecar file — PR#2394's lesson is that a ledger which outlives a data
rollback permanently desyncs; a derived ledger cannot.

Archival is NOT deletion, per store:

* facts    — moved into ``facts_archive.json`` with a ``subject_archived_at``
             stamp (``FactStore._archive_subject_facts``); both recall paths
             filter the stamp out of the archive pool, and the FTS dedup
             guard ignores stamped rows so a revived subject can land
             re-stated facts.
* refl.    — the existing event-sourced ``aarchive_reflection`` path
             (physical move into archive shards, snapshot in the event).
* persona  — the existing event-sourced ``aarchive_persona_entry`` path.

``arestore_scoped_subject`` reverses all three. Restore appends compensating
events for the reflection / persona stores; a full event-log replay after a
sentinel reset re-applies the archive transitions (the restore events replay
as no-ops), in which case restore can simply be re-run — shard copies are
never destroyed.

Revival semantics: new writes for an archived subject flow through the
normal pipeline and make the subject non-stale again; previously archived
entries stay archived unless restored explicitly.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta

from memory.scopes import MemorySubject, entry_matches_subject, subject_from_entry
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Memory")


# 「最后写入时间」认这两个字段：fact/reflection 落盘都带 created_at；
# scoped reflection 另有 confirmed_at。persona 条目通常两者皆无——它们
# 是晋升派生物，贡献不了时间戳也无妨（facts 是权威写入面）。派生管线
# （合成/晋升/refine）产出的新条目会把 last_write 往后推，方向保守：
# 只会推迟归档，绝不会提前。
_TIMESTAMP_FIELDS = ('created_at', 'confirmed_at')


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def collect_subject_last_writes(
    row_groups,
) -> tuple[dict[tuple, tuple[MemorySubject, datetime]], set[tuple]]:
    """Derive per-subject last-write times from stamped rows.

    ``row_groups`` is an iterable of row iterables (facts, reflections,
    persona entries…). Returns ``(last_writes, no_timestamp_markers)``:
    ``last_writes`` maps ``(subject.key, scope)`` to the subject and its
    newest parseable timestamp; ``no_timestamp_markers`` holds subjects
    that were seen but had ZERO parseable timestamps anywhere — those are
    excluded from archival entirely (fail-closed: unknown age must never
    mean "old enough to archive").
    """
    last: dict[tuple, tuple[MemorySubject, datetime]] = {}
    no_ts: set[tuple] = set()
    for rows in row_groups:
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            subject = subject_from_entry(row)
            if subject is None:
                continue
            marker = (subject.key, subject.scope)
            ts: datetime | None = None
            for field in _TIMESTAMP_FIELDS:
                parsed = _parse_iso(row.get(field))
                if parsed is not None and (ts is None or parsed > ts):
                    ts = parsed
            if ts is None:
                no_ts.add(marker)
                continue
            cur = last.get(marker)
            if cur is None or ts > cur[1]:
                last[marker] = (subject, ts)
    # 有任一带时间戳行的 subject 不算「无时间戳」。
    return last, no_ts - set(last)


def find_stale_subjects(
    last_writes: dict[tuple, tuple[MemorySubject, datetime]],
    *,
    now: datetime,
    stale_days: int,
) -> list[tuple[MemorySubject, datetime]]:
    """Return subjects whose age strictly exceeds ``stale_days``.

    Exactly ``stale_days`` old is NOT stale (the spec is "over N days").
    A last write in the future (clock rollback) yields a negative age and
    is likewise not stale — never archive on a clock anomaly.
    """
    cutoff = timedelta(days=stale_days)
    stale = [
        (subject, last_dt)
        for subject, last_dt in last_writes.values()
        if now - last_dt > cutoff
    ]
    stale.sort(key=lambda t: (t[0].key, t[0].scope))
    return stale


def _domain_marker(subject: MemorySubject) -> str:
    """Log-safe domain identifier (never member-derived content)."""
    return f"{subject.kind}/{subject.subject_id} scope={subject.scope}"


def _iter_persona_entries(persona: dict):
    """Yield ``(section_key, entry)`` over every dict entry in every section."""
    for section_key, section in list(persona.items()):
        if not isinstance(section, dict):
            continue
        for entry in section.get('facts', []) or []:
            if isinstance(entry, dict):
                yield section_key, entry


async def asweep_scoped_subject_archive(
    name: str,
    *,
    fact_store,
    persona_manager,
    reflection_engine,
    now: datetime | None = None,
    stale_days: int | None = None,
    dry_run: bool | None = None,
) -> dict:
    """One time-driven archival pass over one character's scoped subjects.

    Judgement is derived from data (see module docstring), so a crash
    mid-pass is self-healing: the next sweep recomputes the same last-write
    times and archives whatever is still active. Already-fully-archived
    subjects have no active rows left and are skipped without logging noise.

    Logs only domain markers and counts — never member-derived text, per
    the scoped logging contract shared with extraction / dead-letter paths.
    """
    from config import (
        SCOPED_SUBJECT_ARCHIVE_DRY_RUN,
        SCOPED_SUBJECT_STALE_DAYS,
    )
    if now is None:
        now = datetime.now()
    if stale_days is None:
        stale_days = SCOPED_SUBJECT_STALE_DAYS
    if dry_run is None:
        dry_run = SCOPED_SUBJECT_ARCHIVE_DRY_RUN

    facts_full = await fact_store.aload_facts_full(name)
    reflections_full = await reflection_engine._aload_reflections_full(name)
    persona = await persona_manager.aensure_persona(name)
    persona_entries = list(_iter_persona_entries(persona))

    last_writes, no_ts = collect_subject_last_writes([
        facts_full,
        reflections_full,
        [entry for _, entry in persona_entries],
    ])
    if no_ts:
        # fail-closed：完全没有可解析时间戳的 subject 永不归档，但要留痕
        # ——判据写错就是静默丢记忆，反向（该归的没归）也要可观测。
        logger.debug(
            f"[SubjectArchive] {name}: {len(no_ts)} 个 subject 无可解析时间戳，"
            f"跳过归档判定"
        )

    stale = find_stale_subjects(last_writes, now=now, stale_days=stale_days)
    report: dict = {
        'subjects_seen': len(last_writes),
        'subjects_stale': len(stale),
        'subjects_no_timestamp': len(no_ts),
        'dry_run': bool(dry_run),
        'archived': {},
    }
    if not stale:
        return report

    active_facts = await fact_store.aload_facts(name)
    active_refls = await reflection_engine.aload_reflections(name)
    now_iso = now.isoformat()

    for subject, last_dt in stale:
        fact_targets = [
            f for f in active_facts
            if isinstance(f, dict) and entry_matches_subject(f, subject)
        ]
        refl_targets = [
            r for r in active_refls
            if isinstance(r, dict)
            and entry_matches_subject(r, subject)
            and not r.get('protected')
            and r.get('id')
        ]
        persona_targets = [
            (section_key, entry.get('id'))
            for section_key, entry in persona_entries
            if entry_matches_subject(entry, subject)
            and not entry.get('protected')
            and entry.get('id')
        ]
        if not fact_targets and not refl_targets and not persona_targets:
            # 上一轮已归干净（或本就无活跃条目），静默跳过。
            continue

        counts = {
            'facts': len(fact_targets),
            'reflections': len(refl_targets),
            'persona_entries': len(persona_targets),
            'last_write': last_dt.isoformat(),
        }
        if dry_run:
            logger.info(
                f"[SubjectArchive][dry-run] {name}: 将归档 subject "
                f"[{_domain_marker(subject)}] facts={counts['facts']} "
                f"reflections={counts['reflections']} "
                f"persona={counts['persona_entries']} "
                f"(last_write={counts['last_write']})"
            )
            report['archived'][f"{subject.key}|{subject.scope}"] = counts
            continue

        archived_counts = {'facts': 0, 'reflections': 0, 'persona_entries': 0,
                           'last_write': last_dt.isoformat()}
        if fact_targets:
            try:
                archived_counts['facts'] = await fact_store.aarchive_subject_facts(
                    name, subject, now_iso,
                )
            except Exception as e:
                logger.warning(
                    f"[SubjectArchive] {name}: subject "
                    f"[{_domain_marker(subject)}] facts 归档失败: {e}"
                )
        for r in refl_targets:
            try:
                if await reflection_engine.aarchive_reflection(name, r['id']):
                    archived_counts['reflections'] += 1
            except Exception as e:
                logger.warning(
                    f"[SubjectArchive] {name}: reflection {r.get('id')} 归档失败: {e}"
                )
        for section_key, entry_id in persona_targets:
            try:
                if await persona_manager.aarchive_persona_entry(
                    name, section_key, entry_id,
                ):
                    archived_counts['persona_entries'] += 1
            except Exception as e:
                logger.warning(
                    f"[SubjectArchive] {name}: persona {entry_id} 归档失败: {e}"
                )
        logger.info(
            f"[SubjectArchive] {name}: 归档 subject [{_domain_marker(subject)}] "
            f"facts={archived_counts['facts']} "
            f"reflections={archived_counts['reflections']} "
            f"persona={archived_counts['persona_entries']} "
            f"(last_write={archived_counts['last_write']}, "
            f"stale_days={stale_days})"
        )
        report['archived'][f"{subject.key}|{subject.scope}"] = archived_counts
    return report


# ── restore（归档不是删除：可回滚的另一半） ─────────────────────────────


async def _ascan_shards_for_subject(
    archive_dir: str, subject: MemorySubject, *, require_archived_status: bool,
) -> dict[str, dict]:
    """Collect shard entries stamped with this subject, newest shard last.

    ``require_archived_status=True`` (reflections) keeps only entries whose
    shard copy carries ``status='archived'`` — the stamp written by
    ``aarchive_reflection``. Age-based terminal archival (promoted / denied
    older than 30 days) keeps the original status in the shard copy, so
    those must NOT be resurrected by a subject restore.
    """
    from memory.archive_shards import (
        ShardCorruptError,
        _aread_shard,
        _list_shard_files,
    )
    candidates: dict[str, dict] = {}
    shard_files = await asyncio.to_thread(_list_shard_files, archive_dir)
    for filename, _date, _uuid8 in shard_files:
        path = os.path.join(archive_dir, filename)
        try:
            entries = await _aread_shard(path)
        except ShardCorruptError as e:
            logger.warning(
                f"[SubjectArchive] 分片损坏，restore 跳过 {filename}: {e}"
            )
            continue
        for entry in entries:
            if not entry.get('id'):
                continue
            if require_archived_status and entry.get('status') != 'archived':
                continue
            if not entry.get('archived_at'):
                continue
            if not entry_matches_subject(entry, subject):
                continue
            candidates[entry['id']] = entry
    return candidates


async def _arestore_subject_reflections(
    name: str, subject: MemorySubject, reflection_engine, now_iso: str,
) -> int:
    from memory.event_log import EVT_REFLECTION_STATE_CHANGED
    from utils.cloudsave_runtime import assert_cloudsave_writable
    from utils.file_utils import atomic_write_json

    if reflection_engine._event_log is None:
        raise RuntimeError(
            "[SubjectArchive] event_log 未注入；restore 需要 ReflectionEngine "
            "构造时传入 event_log"
        )
    archive_dir = reflection_engine._reflections_archive_dir(name)
    candidates = await _ascan_shards_for_subject(
        archive_dir, subject, require_archived_status=True,
    )
    if not candidates:
        return 0
    restored = 0
    async with reflection_engine._get_alock(name):
        full = await reflection_engine._aload_reflections_full(name)
        present = {r.get('id') for r in full if isinstance(r, dict)}
        for rid, snapshot in candidates.items():
            if rid in present:
                continue
            entry = dict(snapshot)
            entry.pop('archived_at', None)
            entry.pop('archive_shard_path', None)
            # scoped 活跃态只有 confirmed（pending 对 scoped 是死路），恢复
            # 统一落 confirmed；confirmed_at 缺失补 restore 时刻。
            entry['status'] = 'confirmed'
            entry.setdefault('confirmed_at', now_iso)
            entry['restored_at'] = now_iso
            payload = {
                'reflection_id': rid,
                'from': 'archived',
                'to': 'confirmed',
                'restored_at': now_iso,
                # 快照进事件：与归档事件对称，全量 replay 后可人工/重跑恢复。
                'entry_snapshot': dict(entry),
            }

            def _sync_load(_n: str, _full=full):
                return _full

            def _sync_mutate(view, _entry=entry):
                view.append(_entry)

            def _sync_save(n: str, view):
                assert_cloudsave_writable(
                    reflection_engine._config_manager,
                    operation="save",
                    target=f"memory/{n}/reflections.json",
                )
                atomic_write_json(
                    reflection_engine._reflections_path(n), view,
                    indent=2, ensure_ascii=False,
                )

            await reflection_engine._event_log.arecord_and_save(
                name, EVT_REFLECTION_STATE_CHANGED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=_sync_save,
            )
            present.add(rid)
            restored += 1
    return restored


async def _arestore_subject_persona_entries(
    name: str, subject: MemorySubject, persona_manager, now_iso: str,
) -> int:
    from memory.event_log import EVT_PERSONA_FACT_ADDED

    if persona_manager._event_log is None:
        raise RuntimeError(
            "[SubjectArchive] event_log 未注入；restore 需要 PersonaManager "
            "构造时传入 event_log"
        )
    archive_dir = persona_manager._persona_archive_dir(name)
    candidates = await _ascan_shards_for_subject(
        archive_dir, subject, require_archived_status=False,
    )
    if not candidates:
        return 0
    restored = 0
    async with persona_manager._get_alock(name):
        persona = await persona_manager._aensure_persona_locked(name)
        section_facts = persona_manager._get_section_facts(
            persona, subject.kind, subject=subject,
        )
        present = {
            entry.get('id')
            for _, entry in _iter_persona_entries(persona)
        }
        for eid, snapshot in candidates.items():
            if eid in present:
                continue
            entry = dict(snapshot)
            entry.pop('archived_at', None)
            entry.pop('archive_shard_path', None)
            entry['restored_at'] = now_iso
            # payload 不带 archive_shard_path：persona 归档 handler 只处理
            # 带该字段的事件，restore 事件在 replay 时是 no-op（见模块
            # docstring 的全量 replay 限制说明）。
            payload = {
                'entity_key': subject.persona_section_key,
                'entry_id': eid,
                'restored_at': now_iso,
                'entry_snapshot': dict(entry),
            }

            def _sync_load(_n: str, _persona=persona):
                return _persona

            def _sync_mutate(_view, _entry=entry):
                section_facts.append(_entry)

            await persona_manager._event_log.arecord_and_save(
                name, EVT_PERSONA_FACT_ADDED, payload,
                sync_load_view=_sync_load,
                sync_mutate_view=_sync_mutate,
                sync_save_view=persona_manager._sync_save_persona_view,
            )
            present.add(eid)
            restored += 1
    return restored


async def arestore_scoped_subject(
    name: str,
    subject: MemorySubject | dict,
    *,
    fact_store,
    persona_manager,
    reflection_engine,
    now: datetime | None = None,
) -> dict:
    """Bring one archived subject fully back into the active stores.

    Facts get their ``subject_archived_at`` stamp stripped and move back to
    facts.json; reflection / persona entries are re-appended from their
    archive shards (shard copies are left in place — restore never destroys
    the archived copy). Idempotent: entries already active are skipped.
    """
    from memory.scopes import coerce_subject
    memory_subject = coerce_subject(subject)
    if memory_subject is None:
        raise ValueError("arestore_scoped_subject requires an explicit subject")
    if now is None:
        now = datetime.now()
    now_iso = now.isoformat()

    facts_restored = await fact_store.arestore_subject_facts(name, memory_subject)
    reflections_restored = await _arestore_subject_reflections(
        name, memory_subject, reflection_engine, now_iso,
    )
    persona_restored = await _arestore_subject_persona_entries(
        name, memory_subject, persona_manager, now_iso,
    )
    logger.info(
        f"[SubjectArchive] {name}: 恢复 subject "
        f"[{_domain_marker(memory_subject)}] facts={facts_restored} "
        f"reflections={reflections_restored} persona={persona_restored}"
    )
    return {
        'facts': facts_restored,
        'reflections': reflections_restored,
        'persona_entries': persona_restored,
    }
