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


# Last-write time recognizes three fields: persisted facts/reflections have created_at;
# scoped reflections also have confirmed_at; restore stamps restored_at on recovered
# entries in all three stores. Explicit restoration must reset the archival clock or the next sweep
# immediately undoes it. Ordinary persona entries usually lack all three because they derive from promotion;
# missing timestamps there is acceptable, since facts are the authoritative write surface. New derived entries from synthesis/
# promotion/refine advance last_write conservatively, delaying archival
# but never accelerating it.
_TIMESTAMP_FIELDS = ('created_at', 'confirmed_at', 'restored_at')


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is not None:
        # External imports/migrations may write timezone-aware ISO strings; sweep's now is naive
        # local time, and mixing them raises TypeError, failing the whole stage each round.
        # Convert all timestamps to local naive time before comparison.
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


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
    # A subject with any timestamped row is not timestamp-free.
    return last, no_ts - set(last)


def _coalesce_by_participant(
    last_writes: dict[tuple, tuple[MemorySubject, datetime]],
) -> dict[tuple, tuple[MemorySubject, datetime]]:
    """Give every account of one participant that participant's newest write.

    Best-effort by design: an unloaded pool or a resolution failure degrades to
    the identity, i.e. exactly today's per-subject behaviour. Archival must
    never break because identity resolution had a bad day.
    """
    if not last_writes:
        return last_writes
    try:
        from memory import trust_store
        from memory.subject_identity import (
            coalesce_participant_last_writes,
            expand_subject,
        )

        snap = trust_store.trust_snapshot()
        if not snap.loaded:
            return last_writes
        return coalesce_participant_last_writes(
            last_writes, lambda subject: expand_subject(subject, snap),
        )
    except Exception as exc:  # noqa: BLE001 - archival must stay best-effort
        logger.warning(f"[SubjectArchive] 参与者级归档合并跳过: {exc}")
        return last_writes


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
        # Fail closed: never archive subjects with no parseable timestamps, but log evidence;
        # an incorrect criterion silently loses memory, while missed eligible archival also needs observability.
        logger.debug(
            f"[SubjectArchive] {name}: {len(no_ts)} 个 subject 无可解析时间戳，"
            f"跳过归档判定"
        )

    # Merge per participant: after canonical write routing, noncanonical accounts stop receiving writes and appear stale
    # after 90 days although the person remains active. Take the participant's latest timestamp and write it to
    # every marker so last-write time applies per person rather than account. Inject the resolver
    # here, keeping subject_archive independent of trust_store imports.
    last_writes = _coalesce_by_participant(last_writes)

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

    active_refls = await reflection_engine.aload_reflections(name)
    now_iso = now.isoformat()
    # Carry the staleness decision snapshot into execution for locked fact-store revalidation:
    # new writes arriving after assessment (created_at >= cutoff) abort archival for the entire subject,
    # preserving the first new memory after reactivation in recall.
    stale_cutoff = now - timedelta(days=stale_days)

    for subject, last_dt in stale:
        # Fact-side pending work counts all unmarked rows: move active rows and stamp subject_archived_at in place
        # on absorbed rows already residing in archive files; otherwise stale subjects containing only
        # absorbed history remain in recall forever.
        fact_targets = [
            f for f in facts_full
            if isinstance(f, dict)
            and entry_matches_subject(f, subject)
            and not f.get('subject_archived_at')
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
            # Already fully archived last round, or no active entries: skip silently.
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
                moved = await fact_store.aarchive_subject_facts(
                    name, subject, now_iso, stale_cutoff,
                )
            except Exception as e:
                # Treat real disk/permission failures like reactivation aborts: skip remaining stores to prevent
                # divergence where facts remain active but reflection/persona have moved into shards.
                logger.warning(
                    f"[SubjectArchive] {name}: subject "
                    f"[{_domain_marker(subject)}] facts 归档失败，跳过本轮"
                    f"其余存储的归档: {e}"
                )
                moved = None
            if moved is None:
                # Locked fact-store checks detected new writes since assessment, reactivating the subject.
                # All three stores must follow the same decision or none; skip this subject's
                # reflection/persona archival and let the next sweep reassess using the new
                # last_write value.
                logger.info(
                    f"[SubjectArchive] {name}: subject "
                    f"[{_domain_marker(subject)}] 判定窗口内复活，跳过本轮"
                    f"其余存储的归档"
                )
                continue
            archived_counts['facts'] = moved
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


# -- Restore: archival is reversible, not deletion --


async def _ascan_shards_for_subject(
    archive_dir: str, subject: MemorySubject, *, require_archived_status: bool,
    archived_after_iso: str | None = None,
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
            if (
                archived_after_iso is not None
                and str(entry.get('archived_at')) <= archived_after_iso
            ):
                continue
            if not entry_matches_subject(entry, subject):
                continue
            # Multiple shard copies of one ID after archive/restore cycles: select
            # the latest archived_at snapshot. Same-day shard suffixes are random uuid8,
            # so filename/traversal order is not chronological.
            prev = candidates.get(entry['id'])
            if (
                prev is None
                or str(entry.get('archived_at') or '')
                > str(prev.get('archived_at') or '')
            ):
                candidates[entry['id']] = entry
    return candidates


async def _arestore_subject_reflections(
    name: str, subject: MemorySubject, reflection_engine, now_iso: str,
    archived_after_iso: str | None = None,
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
        archived_after_iso=archived_after_iso,
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
            # confirmed is the only active scoped state; pending cannot progress. Restore
            # as confirmed, supplying restoration time if confirmed_at is missing.
            entry['status'] = 'confirmed'
            entry.setdefault('confirmed_at', now_iso)
            entry['restored_at'] = now_iso
            payload = {
                'reflection_id': rid,
                'from': 'archived',
                'to': 'confirmed',
                'restored_at': now_iso,
                # Include snapshots in events, symmetric with archival events, for manual/retried recovery after full replay.
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
    archived_after_iso: str | None = None,
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
        archived_after_iso=archived_after_iso,
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
            # Omit archive_shard_path from payload: the persona archive handler processes only
            # events with that field, so restore events replay as no-ops (see the module
            # docstring's full-replay limitations).
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

    # Hold the same per-subject transaction lock as scoped_forget.  Reading the
    # cutoff once is then safe for the full three-store restore: a forget can
    # only run wholly before (and update the cutoff) or wholly after (and erase
    # the restored rows), never between shard scan and append.
    transaction_lock = fact_store._get_subject_forget_transaction_lock(
        name, memory_subject,
    )
    async with transaction_lock:
        forgotten_at = await fact_store.asubject_forget_cutoff(
            name, memory_subject,
        )

        facts_restored = await fact_store.arestore_subject_facts(
            name, memory_subject, now_iso, forgotten_at,
        )
        if facts_restored is None:
            # Abort symmetrically with archival: corrupt facts_archive.json must stop restoration
            # of higher-level stores; otherwise facts stay archived while reflection/persona become active,
            # a divergence that repeated restores cannot repair until the archive file is fixed.
            logger.warning(
                f"[SubjectArchive] {name}: subject "
                f"[{_domain_marker(memory_subject)}] fact 恢复中止（归档文件"
                f"损坏），跳过其余存储的恢复"
            )
            return {
                'facts': 0,
                'reflections': 0,
                'persona_entries': 0,
                'aborted': True,
            }
        reflections_restored = await _arestore_subject_reflections(
            name, memory_subject, reflection_engine, now_iso, forgotten_at,
        )
        persona_restored = await _arestore_subject_persona_entries(
            name, memory_subject, persona_manager, now_iso, forgotten_at,
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
