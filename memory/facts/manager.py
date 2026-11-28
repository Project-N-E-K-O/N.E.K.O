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
"""FactStore assembly combining semantic domain mixins."""

from __future__ import annotations

import threading
from datetime import datetime
from memory.scopes import MemorySubject, coerce_subject
from typing import TYPE_CHECKING

from memory import facts as _facts_facade
from .persistence import PersistenceMixin
from .forget_archive import ForgetArchiveMixin
from .speaker_trust import SpeakerTrustMixin
from .formatting import FormattingMixin
from .extraction import ExtractionMixin
from .write_pipeline import WritePipelineMixin
from .signals import SignalsMixin
from .external_import import ExternalImportMixin
from .recheck_manual import RecheckManualMixin

if TYPE_CHECKING:
    from memory.timeindex import TimeIndexedMemory

class FactStore(
    PersistenceMixin,
    ForgetArchiveMixin,
    SpeakerTrustMixin,
    FormattingMixin,
    ExtractionMixin,
    WritePipelineMixin,
    SignalsMixin,
    ExternalImportMixin,
    RecheckManualMixin,
):
    """Manages raw fact extraction, deduplication, and persistence."""

    # In-process mirror of legacy-fact recheck failure counts: {name: {fid: {"n", "at"}}}.
    # Authoritative session working copy; facts.json fields serve persistence/restart recovery only.
    # Matches ReflectionEngine._synth_backoff_mem (memory/reflection/manager.py:79):
    # counters stored only in the unwritable file can never trigger circuit breakers under read-only
    # filesystems / permission errors / maintenance mode.
    #
    # Class default None + lazy creation, not just __init__: multiple repository sites
    # construct via `FactStore.__new__(FactStore)`, bypassing __init__;
    # （tests/unit/test_ai_aware_stage1_path_b.py、tests/unit/test_group_memory_scopes.py），
    # those instances would otherwise raise AttributeError upon mirror access.
    #
    # Deliberate instance-level state: a liveness fallback, not persistent state. Hot reload
    # (app/memory_server/runtime.py rebuilding FactStore) merely retries stuck facts a few extra
    # rounds, whereas module singletons leak state between pytest cases.
    _recheck_attempts_mem: dict | None = None

    _recheck_mem_guard: threading.Lock | None = None

    # fact_dedup LLM arbitration queue. Stage-2 only finds candidates for its decisions; without
    # resolver here, Stage-2 matches can only be logged (see _aenqueue_near_dup_pairs).
    _dedup_resolver = None

    def attach_dedup_resolver(self, resolver) -> None:
        """Wire the arbitration queue that Stage-2 near-dup hits feed into.

        Set by the memory runtime after both objects exist (the resolver
        takes the store in its constructor, so the store cannot build it).
        """
        self._dedup_resolver = resolver

    # Pinned-fact section (memory/pinned_facts.py). Without one, the ``pin``
    # fields of Stage-1 output are simply not read.
    _pinned_fact_store = None

    def attach_pinned_fact_store(self, store) -> None:
        """Wire the pinned-fact section that private-chat Stage-1 results feed.

        Set by the memory runtime next to ``attach_dedup_resolver``; the
        store needs the persona manager for its character-card check, which
        the FactStore does not hold.
        """
        self._pinned_fact_store = store

    async def _aadmit_pinned_candidates(
        self, lanlan_name: str, extracted, *, default_source: str = 'user_observation',
        conversation_at: datetime | None = None,
    ) -> None:
        """Offer one private-chat Stage-1 result to the pinned-fact section.

        Best-effort and called only after the facts themselves persisted: a
        failure here leaves the ordinary pipeline untouched, and the fact still
        reaches the prompt the slow way. ``conversation_at`` (the latest
        message of the extracted window; None means now) dates the entries.
        """
        store = self._pinned_fact_store
        if store is None or not extracted:
            return
        try:
            await store.aadmit_extracted(
                lanlan_name, extracted, default_source=default_source,
                noted_at=(
                    conversation_at.timestamp()
                    if isinstance(conversation_at, datetime) else None
                ),
            )
        except Exception as e:
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: pinned-fact admission failed: "
                f"{type(e).__name__}"
            )

    def __init__(self, *, time_indexed_memory: TimeIndexedMemory | None = None):
        self._config_manager = _facts_facade.get_config_manager()
        self._time_indexed = time_indexed_memory
        self._facts: dict[str, list[dict]] = {}  # {lanlan_name: [fact, ...]}
        self._locks: dict[str, threading.Lock] = {}  # Per-character file locks.
        self._locks_guard = threading.Lock()  # Protect the _locks dictionary itself.
        self._persist_alocks: dict[str, _facts_facade.asyncio.Lock] = {}
        # Courtesy call-start interval for external daily imports: it lives on the shared FactStore, so several characters
        # importing at once share one interval (they share one provider quota).
        # The last call time is stored on the gate lock object (gate.last_call_at), so sharing this one lock
        # when the runtime hot-replaces the FactStore shares the interval state as well.
        # Built at construction (not lazily): runtime hot replacement copies this object to the new
        # FactStore, and a lazily built gate could be copied as None, leaving old and new imports with separate gates.
        # asyncio.Lock only binds an event loop on its first wait, so creating it at construction is safe.
        self._external_daily_call_gate: _facts_facade.asyncio.Lock = _facts_facade.asyncio.Lock()
        self._external_daily_call_gate.last_call_at = 0.0
        # Ordering hint for segmented external daily imports: days extracted in this process whose facts all matched existing
        # facts and left no completion marker ({character: {day fingerprint}}). Only used to order them after deferred days;
        # never skips a day on its own and is never persisted, so a rollback or restart clears it and restores the original order, keeping rollback self-healing intact.
        self._external_daily_deduped_fps: dict[str, set[str]] = {}
        # Per-character, per-subject erase generations. Scoped extraction
        # captures one before its LLM call and rechecks it under the persistence
        # lock, so an in-flight pre-forget request cannot recreate erased facts.
        self._subject_forget_generations: dict[tuple[str, str, str], int] = {}
        self._active_subject_forgets: set[tuple[str, str, str]] = set()
        # Restore and forget are multi-store transactions.  A dedicated
        # per-subject lock prevents restore from re-appending an archive shard
        # between the individual store erases.  runtime reload deliberately
        # shares this registry with the replacement FactStore so requests that
        # captured the old instance participate in the same transaction.
        self._subject_forget_transaction_locks: dict[
            tuple[str, str, str], _facts_facade.asyncio.Lock
        ] = {}

    def _get_lock(self, name: str) -> threading.Lock:
        """Get the character-specific file lock (lazily created)"""
        if name not in self._locks:
            with self._locks_guard:
                if name not in self._locks:  # double-check
                    self._locks[name] = threading.Lock()
        return self._locks[name]

    def _get_persist_alock(self, name: str) -> _facts_facade.asyncio.Lock:
        """Serialize each character's load/dedup/mutate/save fact pipeline."""
        if name not in self._persist_alocks:
            with self._locks_guard:
                if name not in self._persist_alocks:
                    self._persist_alocks[name] = _facts_facade.asyncio.Lock()
        return self._persist_alocks[name]

    def _subject_forget_generation(
        self, name: str, subject: MemorySubject,
    ) -> int:
        generations = getattr(self, '_subject_forget_generations', None)
        if generations is None:
            # Some focused tests construct FactStore via __new__.
            generations = {}
            self._subject_forget_generations = generations
        return generations.get((name, subject.key, subject.scope), 0)

    def _bump_subject_forget_generation(
        self, name: str, subject: MemorySubject,
    ) -> int:
        generations = getattr(self, '_subject_forget_generations', None)
        if generations is None:
            generations = {}
            self._subject_forget_generations = generations
        key = (name, subject.key, subject.scope)
        generations[key] = generations.get(key, 0) + 1
        return generations[key]

    @staticmethod
    def _subject_forget_key(
        name: str, subject: MemorySubject,
    ) -> tuple[str, str, str]:
        return (name, subject.key, subject.scope)

    def _subject_forget_is_active(
        self, name: str, subject: MemorySubject,
    ) -> bool:
        active = getattr(self, '_active_subject_forgets', None)
        return bool(
            active is not None
            and self._subject_forget_key(name, subject) in active
        )

    def _subject_forget_fields_are_active(
        self, name: str, subject_key: object, scope: object,
    ) -> bool:
        """Check a queue row's stamped isolation fields against tombstones."""
        if not isinstance(subject_key, str) or not isinstance(scope, str):
            return False
        active = getattr(self, '_active_subject_forgets', None)
        return bool(active and (name, subject_key, scope) in active)

    def _get_subject_forget_transaction_lock(
        self, name: str, subject,
    ) -> _facts_facade.asyncio.Lock:
        """Return the cross-store restore/forget lock for one subject."""
        memory_subject = coerce_subject(subject)
        if memory_subject is None:
            raise ValueError("subject transaction requires an explicit subject")
        locks = getattr(self, '_subject_forget_transaction_locks', None)
        if locks is None:
            # Focused tests may construct FactStore via __new__.
            locks = {}
            self._subject_forget_transaction_locks = locks
        key = self._subject_forget_key(name, memory_subject)
        if key not in locks:
            guard = getattr(self, '_locks_guard', None)
            if guard is None:
                guard = threading.Lock()
                self._locks_guard = guard
            with guard:
                if key not in locks:
                    locks[key] = _facts_facade.asyncio.Lock()
        return locks[key]

    async def abegin_subject_forget(self, name: str, subject) -> None:
        """Open a fact-write tombstone for the complete scoped-forget route."""
        memory_subject = coerce_subject(subject)
        if memory_subject is None:
            raise ValueError("abegin_subject_forget requires an explicit subject")
        async with self._get_persist_alock(name):
            active = getattr(self, '_active_subject_forgets', None)
            if active is None:
                active = set()
                self._active_subject_forgets = active
            key = self._subject_forget_key(name, memory_subject)
            if key in active:
                raise RuntimeError("subject forget is already active")
            active.add(key)
            self._bump_subject_forget_generation(name, memory_subject)

    async def aend_subject_forget(self, name: str, subject) -> None:
        """Close the route tombstone and invalidate work started inside it."""
        memory_subject = coerce_subject(subject)
        if memory_subject is None:
            raise ValueError("aend_subject_forget requires an explicit subject")
        async with self._get_persist_alock(name):
            active = getattr(self, '_active_subject_forgets', None)
            if active is None:
                return
            key = self._subject_forget_key(name, memory_subject)
            if key in active:
                self._bump_subject_forget_generation(name, memory_subject)
                active.remove(key)
