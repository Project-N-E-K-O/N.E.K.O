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
"""Subject erasure, tombstones, arbitration/subject archiving, and cold restore methods."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime
from memory.temporal import to_naive_local
from memory import cold_store
from memory.scopes import MemorySubject, entry_matches_subject, subject_from_entry

from memory import facts as _facts_facade
from ._shared import (
    _ARCHIVE_AGE_DAYS,
    _fact_scoped_identity,
    _is_cold_row,
    _merge_archive_entries,
    _readable_fact_id,
    _speaker_trust_fact_id,
)

class ForgetArchiveMixin:
    async def aforget_subject(self, lanlan_name: str, subject) -> dict:
        """Run scoped erasure as one transaction against archive sweeps."""
        # Lock order is persist -> fact everywhere. Extraction already holds
        # persist when save_facts takes the fact lock; reversing that order
        # here would deadlock against an in-flight persistence task.
        async with self._get_persist_alock(lanlan_name):
            fact_lock = self._get_lock(lanlan_name)
            acquire_task = _facts_facade.asyncio.create_task(
                _facts_facade.asyncio.to_thread(fact_lock.acquire)
            )
            try:
                await _facts_facade.asyncio.shield(acquire_task)
            except _facts_facade.asyncio.CancelledError:
                # to_thread keeps running after its awaiter is cancelled. Let
                # it acquire, then release, so cancellation cannot strand the
                # per-character lock forever.
                acquired = await acquire_task
                if acquired:
                    fact_lock.release()
                raise
            try:
                return await self._aforget_subject_with_fact_lock(
                    lanlan_name, subject, _persist_lock_held=True,
                )
            finally:
                fact_lock.release()

    async def _aforget_subject_with_fact_lock(
        self, lanlan_name: str, subject, *, _persist_lock_held: bool = False,
    ) -> dict:
        """Delete every fact belonging to one exact (subject, scope) domain.

        Retraction entry after friend removal/group departure: clear active facts, facts_archive,
        and FTS index together. entry_matches_subject requires exact (key, scope)
        equality: unstamped legacy entries and other scopes never fall in deletion scope
        (fail-closed direction matches read filtering). Idempotent: absent targets are no-ops.

        Strict FTS deletion precedes JSON changes: unconfirmed index cleanup fails the entire
        forget operation, retaining main-store IDs for retry; successful per-ID deletions are idempotent.

        Cold storage is erased too, and an unreadable cold shard fails the whole
        forget before anything changes. Erased cold rows count as ``facts_archive``,
        where they lived before cold storage existed.
        """  # noqa: DOCSTRING_CJK
        from memory.scopes import coerce_subject, entry_matches_subject
        memory_subject = coerce_subject(subject)
        if memory_subject is None:
            raise ValueError("aforget_subject requires an explicit subject")
        removed_active = 0
        removed_archive = 0
        removed_from_archive: list[dict] = []
        persist_context = (
            nullcontext()
            if _persist_lock_held else self._get_persist_alock(lanlan_name)
        )
        async with persist_context:
            # Fence every scoped extraction that captured the previous
            # generation before this critical section.
            self._bump_subject_forget_generation(lanlan_name, memory_subject)
            # Never trust the cache here. The normal loader is deliberately
            # best-effort and may already have cached [] after a malformed or
            # transiently unreadable facts.json. Re-read the authoritative
            # file on every forget attempt so a repaired file can be erased
            # without restarting the process.
            facts_path = self._facts_path(lanlan_name)
            facts: list = []
            if await _facts_facade.asyncio.to_thread(os.path.exists, facts_path):
                def _read_active_facts() -> object:
                    with open(facts_path, encoding='utf-8') as f:
                        return json.load(f)

                try:
                    facts_data = await _facts_facade.asyncio.to_thread(_read_active_facts)
                except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                    raise RuntimeError(
                        f"facts state unreadable during forget: {exc}"
                    ) from exc
                if not isinstance(facts_data, list):
                    raise RuntimeError(
                        "facts state is not a list during forget"
                    )
                facts = facts_data
            else:
                # A freshly-created in-process store may have durable work
                # pending in its cache before the first file appears.
                facts = self._facts.get(lanlan_name, [])
            self._facts[lanlan_name] = facts
            removed = [
                f for f in facts
                if isinstance(f, dict) and entry_matches_subject(f, memory_subject)
            ]

            # Strictly inspect and prepare the archive before changing active
            # facts. Otherwise a corrupt/transiently unreadable archive raises
            # only after facts.json has already been durably deleted, yielding
            # a permanent partial forget.
            archive_path = self._facts_archive_path(lanlan_name)
            archived: list = []
            archive_exists = await _facts_facade.asyncio.to_thread(
                os.path.exists, archive_path,
            )
            kept_archive: list = []
            if archive_exists:
                def _read_archive() -> object:
                    with open(archive_path, encoding='utf-8') as f:
                        return json.load(f)

                try:
                    archived = await _facts_facade.asyncio.to_thread(_read_archive)
                except (json.JSONDecodeError, OSError) as exc:
                    # Explicitly fail corrupt archives: silently skipping lets "fully deleted" responses
                    # conceal copies still retrievable by BM25.
                    raise RuntimeError(
                        f"facts_archive unreadable during forget: {exc}"
                    ) from exc
                if not isinstance(archived, list):
                    raise RuntimeError(
                        "facts_archive is not a list during forget"
                    )
                removed_from_archive = [
                    f for f in archived
                    if (
                        isinstance(f, dict)
                        and entry_matches_subject(f, memory_subject)
                    )
                ]
                kept_archive = [
                    f for f in archived
                    if not (
                        isinstance(f, dict)
                        and entry_matches_subject(f, memory_subject)
                    )
                ]
                removed_archive = len(removed_from_archive)

            character_dir = self._character_dir(lanlan_name)
            try:
                removed_from_cold = [
                    f for f in await cold_store.aread_full(character_dir)
                    if entry_matches_subject(f, memory_subject)
                ]
            except cold_store.ColdStoreUnreadable as exc:
                raise RuntimeError(
                    f"cold facts unreadable during forget: {exc}"
                ) from exc

            # Persist the privacy boundary before deleting any recoverable
            # copy. Reflection/persona archive events can recreate their shard
            # snapshots during a later full replay; restore must still know
            # that every snapshot at or before this point is erased history.
            await _facts_facade.asyncio.to_thread(
                self._record_subject_forget_tombstone_locked,
                lanlan_name,
                memory_subject,
                datetime.now().isoformat(),
            )

            # Privacy erasure must fail closed.  The normal FTS helper is
            # deliberately best-effort, so request strict propagation here
            # while the authoritative JSON rows still preserve every id for
            # a retry.  A partial multi-id FTS deletion is harmless because
            # DELETE is idempotent and no JSON state has changed yet.
            if self._time_indexed is not None:
                deleted_fact_ids: set = set()
                for fact in removed + removed_from_archive + removed_from_cold:
                    fact_id = _readable_fact_id(fact)
                    if fact_id is None or fact_id in deleted_fact_ids:
                        continue
                    deleted_fact_ids.add(fact_id)
                    await self._time_indexed.adelete_fact_from_index(
                        lanlan_name, fact_id, strict=True,
                    )

            # Cold storage and archive first: if the later active write fails,
            # facts.json still contains enough subject-stamped rows for a retry.
            # The reverse order is the irrecoverable half-commit reported by Greptile.
            if removed_from_cold:
                _facts_facade.assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{lanlan_name}/{cold_store.FACT_COLD_DIRNAME}",
                )
                try:
                    await cold_store.aforget(
                        character_dir,
                        lambda row: entry_matches_subject(row, memory_subject),
                    )
                except cold_store.ColdStoreUnreadable as exc:
                    raise RuntimeError(
                        f"cold facts unreadable during forget: {exc}"
                    ) from exc
                removed_archive += len(removed_from_cold)
            if removed_from_archive:
                _facts_facade.assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{lanlan_name}/facts_archive.json",
                )
                await _facts_facade.asyncio.to_thread(
                    _facts_facade.atomic_write_json, archive_path, kept_archive,
                    indent=2, ensure_ascii=False,
                )

            if removed:
                removed_active = len(removed)
                removed_identities = {id(f) for f in removed}
                previous_facts = list(facts)
                facts[:] = [
                    f for f in facts if id(f) not in removed_identities
                ]
                try:
                    await self.asave_facts(
                        lanlan_name, _fact_lock_held=True,
                    )
                except Exception:
                    # Keep the in-memory cache aligned with the unchanged
                    # active file so the retry can still find the subject.
                    facts[:] = previous_facts
                    raise
        if removed_active or removed_archive:
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: forget "
                f"{memory_subject.key}/{memory_subject.scope}: "
                f"active={removed_active} archive={removed_archive}"
            )
        return {"facts": removed_active, "facts_archive": removed_archive}

    def _facts_archive_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'facts_archive.json')

    def _load_archived_speaker_trust_signal_facts(self, name: str) -> list[dict]:
        """Strictly load archived and cold rows carrying issued trust signals (cold ones as stubs)."""
        try:
            cold = [
                dict(row) for row in self._cold_fact_stubs(name)
                if isinstance(row.get('_speaker_trust_signal_events'), list)
            ]
        except cold_store.ColdStoreUnreadable as exc:
            raise RuntimeError(f"cold facts unreadable during trust replay: {exc}") from exc
        cold_identities = {_fact_scoped_identity(row) for row in cold}
        archive_path = self._facts_archive_path(name)
        if not os.path.exists(archive_path):
            return cold
        try:
            with open(archive_path, encoding='utf-8') as fh:
                archived = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise RuntimeError(
                f"facts_archive unreadable during trust replay: {exc}"
            ) from exc
        if not isinstance(archived, list):
            raise RuntimeError("facts_archive is not a list during trust replay")
        return [
            dict(row)
            for row in archived
            if (
                isinstance(row, dict)
                and isinstance(row.get('_speaker_trust_signal_events'), list)
                and _fact_scoped_identity(row) not in cold_identities
            )
        ] + cold

    async def aload_archived_speaker_trust_signal_facts(
        self, name: str,
    ) -> list[dict]:
        return await _facts_facade.asyncio.to_thread(
            self._load_archived_speaker_trust_signal_facts, name,
        )

    def _subject_forget_tombstones_path(self, name: str) -> str:
        return os.path.join(
            os.path.dirname(self._facts_path(name)),
            'subject_forget_tombstones.json',
        )

    def _load_subject_forget_tombstones_strict(self, name: str) -> list[dict]:
        """Load persistent scoped-forget cutoffs without best-effort fallback."""
        path = self._subject_forget_tombstones_path(name)
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding='utf-8') as fh:
                rows = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise RuntimeError(
                f"subject forget tombstones unreadable: {exc}"
            ) from exc
        if not isinstance(rows, list):
            raise RuntimeError("subject forget tombstones are not a list")
        return [row for row in rows if isinstance(row, dict)]

    def _record_subject_forget_tombstone_locked(
        self, name: str, subject: MemorySubject, forgotten_at: str,
    ) -> None:
        """Persist the latest erasure cutoff while the fact lock is held."""
        rows = self._load_subject_forget_tombstones_strict(name)
        kept = [row for row in rows if not entry_matches_subject(row, subject)]
        kept.append({**subject.as_entry_fields(), 'forgotten_at': forgotten_at})
        _facts_facade.assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/subject_forget_tombstones.json",
        )
        _facts_facade.atomic_write_json(
            self._subject_forget_tombstones_path(name), kept,
            indent=2, ensure_ascii=False,
        )

    def subject_forget_cutoff(
        self, name: str, subject: MemorySubject,
    ) -> str | None:
        """Return the latest persistent erasure cutoff for one exact scope."""
        cutoffs = [
            str(row.get('forgotten_at') or '')
            for row in self._load_subject_forget_tombstones_strict(name)
            if entry_matches_subject(row, subject) and row.get('forgotten_at')
        ]
        return max(cutoffs, default=None)

    async def asubject_forget_cutoff(
        self, name: str, subject: MemorySubject,
    ) -> str | None:
        return await _facts_facade.asyncio.to_thread(self.subject_forget_cutoff, name, subject)

    async def afinalize_subject_forget(
        self, name: str, subject: MemorySubject,
    ) -> None:
        """Advance the durable cutoff after the other stores have drained."""
        async with self._get_persist_alock(name):
            def _record_under_fact_lock() -> None:
                with self._get_lock(name):
                    self._record_subject_forget_tombstone_locked(
                        name, subject, datetime.now().isoformat(),
                    )

            await _facts_facade.asyncio.to_thread(_record_under_fact_lock)

    async def aarchive_arbitrated_facts(
        self,
        name: str,
        archive_specs: dict[tuple[object, str, str, str], dict],
        *,
        survivor_updates: dict[tuple[object, str, str, str], dict] | None = None,
        expected_survivors: dict[tuple[object, str, str, str], dict] | None = None,
        expected_losers: dict[tuple[object, str, str, str], dict] | None = None,
        archive_check: Callable[[list, list], bool] | None = None,
        archived_at: str | None = None,
    ) -> int:
        """Move trust/dedup losers into cold storage with a cold-first two-file commit.

        The losers carry ``arbitration_archived_at`` (and ``superseded_by`` when
        a survivor replaces them) into cold storage, where nothing recalls,
        searches or organizes them; only cold_store's restore brings one back.

        ``archive_check(archive_rows, live_rows)`` runs under the same lock, after
        facts_archive.json is read; a False refuses the whole commit (a caller whose
        decision rests on archived rows checks them where nothing can change them).
        ``archived_at`` stamps the archive time instead of now, so a caller can later
        tell its own archiving apart (the time survives a snapshot round trip).
        """
        if not archive_specs:
            return 0
        survivor_updates = survivor_updates or {}
        expected_survivors = expected_survivors or {}
        if (
            expected_losers is not None
            and set(expected_losers) != set(archive_specs)
        ):
            raise ValueError("loser snapshots must match archive specs")
        if set(survivor_updates) != set(expected_survivors):
            raise ValueError("survivor updates require matching expected snapshots")
        async with self._get_persist_alock(name):
            def _archive() -> int:
                with self._get_lock(name):
                    _facts_facade.assert_cloudsave_writable(
                        self._config_manager,
                        operation="archive",
                        target=f"memory/{name}/{cold_store.FACT_COLD_DIRNAME}",
                    )
                    facts = self._facts.get(name, [])
                    fact_identities = {
                        identity for fact in facts
                        if isinstance(fact, dict)
                        and (identity := _fact_scoped_identity(fact)) is not None
                    }

                    def _normalize_keys(values: dict) -> dict:
                        normalized = {}
                        for key, value in values.items():
                            if isinstance(key, tuple) and len(key) == 4:
                                identity = (
                                    key[0],
                                    str(key[1]),
                                    str(key[2]),
                                    str(key[3]),
                                )
                                if identity not in fact_identities:
                                    identity = (
                                        _speaker_trust_fact_id(key[0]),
                                        *identity[1:],
                                    )
                            else:
                                matches = [
                                    candidate
                                    for fact in facts
                                    if isinstance(fact, dict)
                                    and _readable_fact_id(fact) == key
                                    and (
                                        candidate := _fact_scoped_identity(fact)
                                    ) is not None
                                ]
                                if len(matches) != 1:
                                    raise RuntimeError(
                                        "bare fact id is missing or ambiguous "
                                        "during arbitration"
                                    )
                                identity = matches[0]
                            normalized[identity] = value
                        return normalized

                    archive_specs_by_identity = _normalize_keys(archive_specs)
                    survivor_updates_by_identity = _normalize_keys(
                        survivor_updates,
                    )
                    expected_survivors_by_identity = _normalize_keys(
                        expected_survivors,
                    )
                    expected_losers_by_identity = (
                        _normalize_keys(expected_losers)
                        if expected_losers is not None else None
                    )
                    losers = [
                        fact for fact in facts
                        if _fact_scoped_identity(fact) in archive_specs_by_identity
                    ]
                    if len(losers) != len(archive_specs_by_identity):
                        # The resolver mutates its selected survivor before
                        # entering this persistence lock.  A concurrent forget
                        # may remove either side in that window; treating a
                        # partial archive as success would leave the mutated
                        # cache ahead of disk.  Raise inside this helper so its
                        # exception path below evicts the cache atomically.
                        raise RuntimeError(
                            "fact arbitration archive mismatch: expected "
                            f"{len(archive_specs_by_identity)}, archived {len(losers)}"
                        )
                    live_by_identity = {
                        identity: fact
                        for fact in facts
                        if (identity := _fact_scoped_identity(fact)) is not None
                    }
                    stale_survivors = [
                        identity
                        for identity, expected in (
                            expected_survivors_by_identity.items()
                        )
                        if live_by_identity.get(identity) != expected
                    ]
                    if stale_survivors:
                        raise RuntimeError(
                            "fact arbitration survivor mismatch: "
                            + ",".join(map(str, sorted(stale_survivors)))
                        )
                    stale_losers = [
                        identity for identity, expected in (
                            expected_losers_by_identity or {}
                        ).items()
                        if live_by_identity.get(identity) != expected
                    ]
                    if stale_losers:
                        raise RuntimeError(
                            "fact arbitration loser mismatch: "
                            + ",".join(map(str, sorted(stale_losers)))
                        )
                    archive_path = self._facts_archive_path(name)
                    archived: list[dict] = []
                    if os.path.exists(archive_path):
                        try:
                            with open(archive_path, encoding='utf-8') as fh:
                                data = json.load(fh)
                        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                            raise RuntimeError(
                                f"facts_archive unreadable during arbitration: {exc}"
                            ) from exc
                        if not isinstance(data, list):
                            raise RuntimeError(
                                "facts_archive is not a list during arbitration"
                            )
                        archived = data
                    if archive_check is not None and not archive_check(archived, facts):
                        raise RuntimeError("fact arbitration archive check failed")
                    now_iso = archived_at or datetime.now().isoformat()
                    stamped = []
                    for fact in losers:
                        copy = dict(fact)
                        spec = archive_specs_by_identity.get(
                            _fact_scoped_identity(fact),
                        ) or {}
                        copy['arbitration_archived_at'] = now_iso
                        copy['arbitration_reason'] = str(
                            spec.get('reason') or 'dedup_arbitration'
                        )
                        if spec.get('superseded_by'):
                            copy['superseded_by'] = spec['superseded_by']
                        stamped.append(copy)
                    loser_identities = {
                        _fact_scoped_identity(fact) for fact in losers
                    }
                    active = []
                    for fact in facts:
                        identity = _fact_scoped_identity(fact)
                        if identity in loser_identities:
                            continue
                        replacement = survivor_updates_by_identity.get(identity)
                        active.append(
                            dict(replacement) if replacement is not None else fact
                        )
                    # Never reverse: crash between writes leaves a duplicate,
                    # which readers settle as cold, not a missing fact.
                    cold_written = False
                    try:
                        cold_store.append(
                            self._character_dir(name), stamped,
                            identity=_fact_scoped_identity,
                        )
                        cold_written = True
                        _facts_facade.atomic_write_json(
                            self._facts_path(name), active, indent=2,
                            ensure_ascii=False,
                        )
                    except Exception:
                        if cold_written:
                            # The survivors were not updated: take this commit's
                            # cold copies back so the whole arbitration retries,
                            # instead of a cold loser next to a stale survivor.
                            try:
                                cold_store.rewrite_rows(
                                    self._character_dir(name),
                                    lambda row: None if (
                                        row.get('arbitration_archived_at') == now_iso
                                        and _fact_scoped_identity(row) in loser_identities
                                    ) else row,
                                )
                            except Exception:  # noqa: BLE001 - the duplicate then reads as cold
                                _facts_facade.logger.warning(
                                    f"[FactStore] {name}: 仲裁提交失败后撤回冷存副本也失败",
                                    exc_info=True,
                                )
                        # The active cache may no longer match either durable
                        # file after a partial two-file commit.  Force reload;
                        # archive-first ordering still guarantees recoverability.
                        self._facts.pop(name, None)
                        raise
                    facts[:] = active
                    for fact in losers:
                        spec = archive_specs_by_identity.get(
                            _fact_scoped_identity(fact),
                        ) or {}
                        _facts_facade.logger.info(
                            f"[FactStore] {name}: 仲裁归档 fact={fact.get('id')} "
                            f"speaker={fact.get('speaker_id')} "
                            f"superseded_by={spec.get('superseded_by')}"
                        )
                    return len(losers)

            try:
                return await _facts_facade.asyncio.to_thread(_archive)
            except BaseException:
                # Any failure may follow a partial archive-first two-file
                # commit, so discard the cache and reload durable truth.
                def _invalidate() -> None:
                    with self._get_lock(name):
                        self._facts.pop(name, None)

                await _facts_facade.asyncio.to_thread(_invalidate)
                raise

    async def arestore_cold_facts(
        self, name: str, select: Callable[[dict], bool],
    ) -> list[dict]:
        """Bring cold facts chosen by ``select(row)`` back into the active pool.

        Goes through ``cold_store.restore``: a private-chat fact comes back as
        a copy under a new id (its cold row stays, so peers that hold the old
        id as cold never fold the copy away again); a group-subject fact comes
        back under its own id and leaves cold storage. The active file is
        written before cold storage changes, so a crash leaves the row cold,
        never lost. Returns the rows added.
        """
        async with self._get_persist_alock(name):
            def _restore() -> list[dict]:
                # Warm the non-reentrant cache lock before taking it below.
                self.load_facts(name)
                with self._get_lock(name):
                    _facts_facade.assert_cloudsave_writable(
                        self._config_manager,
                        operation='restore',
                        target=f'memory/{name}/facts.json',
                    )
                    # Rows demoted before cold storage existed are restorable too.
                    if self._migrate_cold_rows_locked(name) is None:
                        raise RuntimeError('cold facts could not be prepared for restore')
                    facts = self._facts.get(name, [])
                    # A copy restored earlier may itself have aged into the
                    # archive or gone cold since; it still counts as restored.
                    archive_path = self._facts_archive_path(name)
                    archived: list = []
                    if os.path.exists(archive_path):
                        with open(archive_path, encoding='utf-8') as fh:
                            archived = json.load(fh)
                        if not isinstance(archived, list):
                            raise RuntimeError('facts_archive is not a list during restore')
                    already = {
                        row.get('restored_from')
                        for row in [*facts, *archived, *self._cold_fact_stubs(name)]
                        if isinstance(row, dict) and row.get('restored_from')
                    }

                    def _activate(rows: list[dict]) -> None:
                        self._activate_restored_rows(name, facts, rows)

                    restored = cold_store.restore(
                        self._character_dir(name),
                        lambda row: select(row) and (
                            subject_from_entry(row) is not None
                            or row.get('id') not in already
                        ),
                        activate=_activate,
                    )
                    if restored:
                        _facts_facade.logger.info(f'[FactStore] {name}: 从冷存恢复 {len(restored)} 条事实')
                    return restored

            try:
                restored = await _facts_facade.asyncio.to_thread(_restore)
            except BaseException:
                def _invalidate() -> None:
                    with self._get_lock(name):
                        self._facts.pop(name, None)

                await _facts_facade.asyncio.to_thread(_invalidate)
                raise
            if restored and self._time_indexed is not None:
                # Indexed under the same lock as the write: a subject forget queued
                # behind this restore must find the rows indexed, or its strict index
                # delete would run first and this write would put the words back.
                index_task = _facts_facade.asyncio.create_task(self._aindex_restored_rows(name, restored))
                try:
                    await _facts_facade.asyncio.shield(index_task)
                except _facts_facade.asyncio.CancelledError:
                    # Every further cancellation is absorbed too, as the trust
                    # persist path above does: the lock is released only once the
                    # index write has finished.
                    current_task = _facts_facade.asyncio.current_task()
                    while not index_task.done():
                        try:
                            await _facts_facade.asyncio.shield(index_task)
                        except _facts_facade.asyncio.CancelledError:
                            if current_task is not None:
                                while current_task.cancelling():
                                    current_task.uncancel()
                    raise
        return restored

    async def _aindex_restored_rows(self, name: str, rows: list[dict]) -> None:
        for row in rows:
            try:
                await self._time_indexed.aindex_fact(name, row['id'], row.get('text', ''))
            except Exception as error:  # noqa: BLE001 - the index backfill repairs a missing row
                _facts_facade.logger.warning(
                    f"[FactStore] {name}: 恢复的事实写入检索索引失败: {type(error).__name__}"
                )

    def _migrate_cold_rows_locked(self, name: str) -> int | None:
        """Move demoted rows still in facts_archive.json into cold storage. Caller holds the fact lock.

        Rows demoted before cold storage existed (arbitration losers, archived
        group subjects) and rows a cloud save brought back after they went cold
        both end up here. Cold storage is written first and skips rows it already
        holds, so a run cut short is simply repeated by the next one. Returns
        how many rows left the archive, or None when either store is unreadable
        (nothing changes then).
        """
        archive_path = self._facts_archive_path(name)
        if not os.path.exists(archive_path):
            return 0
        try:
            with open(archive_path, encoding='utf-8') as fh:
                archived = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            _facts_facade.logger.warning(f"[FactStore] {name}: 读取归档文件失败，跳过冷存迁移: {exc}")
            return None
        if not isinstance(archived, list):
            return None
        directory = self._character_dir(name)
        try:
            cold = self._cold_identities(name)
        except cold_store.ColdStoreUnreadable as exc:
            _facts_facade.logger.warning(f"[FactStore] {name}: 读取冷存失败，跳过冷存迁移: {exc}")
            return None
        moving = [row for row in archived if _is_cold_row(row)]
        staying = [
            row for row in archived
            if not _is_cold_row(row) and _fact_scoped_identity(row) not in cold
        ]
        if len(staying) == len(archived):
            return 0
        _facts_facade.assert_cloudsave_writable(
            self._config_manager,
            operation="archive",
            target=f"memory/{name}/{cold_store.FACT_COLD_DIRNAME}",
        )
        cold_store.append(directory, moving, identity=_fact_scoped_identity)
        _facts_facade.atomic_write_json(archive_path, staying, indent=2, ensure_ascii=False)
        _facts_facade.logger.info(
            f"[FactStore] {name}: {len(archived) - len(staying)} 条降级事实从归档挪进冷存"
        )
        return len(archived) - len(staying)

    def _archive_absorbed(self, name: str) -> int:
        """Move facts that are absorbed and older than _ARCHIVE_AGE_DAYS into the archive file."""
        from datetime import timedelta
        _facts_facade.assert_cloudsave_writable(
            self._config_manager,
            operation="archive",
            target=f"memory/{name}/facts.json",
        )
        facts = self._facts.get(name, [])
        cutoff = datetime.now() - timedelta(days=_ARCHIVE_AGE_DAYS)
        active, to_archive = [], []
        for f in facts:
            if not isinstance(f, dict):
                active.append(f)
                continue
            try:
                created = datetime.fromisoformat(f.get('created_at', ''))
            except (ValueError, TypeError):
                active.append(f)
                continue
            created = to_naive_local(created)
            if f.get('absorbed') and created < cutoff and not f.get('_near_dup_pending'):
                to_archive.append(f)
            else:
                active.append(f)
        if not to_archive:
            return 0
        # Append to archive file.
        archive_path = self._facts_archive_path(name)
        existing_archive: list[dict] = []
        if os.path.exists(archive_path):
            try:
                with open(archive_path, encoding='utf-8') as fh:
                    data = json.load(fh)
                if isinstance(data, list):
                    existing_archive = data
            except (json.JSONDecodeError, OSError) as e:
                # Corrupt archive → abort this archive attempt, avoiding overwrite/data loss.
                _facts_facade.logger.warning(f"[FactStore] {name}: 读取归档文件失败，跳过本次归档: {e}")
                return 0
        # Idempotently merge appends by ID: after partial commit (archive succeeded, facts.json failed),
        # next archive resubmits the same facts; extend would leave two permanent
        # archive copies. Also repair historical on-disk duplicates.
        existing_archive = _merge_archive_entries(existing_archive, to_archive)
        _facts_facade.atomic_write_json(archive_path, existing_archive, indent=2, ensure_ascii=False)
        # Two-file commit order is always archive then facts.json: interruption leaves
        # both copies (duplicates converged by load_facts_full ID deduplication and next archive's
        # idempotent ID append), rather than neither (permanent archived-fact loss, including daily
        # import fingerprints, reimporting whole days). Never reverse this order.
        _facts_facade.atomic_write_json(self._facts_path(name), active, indent=2, ensure_ascii=False)
        # Change cache only after facts.json persists, matching event_log.record_and_save's
        # documented discipline (cache only changes after the disk write). Former cache-first
        # writes left cache missing k rows versus disk after failure;
        # save_facts eviction applies only to the preceding try, not here.
        #
        # Update active list in place, preserving references so external holders do not lose modifications.
        # Remove archived entries by object identity, not wholesale replacement with initial active snapshot:
        # add_facts appends after aload_facts without _get_lock (existing repository convention),
        # so snapshot replacement erases concurrent appends between snapshot and this point;
        # reordered writes extend that window from one disk write to two.
        # id() keys are safe: to_archive keeps strong references to these dictionaries throughout,
        # preventing collection and ID reuse by other objects.
        archived_identities = {id(f) for f in to_archive}
        facts[:] = [f for f in facts if id(f) not in archived_identities]
        # Report actual remaining cache length, not snapshot length: concurrent appends remain.
        _facts_facade.logger.info(f"[FactStore] {name}: 归档 {len(to_archive)} 条已吸收的旧 facts，剩余 {len(facts)} 条")
        return len(to_archive)

    def _archive_subject_facts(
        self, name: str, subject: MemorySubject, archived_at_iso: str,
        stale_cutoff: datetime,
    ) -> int | None:
        """Move the stale facts of one scoped subject into cold storage.

        Returns the number of rows stamped, or ``None`` when the pass ABORTED
        because a fresh write revealed the subject just revived — the caller
        must treat None as "subject is active again" and skip the subject's
        remaining stores, not as an ordinary zero-count result.

        Also moves the subject's rows that the absorbed-shrink path had ALREADY
        put into facts_archive.json, stamped the same way: those rows stay
        recallable by design for live subjects, so leaving them there would
        keep an archived subject searchable through its absorbed history.

        Time-driven counterpart of `_archive_absorbed` (score/absorbed-driven).
        Every moved row is stamped with ``subject_archived_at`` — the marker

          * excludes the row from both recall paths' archive pool
            (`hybrid_recall._aload_archive_facts` filters on it), unlike
            absorbed-archived rows which stay recallable by design;
          * excludes the row from the FTS near-dup guard in
            `_apersist_new_facts_locked`, so a revived subject re-stating an
            archived fact lands a NEW active fact instead of being silently
            deduped into invisibility;
          * is what `_restore_subject_facts` strips when moving rows back.

        ``stale_cutoff`` re-validates the sweep's staleness snapshot UNDER
        the write lock: a fact written (or explicitly restored — the
        ``restored_at`` stamp counts like the ledger does) between the
        sweep's judgement and this call has a timestamp ``>= cutoff`` — the
        subject just revived, so the whole archival aborts (returning
        ``None``) rather than sweeping the subject's first fresh memory out
        of recall. Rows with unparseable timestamps never archive (unknown
        age must not mean "old"), but also never veto the pass — one
        corrupt row must not immortalize the subject. A corrupt archive
        file likewise aborts with ``None``: proceeding would leave the
        subject permanently split (facts active, higher stores archived).

        Same two-file commit discipline as `_archive_absorbed`: cold storage
        first, then facts_archive.json and facts.json — an interruption leaves
        the row in both places (readers settle it as cold, the next run skips
        it), never in neither.
        """
        self.load_facts(name)  # ensure cache before taking the lock (non-reentrant)
        with self._get_lock(name):
            _facts_facade.assert_cloudsave_writable(
                self._config_manager,
                operation="archive",
                target=f"memory/{name}/facts.json",
            )
            if self._migrate_cold_rows_locked(name) is None:
                return None
            facts = self._facts.get(name, [])
            matching = [
                f for f in facts
                if isinstance(f, dict) and entry_matches_subject(f, subject)
            ]
            to_archive: list[dict] = []
            for f in matching:
                # Resurrection checks consider created_at and restored_at: explicit restores within
                # the decision window stamp restored_at while created_at remains old;
                # created_at alone would immediately rearchive newly restored rows.
                latest: datetime | None = None
                for field in ('created_at', 'restored_at'):
                    try:
                        parsed = datetime.fromisoformat(f.get(field) or '')
                    except (ValueError, TypeError):
                        continue
                    if parsed.tzinfo is not None:
                        parsed = parsed.astimezone().replace(tzinfo=None)
                    if latest is None or parsed > latest:
                        latest = parsed
                if latest is None:
                    continue  # Keep unknown-age rows active (as _archive_absorbed).
                if latest >= stale_cutoff:
                    # New writes/restores after decision resurrect the subject; abort the entire
                    # attempt. New writes enter active pool only; archive rows predate the decision
                    # snapshot, so resurrection checks need active rows only.
                    _facts_facade.logger.info(
                        f"[FactStore] {name}: subject "
                        f"[scoped {subject.kind}/{subject.subject_id}] 在归档窗口"
                        f"内有新写入，中止本轮 subject 归档"
                    )
                    return None
                to_archive.append(f)
            archive_path = self._facts_archive_path(name)
            existing_archive: list[dict] = []
            if os.path.exists(archive_path):
                try:
                    with open(archive_path, encoding='utf-8') as fh:
                        data = json.load(fh)
                    if not isinstance(data, list):
                        _facts_facade.logger.warning(
                            f"[FactStore] {name}: 归档文件顶层不是列表，"
                            "中止本轮 subject 归档"
                        )
                        return None
                    existing_archive = data
                except (json.JSONDecodeError, OSError) as e:
                    # Corrupt archives return abort (None), not ordinary zero, telling
                    # caller to skip reflection/persona too; otherwise repeated failures permanently
                    # split the subject into active facts but archived higher layers.
                    _facts_facade.logger.warning(
                        f"[FactStore] {name}: 读取归档文件失败，中止本轮 subject 归档: {e}"
                    )
                    return None
            # Same-subject rows already archived by absorbed shrinkage leave for
            # cold storage too, removed from recall together with active rows.
            # (The migration above already moved every marked row.)
            from_archive = [
                f for f in existing_archive
                if isinstance(f, dict) and entry_matches_subject(f, subject)
            ]
            stamped_in_archive = len(from_archive)
            if not to_archive and not stamped_in_archive:
                return 0
            stamped = []
            # An interrupted absorbed-archive commit can leave one row in both
            # places; the live copy is the newer one and is the one kept.
            for f in _merge_archive_entries(from_archive, to_archive):
                copy = dict(f)
                copy['subject_archived_at'] = archived_at_iso
                stamped.append(copy)
            try:
                cold_store.append(
                    self._character_dir(name), stamped, identity=_fact_scoped_identity,
                )
            except cold_store.ColdStoreUnreadable as e:
                _facts_facade.logger.warning(f"[FactStore] {name}: 读取冷存失败，中止本轮 subject 归档: {e}")
                return None
            try:
                if from_archive:
                    leaving = {id(f) for f in from_archive}
                    _facts_facade.atomic_write_json(
                        archive_path,
                        [f for f in existing_archive if id(f) not in leaving],
                        indent=2, ensure_ascii=False,
                    )
                if to_archive:
                    active = [f for f in facts if id(f) not in {id(x) for x in to_archive}]
                    _facts_facade.atomic_write_json(self._facts_path(name), active, indent=2, ensure_ascii=False)
                    # Remove cache rows in place by identity (retain concurrent appends for next save).
                    archived_identities = {id(f) for f in to_archive}
                    facts[:] = [f for f in facts if id(f) not in archived_identities]
            except Exception:
                # The rows are cold already; the next load filters the stale copies.
                self._facts.pop(name, None)
                raise
            # Privacy: log domain identifiers/counts only, never original text.
            _facts_facade.logger.info(
                f"[FactStore] {name}: subject 归档 [scoped {subject.kind}"
                f"/{subject.subject_id}] 活跃 {len(to_archive)} 条 + 归档池 "
                f"{stamped_in_archive} 条挪进冷存"
            )
            return len(to_archive) + stamped_in_archive

    async def aarchive_subject_facts(
        self, name: str, subject: MemorySubject, archived_at_iso: str,
        stale_cutoff: datetime,
    ) -> int | None:
        return await _facts_facade.asyncio.to_thread(
            self._archive_subject_facts, name, subject, archived_at_iso,
            stale_cutoff,
        )

    def _restore_subject_facts(
        self, name: str, subject: MemorySubject,
        restored_at_iso: str | None = None,
        archived_after_iso: str | None = None,
    ) -> int | None:
        """Move a subject's ``subject_archived_at`` rows from cold storage back into facts.json.

        Inverse of `_archive_subject_facts`, through ``cold_store.restore``:
        group rows keep their ids, facts.json is written before cold storage
        changes, so an interruption leaves the row cold rather than lost.

        Every restored row is stamped with ``restored_at``: the staleness
        ledger counts it as a write (see ``subject_archive._TIMESTAMP_FIELDS``),
        so an explicit restore resets the subject's archival clock instead of
        being undone by the very next sweep.

        A row carrying both subject and arbitration markers stays cold:
        subject restoration clears only its subject marker, preserving the
        independent trust-arbitration decision.

        Returns the number of rows touched, or ``None`` when facts_archive.json
        or cold storage cannot be read — mirroring the archival side's abort
        semantics, so the orchestrator skips the higher stores instead of
        leaving the subject split (facts still cold, reflections/persona active).
        """
        if restored_at_iso is None:
            restored_at_iso = datetime.now().isoformat()
        self.load_facts(name)
        with self._get_lock(name):
            _facts_facade.assert_cloudsave_writable(
                self._config_manager,
                operation="save",
                target=f"memory/{name}/facts.json",
            )
            if self._migrate_cold_rows_locked(name) is None:
                return None

            def _is_subject_marked_row(f) -> bool:
                return (
                    isinstance(f, dict)
                    and bool(f.get('subject_archived_at'))
                    and (
                        archived_after_iso is None
                        or str(f.get('subject_archived_at')) > archived_after_iso
                    )
                    and entry_matches_subject(f, subject)
                )

            directory = self._character_dir(name)
            facts = self._facts.get(name, [])

            def _activate(rows: list[dict]) -> None:
                self._activate_restored_rows(name, facts, rows)

            def _unmark_arbitration_row(f: dict) -> dict:
                if not (_is_subject_marked_row(f) and f.get('arbitration_archived_at')):
                    return f
                copy = dict(f)
                copy.pop('subject_archived_at', None)
                copy['restored_at'] = restored_at_iso
                return copy

            try:
                restored = cold_store.restore(
                    directory,
                    lambda f: _is_subject_marked_row(f) and not f.get('arbitration_archived_at'),
                    activate=_activate, restored_at=restored_at_iso,
                )
                arbitration_rows = cold_store.rewrite_rows(directory, _unmark_arbitration_row)
            except cold_store.ColdStoreUnreadable as e:
                _facts_facade.logger.warning(f"[FactStore] {name}: 读取冷存失败，中止 subject 恢复: {e}")
                return None
            if not restored and not arbitration_rows:
                return 0
            _facts_facade.logger.info(
                f"[FactStore] {name}: subject 恢复 [scoped {subject.kind}"
                f"/{subject.subject_id}] {len(restored)} 条 facts 回活跃池，"
                f"{len(arbitration_rows)} 条仲裁 loser 清除 subject 标记后保持冷存"
            )
            return len(restored) + len(arbitration_rows)

    async def arestore_subject_facts(
        self, name: str, subject: MemorySubject,
        restored_at_iso: str | None = None,
        archived_after_iso: str | None = None,
    ) -> int | None:
        return await _facts_facade.asyncio.to_thread(
            self._restore_subject_facts, name, subject, restored_at_iso,
            archived_after_iso,
        )
