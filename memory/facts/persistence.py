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
"""Fact loading, migration, cold-stub filtering, and atomic persistence methods."""

from __future__ import annotations

import json
import os
from contextlib import nullcontext
from datetime import datetime
from memory import cold_store
from utils.cloudsave_runtime import MaintenanceModeError

from memory import facts as _facts_facade
from ._shared import (
    _ARCHIVE_COOLDOWN_HOURS,
    _fact_scoped_identity,
    _is_cold_row,
    _readable_fact_id,
)

class PersistenceMixin:
    def _facts_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(ensure_character_dir(self._config_manager.memory_dir, name), 'facts.json')

    # v1→v2 entity key renames
    _ENTITY_RENAMES = {'user': 'master', 'ai': 'neko'}

    def load_facts(self, name: str) -> list[dict]:
        from memory.long_term_sync import require_consistent_views
        require_consistent_views(os.path.join(self._config_manager.memory_dir, name))
        if name in self._facts:
            return self._facts[name]
        with self._get_lock(name):
            return self._load_facts_locked(name)

    def _character_dir(self, name: str) -> str:
        # Cold shards sit next to facts_archive.json (same character directory).
        return os.path.dirname(self._facts_archive_path(name))

    def _cold_fact_stubs(self, name: str) -> list[dict]:
        """Stubs of this character's cold facts (no text); raises ``ColdStoreUnreadable``."""
        return cold_store.status(self._character_dir(name))

    def _shared_cold_fact_stubs(self, name: str) -> list[dict]:
        """Cold fact stubs as the shared sync list states them.

        Another device may have retracted a fact this device holds as
        superseded (or the reverse); the merged ``cold`` list then says which
        holds. Source counting reads the stub, so the list's status is laid
        over it; the shard itself is never rewritten by a sync.
        """
        from memory.long_term_sync import shared_cold_facts
        stubs = self._cold_fact_stubs(name)
        shared = shared_cold_facts(self._character_dir(name))
        if not shared:
            return stubs
        for row in stubs:
            item = shared.get((row.get('entity'), row.get('id')))
            if item is None or row.get('scope') or row.get('subject_kind'):
                continue
            if item['status'] == 'retracted':
                row.pop('superseded_by', None)
            elif item.get('supersededById'):
                row['superseded_by'] = item['supersededById']
        return stubs

    def _activate_restored_rows(self, name: str, facts: list, rows: list[dict]) -> None:
        """Durably add revived cold rows to the active pool, skipping any already there.

        A retry after cold storage failed to let go of a group row finds its
        copy already active (same scoped identity); appending it again would
        leave two rows with one id.
        """
        held = {
            identity for identity in map(_fact_scoped_identity, facts)
            if identity is not None
        }
        fresh = [row for row in rows if _fact_scoped_identity(row) not in held]
        _facts_facade.atomic_write_json(
            self._facts_path(name), facts + fresh, indent=2, ensure_ascii=False,
        )
        facts.extend(fresh)

    def _cold_identities(self, name: str) -> set:
        return {
            identity for identity in (
                _fact_scoped_identity(row) for row in self._cold_fact_stubs(name)
            ) if identity is not None
        }

    def _without_cold_copies(self, name: str, rows: list) -> list:
        """Drop rows also held in cold storage: a cold copy wins over any other.

        Such a pair is left by an interrupted demotion, or by a cloud save
        restoring an older file after the row went cold here. The dropped copy
        leaves the file at the next save. An unreadable cold store drops
        nothing; its readers fail on their own.
        """
        try:
            cold = self._cold_identities(name)
        except cold_store.ColdStoreUnreadable as exc:
            _facts_facade.logger.warning(f"[FactStore] {name}: 冷存读取失败，暂不按冷存过滤: {exc}")
            return rows
        if not cold:
            return rows
        kept = [row for row in rows if _fact_scoped_identity(row) not in cold]
        if len(kept) != len(rows):
            _facts_facade.logger.warning(
                f"[FactStore] {name}: {len(rows) - len(kept)} 条事实同时在冷存里，按冷存处理"
            )
        return kept

    async def aload_facts(self, name: str) -> list[dict]:
        from memory.long_term_sync import require_consistent_views
        require_consistent_views(os.path.join(self._config_manager.memory_dir, name))
        if name in self._facts:
            return self._facts[name]
        return await _facts_facade.asyncio.to_thread(self.load_facts, name)

    def load_facts_full(self, name: str, *, include_cold_stubs: bool = False) -> list[dict]:
        """Full fact pool: active + archived (Phase C-2), cold storage excluded.

        Cold rows (demoted by arbitration or by subject archival) are left out,
        including any still waiting in facts_archive.json to move to cold
        storage. ``include_cold_stubs`` appends them as stubs instead: every
        field except the wording, tags and vectors, plus ``text_sha256``. Only
        callers that count or match rows by id, subject, status or provenance
        pass it; none of them may show or rank a stub.

        Archived = old entries already moved into facts_archive.json by
        `_archive_absorbed` (absorbed more than _ARCHIVE_AGE_DAYS = 7 days ago).

        For scenarios where "distant history must be searchable" — currently the
        RELATED_CONTEXT recall of reflection synthesis. Returns a new list; the
        archive never enters the cache.

        A corrupted archive file degrades best-effort to active-only, no raise
        (incl. invalid UTF-8: daily import's fingerprint scan now reads the
        archive via this loader before any per-day isolation, so a non-UTF-8
        archive must degrade here instead of aborting the whole import — Codex
        P2). ``UnicodeDecodeError`` is a ``ValueError`` subclass, distinct from
        ``JSONDecodeError``, so it is listed explicitly.

        Rows whose id and subject also exist among the active facts are
        dropped, keeping the active copy. That is not an optional extra guard — it is the read
        half of ``_archive_absorbed``'s two-file commit protocol, whose write
        order deliberately prefers "the row is in both files" over "the row is
        in neither". Something has to collapse "in both", and a warning makes
        this a backstop with a detector rather than a cover-up."""
        from memory.long_term_sync import require_consistent_views, consistent_view_read, LongTermMemoryRecoveryRequired
        require_consistent_views(os.path.join(self._config_manager.memory_dir, name))
        active = self.load_facts(name)
        def checked(rows):
            require_consistent_views(os.path.join(self._config_manager.memory_dir, name))
            if self._facts.get(name) is not active:
                raise LongTermMemoryRecoveryRequired()
            return rows
        archive_path = self._facts_archive_path(name)
        cold_stubs: list[dict] = []
        try:
            cold_stubs = (
                self._shared_cold_fact_stubs(name) if include_cold_stubs
                else self._cold_fact_stubs(name)
            )
        except cold_store.ColdStoreUnreadable as e:
            # Best-effort like the archive read below: the cold rows are left out.
            _facts_facade.logger.warning(f"[FactStore] {name}: 读取冷存失败，不含冷存: {e}")
        cold_identities = {
            identity for identity in map(_fact_scoped_identity, cold_stubs)
            if identity is not None
        }

        def with_cold(rows: list) -> list:
            if include_cold_stubs:
                rows.extend(dict(row) for row in cold_stubs)
            return rows

        if not os.path.exists(archive_path):
            return checked(with_cold(list(active)))
        try:
            with consistent_view_read(archive_path):
                with open(archive_path, encoding='utf-8') as f:
                    archived = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            _facts_facade.logger.warning(f"[FactStore] {name}: 读取 archive 失败，降级仅 active: {e}")
            return checked(with_cold(list(active)))
        if not isinstance(archived, list):
            return checked(with_cold(list(active)))
        # Active/archive ID overlap is residue from an interrupted two-file archive commit.
        # Converge to active copy: at least as recent as archive, authoritative for monotonic
        # flags such as absorbed / signal_processed.
        # Scoped identity, not the bare id: another subject may reuse the same fact id.
        merged = list(active)
        active_identities = {
            identity for identity in map(_fact_scoped_identity, merged)
            if identity is not None
        }
        dropped = 0
        for f in archived:
            if not isinstance(f, dict):
                continue
            if _fact_scoped_identity(f) in active_identities:
                dropped += 1
                continue
            if _fact_scoped_identity(f) in cold_identities:
                continue
            if _is_cold_row(f):
                if include_cold_stubs:
                    merged.append(cold_store.stub(f))
                continue
            merged.append(f)
        if dropped:
            _facts_facade.logger.warning(
                f"[FactStore] {name}: active/archive 存在 {dropped} 条 id 重叠，"
                f"已按 active 收敛（多半是上一次归档两文件提交被打断）"
            )
        return checked(with_cold(merged))

    async def aload_facts_full(self, name: str, *, include_cold_stubs: bool = False) -> list[dict]:
        return await _facts_facade.asyncio.to_thread(
            self.load_facts_full, name, include_cold_stubs=include_cold_stubs,
        )

    def _assert_active_facts_readable(self, name: str) -> int:
        """Raise ``RuntimeError`` when ``facts.json`` exists but is not a readable JSON list.

        Keyed scoped writes only: the lenient loader caches an unreadable
        active pool as empty, and saving on top of that would erase it.
        Returns the number of rows on disk (0 when the file is absent).
        """
        path = self._facts_path(name)
        if not os.path.exists(path):
            return 0
        from utils.file_utils import read_json_tolerating_replace

        try:
            # Tolerate Windows sharing conflicts from archive/dedup writers' os.replace; replacement windows are not unreadable files.
            data = read_json_tolerating_replace(path)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
            raise RuntimeError(f"facts of {name!r} unreadable: {e}") from e
        if not isinstance(data, list):
            raise RuntimeError(f"facts of {name!r} is not a list")
        return len(data)

    def load_facts_strict(
        self, name: str, *, include_archive: bool = False, include_cold_stubs: bool = False,
    ) -> list[dict]:
        """Active facts (plus archived ones) read from disk, raising ``RuntimeError`` when unreadable.

        For keyed writes deciding "was this already written": unlike
        :meth:`load_facts` (which caches the active pool in memory after a
        successful load) and :meth:`load_facts_full` (which treats an unreadable
        archive as absent), this method bypasses the in-memory cache and fails
        closed on unreadable active, archive, or cold-shard files. Absent files
        read as empty; a row present in both files (an interrupted archive
        commit) counts once, as in :meth:`load_facts_full`.
        ``include_cold_stubs`` adds the cold rows as stubs (no text, see
        :meth:`load_facts_full`); a cold row's stub replaces any copy of it in
        the two files. An unreadable cold shard raises too.
        """
        from memory.long_term_sync import require_consistent_views, consistent_view_read
        require_consistent_views(os.path.join(self._config_manager.memory_dir, name))
        from utils.file_utils import read_json_tolerating_replace
        try:
            cold_stubs = (
                self._shared_cold_fact_stubs(name) if include_cold_stubs
                else self._cold_fact_stubs(name)
            )
        except cold_store.ColdStoreUnreadable as e:
            raise RuntimeError(f"cold facts of {name!r} unreadable: {e}") from e
        cold_identities = {
            identity for identity in map(_fact_scoped_identity, cold_stubs)
            if identity is not None
        }

        rows: list[dict] = []
        paths = [self._facts_path(name)]
        if include_archive:
            paths.append(self._facts_archive_path(name))
        active_identities: set = set()
        with consistent_view_read(paths[0]):
            for index, path in enumerate(paths):
                if not os.path.exists(path):
                    continue
                try:
                    data = read_json_tolerating_replace(path)
                except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
                    raise RuntimeError(f"{os.path.basename(path)} of {name!r} unreadable: {e}") from e
                if not isinstance(data, list):
                    raise RuntimeError(f"{os.path.basename(path)} of {name!r} is not a list")
                for row in data:
                    if not isinstance(row, dict):
                        continue
                    identity = _fact_scoped_identity(row)
                    if identity in cold_identities:
                        continue
                    if index == 0:
                        # Only rows still visible after the cold filter shadow archive copies.
                        if identity is not None:
                            active_identities.add(identity)
                    elif identity is not None and identity in active_identities:
                        # Same as load_facts_full: interrupted two-file archive commits duplicate rows; active copy wins.
                        continue
                    if index > 0 and _is_cold_row(row):
                        if include_cold_stubs:
                            rows.append(cold_store.stub(row))
                        continue
                    rows.append(row)
        if include_cold_stubs:
            rows.extend(dict(row) for row in cold_stubs)
        return rows

    async def aload_facts_strict(
        self, name: str, *, include_archive: bool = False, include_cold_stubs: bool = False,
    ) -> list[dict]:
        return await _facts_facade.asyncio.to_thread(
            self.load_facts_strict, name, include_archive=include_archive,
            include_cold_stubs=include_cold_stubs,
        )

    def _read_archived_effect_keys(self, name: str) -> set[str]:
        """Effect keys stamped on archived rows (keyed scoped writes only).

        Strict: an absent archive is an empty set, but an unreadable or
        malformed one raises ``RuntimeError`` instead of reading as "no keys".
        The effect key is what keeps a keyed retry from writing a fact twice;
        guessing "absent" could duplicate a row that was archived meanwhile,
        so the keyed apply fails and is retried with the same key.
        """
        try:
            cold = self._cold_fact_stubs(name)
        except cold_store.ColdStoreUnreadable as e:
            raise RuntimeError(f"cold facts of {name!r} unreadable: {e}") from e
        keys = {
            row['effect_key'] for row in cold
            if isinstance(row.get('effect_key'), str)
        }
        archive_path = self._facts_archive_path(name)
        if not os.path.exists(archive_path):
            return keys
        from utils.file_utils import read_json_tolerating_replace

        try:
            archived = read_json_tolerating_replace(archive_path)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
            raise RuntimeError(f"facts archive of {name!r} unreadable: {e}") from e
        if not isinstance(archived, list):
            raise RuntimeError(f"facts archive of {name!r} is not a list")
        return keys | {
            row['effect_key'] for row in archived
            if isinstance(row, dict) and isinstance(row.get('effect_key'), str)
        }

    @classmethod
    def _migrate_v1_entity_values(cls, facts: list[dict]) -> bool:
        """Rename v1 entity values ('user'→'master', 'ai'→'neko') in-place."""
        changed = False
        for f in facts:
            old = f.get('entity')
            new = cls._ENTITY_RENAMES.get(old)
            if new:
                f['entity'] = new
                changed = True
        return changed

    def save_facts(self, name: str, *, _fact_lock_held: bool = False) -> None:
        lock_context = (
            nullcontext() if _fact_lock_held else self._get_lock(name)
        )
        with lock_context:
            try:
                _facts_facade.assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{name}/facts.json",
                )
                facts = self._facts.get(name, [])
                path = self._facts_path(name)
                # Remove temporary notes first (see _SIGNAL_RESET_PENDING), unconditionally;
                # never put this inside read-merge branches below. Missing files
                # or batches without monotonic flags skip those branches and would persist the notes.
                just_unsealed_ids = {
                    _readable_fact_id(f) for f in facts
                    if isinstance(f, dict)
                    and f.pop(self._SIGNAL_RESET_PENDING, False)
                    and _readable_fact_id(f) is not None
                }
                # Read-merge-write protects monotonic flags written by other processes/paths
                # (False → True only: absorbed, signal_processed).
                # Otherwise stale-cache writes overwrite disk True with False, making drain loops
                # resend facts to Stage-2 / synthesize duplicate reflections.
                _DISK_FACTS_ABSENT = object()
                try:
                    with open(path, encoding='utf-8') as f:
                        disk_facts = json.load(f)
                except FileNotFoundError:
                    disk_facts = _DISK_FACTS_ABSENT
                except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
                    _facts_facade.logger.warning(
                        f"[FactStore] {name}: facts.json 读取失败，拒绝覆盖写入: {e}"
                    )
                    raise RuntimeError(
                        f"Refusing to overwrite unreadable facts file {path}: {e}"
                    ) from e
                if disk_facts is not _DISK_FACTS_ABSENT:
                    if not isinstance(disk_facts, list):
                        _facts_facade.logger.warning(
                            f"[FactStore] {name}: facts.json 顶层非 list ({type(disk_facts).__name__})，拒绝覆盖写入"
                        )
                        raise RuntimeError(
                            f"Refusing to overwrite non-list facts file {path}"
                        )
                    # Index keys always use _readable_fact_id: manual/partly corrupt legacy rows
                    # may lack ID (f['id'] raises KeyError) or use list/dict IDs
                    # (sets raise TypeError: unhashable).
                    absorbed_ids = {
                        _readable_fact_id(f) for f in disk_facts
                        if isinstance(f, dict) and f.get('absorbed')
                        and _readable_fact_id(f) is not None
                    }
                    signal_processed_ids = {
                        _readable_fact_id(f) for f in disk_facts
                        if isinstance(f, dict) and f.get('signal_processed')
                        and _readable_fact_id(f) is not None
                    }
                    # ⚠ Remaining window: no interprocess lock spans this disk read and
                    # atomic_write_json below (self._get_lock protects local threads only), so
                    # another process may upgrade/consume after we read ai_disclosure, then our write restores
                    # False.
                    disk_disclosure_ids: set = set()
                    disk_other_source_ids: set = set()
                    for f in disk_facts:
                        if not isinstance(f, dict):
                            continue
                        disk_id = _readable_fact_id(f)
                        if disk_id is None:
                            continue
                        if f.get('source', self._SOURCE_DEFAULT) == 'ai_disclosure':
                            disk_disclosure_ids.add(disk_id)
                        else:
                            disk_other_source_ids.add(disk_id)
                    disk_ai_disclosure_ids = disk_disclosure_ids - disk_other_source_ids
                    if absorbed_ids or signal_processed_ids:
                        for f in facts:
                            if f.get('id') in absorbed_ids:
                                f['absorbed'] = True
                            unsealed = (
                                f.get('id') in just_unsealed_ids
                                and f.get('id') in disk_ai_disclosure_ids
                            )
                            if f.get('id') in signal_processed_ids and not unsealed:
                                f['signal_processed'] = True
                _facts_facade.atomic_write_json(path, facts, indent=2, ensure_ascii=False)
            except Exception:
                # Cache divergence guard (CodeRabbit PR-956 Major,
                # mirroring `PersonaManager.asave_persona`'s round-7
                # fix from PR #936). Callers like
                # `FactDedupResolver._aapply_decisions` mutate the
                # in-memory list directly via `facts[:] = [...]` and
                # then call us; if the disk write raises, the cache
                # still holds the post-mutation state but disk
                # doesn't, so the next `aload_facts` returns
                # divergent data. Evicting forces a fresh disk read.
                self._facts.pop(name, None)
                raise
            # Throttle archive by file modification time: attempt only after _ARCHIVE_COOLDOWN_HOURS since last archive.
            try:
                archive_path = self._facts_archive_path(name)
                if os.path.exists(archive_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(archive_path))
                    if (datetime.now() - mtime).total_seconds() < _ARCHIVE_COOLDOWN_HOURS * 3600:
                        return
                # Record last archive attempt time in marker file (even if archive file does not yet exist).
                marker_path = archive_path + '.last_attempt'
                if os.path.exists(marker_path):
                    mtime = datetime.fromtimestamp(os.path.getmtime(marker_path))
                    if (datetime.now() - mtime).total_seconds() < _ARCHIVE_COOLDOWN_HOURS * 3600:
                        return
                # Touch marker only when archiving actually ran (completed or genuinely failed). Maintenance is
                # an expected skip, consuming no archive window; otherwise every maintenance save extends
                # cooldown by 24h, delaying archival another full day afterward.
                archive_ran = True
                try:
                    self._migrate_cold_rows_locked(name)
                    self._archive_absorbed(name)
                except MaintenanceModeError as exc:
                    # Match load_facts migration branch: maintenance skips log at debug.
                    #
                    # Narrow but reachable: save_facts' initial assert_cloudsave_writable
                    # already checks maintenance, excluding steady maintenance. Only the window where
                    # maintenance begins after that check passes but before _archive_absorbed's
                    # check can reach here. No interprocess lock spans the checks; another process/thread changes
                    # root_state. Never delete archive_ran as unreachable:
                    # in this race it prevents consuming the 24h archive window.
                    archive_ran = False
                    _facts_facade.logger.debug(f"[FactStore] {name}: 维护态跳过归档: {exc}")
                except Exception:
                    # Archive failure must not fail callers' saves (facts.json already persisted
                    # successfully), but must leave evidence: former bare pass hid partial two-file
                    # commits in production. Touch marker normally; if archive write itself fails,
                    # archive mtime stays unchanged and cooldown fails, so omitting touch would rerun
                    # inevitably failing archive on every save.
                    _facts_facade.logger.warning(
                        f"[FactStore] {name}: 归档失败，facts.json 已落盘，"
                        f"{_ARCHIVE_COOLDOWN_HOURS}h 后重试",
                        exc_info=True,
                    )
                if archive_ran:
                    # Update marker regardless of whether archiving found actual entries.
                    with open(marker_path, 'w', encoding='utf-8') as f:
                        f.write(datetime.now().isoformat())
            except Exception:
                # Cooldown checks / marker persistence failures do not invalidate successful facts.json saves,
                # but must no longer be swallowed entirely.
                _facts_facade.logger.debug(
                    f"[FactStore] {name}: 归档冷却判定失败", exc_info=True,
                )

    async def asave_facts(
        self, name: str, *, _fact_lock_held: bool = False,
    ) -> None:
        await _facts_facade.asyncio.to_thread(
            self.save_facts, name, _fact_lock_held=_fact_lock_held,
        )
