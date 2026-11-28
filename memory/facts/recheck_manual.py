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
"""Unabsorbed queries, legacy fact recheck, and manual edit/archive/merge methods."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from config import MEMORY_SCHEMA_VERSION_CURRENT
from memory.scopes import MemorySubject, entry_matches_subject, subject_from_entry
from utils.cloudsave_runtime import MaintenanceModeError
from utils.file_utils import robust_json_loads
from utils.token_tracker import set_call_type

from memory import facts as _facts_facade
from ._shared import (
    _RECHECK_MEM_BOOTSTRAP,
    _merge_archive_entries,
    _readable_fact_id,
    safe_importance,
    safe_int_field,
)

class RecheckManualMixin:
    def get_unabsorbed_facts(
        self,
        name: str,
        min_importance: int = 5,
        *,
        subject: MemorySubject | dict | None = None,
    ) -> list[dict]:
        """Get facts that haven't been consumed by a reflection yet."""
        facts = self.load_facts(name)
        return [
            f for f in facts
            if not f.get('absorbed')
            and f.get('importance', 0) >= min_importance
            and entry_matches_subject(f, subject)
        ]

    async def _rollback_uncommitted_facts(
        self, lanlan_name: str, new_facts: list, existing_hashes: set,
        upgraded_snapshots: list | None = None,
        provenance_snapshots: list | None = None,
        source_snapshots: list | None = None,
    ) -> None:
        """Undo in-memory effects of a batch that never reached disk.

        The fail-closed callers retry on error; if the cache and hash set
        keep the uncommitted rows, that retry deduplicates into an empty
        success and the caller advances a volatile cursor over facts that
        facts.json never received. In-place source and provenance changes are
        restored so a retry cannot hit the dedup guard and skip persistence."""
        for entry, previous in reversed(source_snapshots or []):
            entry.pop('source_message_ids', None)
            entry.pop('source_created_at', None)
            entry.update(previous)
        for entry, prev_source, prev_signal in (upgraded_snapshots or []):
            # Restore in-place upgrades too: otherwise retry hits upgrade guard (source already
            # user_observation), gets upgraded_count=0, skips saving altogether;
            # caller receives success and advances cursor while disk fact remains unconfirmed.
            entry['source'] = prev_source
            if prev_signal is None:
                entry.pop('signal_processed', None)
            else:
                entry['signal_processed'] = prev_signal
        for entry, previous in reversed(provenance_snapshots or []):
            # Must be the SAME key set `_reconcile_existing_provenance` pops
            # and rewrites — a key it wrote but this loop does not clear would
            # survive the rollback and leave the cached row carrying an
            # attribution that never reached disk.
            for key in (
                'speaker_id', 'speaker_label', 'speaker_trust',
                'speaker_entity_id', 'speaker_provenance_mixed',
            ):
                entry.pop(key, None)
            entry.update(previous)
        added_ids = {
            nf.get('id') for nf in new_facts if isinstance(nf, dict)
        }
        added_hashes = {
            nf.get('hash') for nf in new_facts
            if isinstance(nf, dict) and nf.get('hash')
        }
        cache = self._facts.get(lanlan_name)
        if cache is not None:
            cache[:] = [
                f for f in cache
                if not (isinstance(f, dict) and f.get('id') in added_ids)
            ]
        for content_hash in added_hashes:
            existing_hashes.discard(content_hash)
        if self._time_indexed is not None:
            for fact_id in added_ids:
                if not fact_id:
                    continue
                try:
                    await self._time_indexed.adelete_fact_from_index(
                        lanlan_name, fact_id,
                    )
                except Exception:
                    _facts_facade.logger.warning(
                        f"[FactStore] {lanlan_name}: 回滚 FTS 索引失败 "
                        f"({fact_id})"
                    )

    async def aget_unabsorbed_facts(
        self,
        name: str,
        min_importance: int = 5,
        *,
        subject: MemorySubject | dict | None = None,
    ) -> list[dict]:
        facts = await self.aload_facts(name)
        # load_facts preserves legacy/hand-edited non-dict rows; filter them
        # here too or one corrupted row keeps raising through every caller
        # (scoped synthesis re-enters this getter after its own guard).
        return [
            f for f in facts
            if isinstance(f, dict)
            and f.get('id')
            and not f.get('absorbed')
            and not f.get('_near_dup_pending')
            # safe_importance: nonnumeric values in manually edited rows (e.g. "high") would
            # raise TypeError in native comparisons, killing this character's scoped synthesis every tick.
            and safe_importance(f, 0) >= min_importance
            and entry_matches_subject(f, subject)
        ]

    def get_facts_by_entity(self, name: str, entity: str) -> list[dict]:
        facts = self.load_facts(name)
        return [f for f in facts if f.get('entity') == entity]

    def mark_absorbed(self, name: str, fact_ids: list[str]) -> None:
        """Mark facts as absorbed by a reflection."""
        facts = self.load_facts(name)
        id_set = set(fact_ids)
        changed = False
        for f in facts:
            if f.get('id') in id_set and not f.get('absorbed'):
                f['absorbed'] = True
                changed = True
        if changed:
            self.save_facts(name)

    async def amark_absorbed(self, name: str, fact_ids: list[str]) -> None:
        await _facts_facade.asyncio.to_thread(self.mark_absorbed, name, fact_ids)

    def mark_signal_processed(self, name: str, fact_ids: list[str]) -> None:
        """Mark facts as having gone through Stage-2 signal detection.

        Mirrors `mark_absorbed`'s shape so the drain loop in
        `aextract_facts_and_detect_signals` can checkpoint a batch after
        the LLM call returns. Old on-disk facts that lack the field are
        treated as already processed (default=True) at read time, so
        re-flipping them here is a no-op.
        """
        facts = self.load_facts(name)
        id_set = set(fact_ids)
        changed = False
        for f in facts:
            if f.get('id') in id_set and not f.get('signal_processed', False):
                f['signal_processed'] = True
                changed = True
        if changed:
            self.save_facts(name)

    async def amark_signal_processed(self, name: str, fact_ids: list[str]) -> None:
        await _facts_facade.asyncio.to_thread(self.mark_signal_processed, name, fact_ids)

    def _recheck_mem(self) -> tuple[dict, threading.Lock]:
        """Return the recheck failure mirror and its guard, creating them once.

        Lazy rather than ``__init__``-only because several call sites build a
        ``FactStore`` through ``object.__new__``, which never runs ``__init__``;
        a plain instance attribute would make those instances raise
        ``AttributeError`` the moment the recheck path touches the mirror.
        Creation is serialised by a module-level lock — the thing that has to be
        protected here is precisely the window in which the instance has no lock
        of its own yet.
        """
        mem = self._recheck_attempts_mem
        guard = self._recheck_mem_guard
        if mem is not None and guard is not None:
            return mem, guard
        with _RECHECK_MEM_BOOTSTRAP:
            if self._recheck_attempts_mem is None:
                # Assign to instance, not class: mirrors must be per-instance, or become
                # singletons across instances / pytest cases.
                self._recheck_attempts_mem = {}
            if self._recheck_mem_guard is None:
                self._recheck_mem_guard = threading.Lock()
            return self._recheck_attempts_mem, self._recheck_mem_guard

    def _note_recheck_attempt(
        self, name: str, fid: str, *, base: dict | None,
    ) -> tuple[int, str]:
        """Record one recheck failure in the in-process mirror; returns (n, at).

        The mirror stores the failure timestamp as well as the count on
        purpose: ``cooldown_elapsed(None, …)`` returns True, so a count-only
        mirror would let the dead-letter self-heal branch re-admit the entry on
        the very next round — the breaker would look implemented and still
        never bite.

        On first entry for a fact the count resumes from the on-disk column so
        a restart does not reset the budget to zero.

        Concurrency contract: writers always replace an entry wholesale
        (``per_char[fid] = {...}``) instead of mutating its fields in place, so
        the lock-free reader in ``arecheck_one_legacy_fact`` can never observe a
        half-written entry. Keep it that way.

        No entry cap, unlike ``_abump_synth_backoff``'s 64: that map is keyed by
        content-derived synth keys (unbounded key space), this one by fact id
        (bounded by facts.json rows, which the archive sweep itself caps). A cap
        here would evict entries already frozen at MAX and un-freeze them.
        """
        stamp = datetime.now().isoformat()
        mem, guard = self._recheck_mem()
        with guard:
            per_char = mem.setdefault(name, {})
            prev = per_char.get(fid)
            if prev is not None:
                n = safe_int_field(prev, 'n') + 1
            else:
                n = (safe_int_field(base, 'recheck_attempts') if base else 0) + 1
            per_char[fid] = {"n": n, "at": stamp}
        return n, stamp

    def _clear_recheck_attempt(self, name: str, fid: str) -> None:
        """Drop a fact's failure record after it migrates successfully.

        Dual of ``_note_recheck_attempt``, mirroring
        ``ReflectionEngine._aclear_synth_backoff``: without it the mirror would
        only ever grow, and a migrated fact carrying a stale failure count is
        unexplainable state even though the schema filter already excludes it.
        """
        mem = self._recheck_attempts_mem
        if not mem:
            return
        _, guard = self._recheck_mem()
        with guard:
            mem.get(name, {}).pop(fid, None)

    def _recheck_budget_open(self, mem_entry: dict | None, fact: dict) -> bool:
        """Whether this legacy fact may still consume a recheck slot.

        The in-process mirror wins over the two on-disk columns. When
        facts.json cannot be written (read-only FS / permissions / maintenance
        mode) ``recheck_attempts`` stays 0 and ``last_recheck_attempt_at`` stays
        ``None`` on disk forever, and ``cooldown_elapsed(None, …)`` returns
        True — reading disk only would mean neither the breaker nor the
        self-heal gate ever bites, which is exactly the situation both were
        added for.

        ``safe_int_field`` rather than ``(… or 0)``: both columns come out of
        hand-editable JSON, and a dirty value like ``""`` / ``[]`` would raise
        inside the comparison and stall the whole drain loop.

        A mirror entry always carries both fields — ``_note_recheck_attempt``
        is its only writer and stamps ``at`` on every write — so this reads
        ``at`` straight, with no on-disk fallback that could never run.
        """
        from config import (
            MEMORY_RECHECK_MAX_ATTEMPTS,
            MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        )
        from memory.temporal import cooldown_elapsed

        if mem_entry is not None:
            attempts = safe_int_field(mem_entry, 'n')
            last_at = mem_entry.get('at')
        else:
            attempts = safe_int_field(fact, 'recheck_attempts')
            last_at = fact.get('last_recheck_attempt_at')
        if attempts < MEMORY_RECHECK_MAX_ATTEMPTS:
            return True
        # Timed self-healing: entries at retry cap get one probe after MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS,
        # recovering after transient disk/network failures resolve.
        return cooldown_elapsed(last_at, MEMORY_DEAD_LETTER_SELF_HEAL_SECONDS)

    def _bump_fact_recheck_attempts(self, name: str, fid: str, reason: str) -> None:
        """Increment the given fact's recheck failure count.

        Once failures reach the ``MEMORY_RECHECK_MAX_ATTEMPTS`` cap, the
        candidates filter excludes the fact so the loop gives its slot to other
        v1 entries.

        The count lands in the in-process mirror first, then best-effort on
        disk. The old version only wrote facts.json — the very file whose
        failure it was supposed to count — so in the read-only FS / permission
        case named in ``arecheck_one_legacy_fact``'s comment the counter never
        moved (``save_facts`` even evicts the cache on failure, discarding the
        just-incremented value), and the same fact was re-judged, at the cost of
        one LLM call, every 30s forever.

        A failed persist logs at WARNING — "the mirror moved but the disk
        counter did not" has no other visible signal in production — except
        under maintenance mode, an expected write ban that the archive block in
        ``save_facts`` also keeps at debug.
        """
        target: dict | None = None
        try:
            for f in self.load_facts(name):
                if isinstance(f, dict) and f.get('id') == fid:
                    target = f
                    break
        except Exception as e:
            _facts_facade.logger.debug(f"[Recheck-Fact] {name} {fid}: 读 facts 失败: {e}")
        if target is None and fid not in (self._recheck_attempts_mem or {}).get(name, {}):
            # Fact absent from store and no historical mirror count → no bookkeeping needed.
            return
        # 1) Update in-process mirror first (memory-only, cannot fail).
        attempts, stamp = self._note_recheck_attempt(name, fid, base=target)
        if target is not None:
            # 2) Best-effort persistence next. Failures WARN without raising; mirror already enforces circuit breaking,
            #    matching ReflectionEngine._asave_synth_backoff.
            target['recheck_attempts'] = attempts
            # Stamp failure time for dead-letter cooldown_elapsed self-healing.
            target['last_recheck_attempt_at'] = stamp
            try:
                self.save_facts(name)
            except MaintenanceModeError as e:
                # Maintenance is expected write prohibition (save_facts entry gate), not failure. Match
                # save_facts archive branch, which also downgrades maintenance WARNING to debug,
                # avoiding a persistence-failure warning on every failed recheck during maintenance.
                _facts_facade.logger.debug(
                    f"[Recheck-Fact] {name} {fid}: 维护态跳过 recheck_attempts "
                    f"落盘（进程内镜像仍生效）: {e}"
                )
            except Exception as e:
                _facts_facade.logger.warning(
                    f"[Recheck-Fact] {name} {fid}: recheck_attempts 落盘失败"
                    f"（进程内镜像仍生效，熔断不受影响）: {e}"
                )
        _facts_facade.logger.debug(
            f"[Recheck-Fact] {name} {fid}: recheck_attempts → {attempts} ({reason})"
        )

    async def arecheck_one_legacy_fact(self, name: str) -> bool:
        """Schema v1 → v2 slow recheck (processes only 1 fact per call).

        Finds the character's oldest fact with schema_version < CURRENT (main
        facts.json only, archive shards excluded), asks the LLM to fill in
        event_when, resolves event_start_at / event_end_at against created_at
        and writes them back. Facts have no temporal_scope, so this is lighter
        than the reflection recheck.

        Returns: True when one fact was processed successfully; False when no
        candidate was found or processing failed.
        """
        from memory import organizer
        if not organizer.allowed(name):
            return False
        from config.prompts.prompts_memory import MEMORY_RECHECK_FACT_PROMPT
        from memory.temporal import (
            normalize_event_when as _norm_when,
            compute_event_timestamps as _compute_ts,
        )

        facts = await self.aload_facts(name)
        # One lock-free read: writers replace entire entry dictionaries, avoiding partial states
        # (contract in _note_recheck_attempt).
        recheck_mem = (self._recheck_attempts_mem or {}).get(name, {})
        candidates = [
            f for f in facts
            if (f.get('schema_version') or 1) < MEMORY_SCHEMA_VERSION_CURRENT
            # Retry budget: continuously failing LLM entries stop blocking queue after reaching cap
            # (Codex review on PR #1316 P2, symmetric with reflections).
            and self._recheck_budget_open(
                recheck_mem.get(_readable_fact_id(f)), f,
            )
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda f: (f.get('created_at', ''), f.get('id', '')))
        # Skip malformed candidates (missing id / created_at) instead of
        # aborting the whole call — otherwise a single bad legacy entry at
        # head of FIFO order would starve every later v1 fact forever
        # (Codex review on PR #1316 P2 catch).
        target: dict | None = None
        fid = ''
        created_at_iso = ''
        for c in candidates:
            cid = c.get('id')
            cts = c.get('created_at', '')
            if not cid or not cts:
                _facts_facade.logger.debug(
                    f"[Recheck-Fact] {name}: skip malformed legacy fact "
                    f"(id={cid!r} created_at={cts!r})"
                )
                continue
            target = c
            fid = cid
            created_at_iso = cts
            break
        if target is None:
            return False

        prompt = MEMORY_RECHECK_FACT_PROMPT.format(
            FACT_TEXT=target.get('text', ''),
            CREATED_AT=created_at_iso,
        )

        failure_reason: str | None = None
        event_when_raw: dict | None = None
        try:
            from utils.llm_client import create_chat_llm_async
            set_call_type("memory_recheck_fact")
            api_config = await self._config_manager.aget_model_api_config('memory')
            from config import LLM_OUTPUT_GUARD_MAX_TOKENS
            # Disable thinking using factory-default extra_body: Stage-1 already annotates the same event_when
            # without thinking, despite harder input (whole conversations, multiple facts
            # simultaneously). This only annotates one legacy fact; thinking adds a failure path:
            # excessive reasoning → truncation/timeout → exhausted retries leave it v1 forever.
            llm = await create_chat_llm_async(
                api_config['model'],
                api_config['base_url'], api_config['api_key'],
                timeout=60, max_retries=0,
                max_completion_tokens=LLM_OUTPUT_GUARD_MAX_TOKENS,  # runaway guard; the answer is one small event_when JSON
                provider_type=api_config.get('provider_type'),
            )
            try:
                resp = await llm.ainvoke(prompt)  # noqa: LLM_INPUT_BUDGET  # recheck prompt assembled from token-capped memory components.
            finally:
                await llm.aclose()
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.replace("```json", "").replace("```", "").strip()
            result = robust_json_loads(raw)
            if not isinstance(result, dict):
                failure_reason = "non-dict response"
            else:
                event_when_raw = _norm_when(result.get('event_when'))
        except Exception as e:
            failure_reason = f"LLM call failed: {e}"

        # Unify failure handling: bump recheck_attempts; consecutive failures at MAX
        # are excluded by candidate filter (Codex review on PR #1316 P2, symmetric
        # with reflections).
        if failure_reason is not None:
            _facts_facade.logger.debug(
                f"[Recheck-Fact] {name} {fid}: 跳过本轮 ({failure_reason})"
            )
            await _facts_facade.asyncio.to_thread(self._bump_fact_recheck_attempts, name, fid, failure_reason)
            return False

        event_start_at, event_end_at = _compute_ts(
            event_when_raw,
            created_at_iso,
            fallback_start=True,
            fallback_end=False,
        )

        # Lock policy matches mark_absorbed / mark_signal_processed (around line 984):
        # mutate cached list returned by `load_facts` directly (CPython
        # field assignment is atomic), without outer _get_lock. save_facts (line 163)
        # acquires lock internally and read-merge-writes for concurrency safety.
        # No outer lock: _get_lock uses non-reentrant threading.Lock; acquiring it
        # before save_facts self-deadlocks (caught in Codex review on PR #1316).
        def _apply_update() -> bool:
            current = self.load_facts(name)
            found = None
            for f in current:
                if f.get('id') == fid:
                    found = f
                    break
            if found is None:
                return False
            if (found.get('schema_version') or 1) >= MEMORY_SCHEMA_VERSION_CURRENT:
                return False
            found['event_when_raw'] = event_when_raw
            found['event_start_at'] = event_start_at
            found['event_end_at'] = event_end_at
            found['schema_version'] = MEMORY_SCHEMA_VERSION_CURRENT
            self.save_facts(name)
            return True

        try:
            ok = await _facts_facade.asyncio.to_thread(_apply_update)
        except Exception as e:
            _facts_facade.logger.warning(f"[Recheck-Fact] {name} {fid}: save 失败: {e}")
            # Persistence failures count toward recheck_attempts too: sustained cloudsave maintenance / read-only FS /
            # permission failures would otherwise recheck the same unchanged fact every 30s forever,
            # never triggering circuit breaker (matching LLM-failure bump above).
            await _facts_facade.asyncio.to_thread(
                self._bump_fact_recheck_attempts, name, fid, f"save failed: {e}",
            )
            return False
        if ok:
            self._clear_recheck_attempt(name, fid)
            _facts_facade.logger.info(
                f"[Recheck-Fact] {name} {fid}: v1→v{MEMORY_SCHEMA_VERSION_CURRENT} "
                f"when={event_when_raw}"
            )
        return ok

    def _load_facts_locked(self, name: str) -> list[dict]:
        """Load facts for ``name`` assuming caller ALREADY holds ``self._get_lock(name)``."""
        from memory.long_term_sync import require_consistent_views, consistent_view_read
        require_consistent_views(os.path.join(self._config_manager.memory_dir, name))
        if name in self._facts:
            return self._facts[name]
        path = self._facts_path(name)
        try:
            with consistent_view_read(path):
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
        except FileNotFoundError:
            self._facts[name] = []
            return self._facts[name]
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, RecursionError) as e:
            _facts_facade.logger.warning(f"[FactStore] 加载 facts 文件失败: {e}")
            raise RuntimeError(f"Failed to load facts file {path}: {e}") from e
        if not isinstance(data, list):
            _facts_facade.logger.warning(
                f"[FactStore] facts 文件顶层非 list ({type(data).__name__}): {path}"
            )
            raise RuntimeError(
                f"Facts file {path} must contain a JSON list, got {type(data).__name__}"
            )
        data = self._without_cold_copies(name, data)
        if self._migrate_v1_entity_values(data):
            try:
                _facts_facade.assert_cloudsave_writable(
                    self._config_manager,
                    operation="migrate",
                    target=f"memory/{name}/facts.json",
                )
                _facts_facade.atomic_write_json(path, data, indent=2, ensure_ascii=False)
                _facts_facade.logger.info(f"[FactStore] {name}: v1→v2 entity 值迁移完成")
            except MaintenanceModeError as exc:
                _facts_facade.logger.debug(f"[FactStore] {name}: 维护态跳过 facts.json 迁移落盘: {exc}")
            except OSError as exc:
                _facts_facade.logger.warning(f"[FactStore] {name}: facts.json v1→v2 迁移落盘失败，暂用内存迁移结果: {exc}")
        self._facts[name] = data
        return data

    @staticmethod
    def _recompute_fact_hash(target: dict, cleaned_text: str) -> str:
        """Recompute exact-dedup hash preserving daily event_date and subject/scope salts."""
        ext = target.get("external_import")
        daily_date = (
            str(ext.get("event_date"))
            if isinstance(ext, dict) and ext.get("section") == "daily" and ext.get("event_date")
            else None
        )
        hash_input = f"{daily_date}\n{cleaned_text}" if daily_date else cleaned_text
        subj = subject_from_entry(target)
        if subj is not None:
            hash_input = f"{subj.key}\n{subj.scope}\n{hash_input}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:16]

    @staticmethod
    async def _run_shielded_async(coro_fn):
        """Run an async persist+index sequence shielded against repeated cancellations."""
        task = _facts_facade.asyncio.create_task(coro_fn())
        cancelled = False
        while not task.done():
            try:
                await _facts_facade.asyncio.shield(task)
            except _facts_facade.asyncio.CancelledError:
                cancelled = True
        if cancelled:
            if not task.cancelled():
                task.exception()
            raise _facts_facade.asyncio.CancelledError
        return task.result()

    async def aedit_fact_manual(self, name: str, fact_id: str, new_text: str) -> dict | None:
        """Manually edit one active fact's text, recompute dedup hash, and clear embedding cache."""
        from memory._embeddings.schema import clear_embedding_fields

        cleaned = str(new_text or "").strip()
        if not cleaned or len(cleaned) > 1000:
            raise ValueError("Fact text must be 1..1000 chars")

        def _edit() -> dict | None:
            with self._get_lock(name):
                _facts_facade.assert_cloudsave_writable(
                    self._config_manager,
                    operation="save",
                    target=f"memory/{name}/facts.json",
                )
                current = self._load_facts_locked(name)
                matches = [
                    fact for fact in current
                    if isinstance(fact, dict) and fact.get("id") == fact_id
                ]
                if not matches:
                    return None
                if len(matches) > 1:
                    raise ValueError("Ambiguous duplicate fact_id across scopes")
                target = matches[0]
                target["text"] = cleaned
                target["hash"] = self._recompute_fact_hash(target, cleaned)
                clear_embedding_fields(target)
                target.pop("embedding_model", None)
                target.pop("embedding_text_hash", None)
                self.save_facts(name, _fact_lock_held=True)
                return dict(target)

        async def _commit_and_index() -> dict | None:
            result = await _facts_facade.asyncio.to_thread(_edit)
            if result is not None and self._time_indexed is not None:
                try:
                    await self._time_indexed.aindex_fact(
                        name, fact_id, result["text"], replace=True,
                    )
                except Exception as exc:
                    _facts_facade.logger.warning(f"[FactStore] {name}: 刷新编辑后 FTS 索引失败 ({fact_id}): {exc}")
            return result

        async with self._get_persist_alock(name):
            return await self._run_shielded_async(_commit_and_index)

    async def aarchive_fact_manual(self, name: str, fact_id: str) -> bool:
        """Manually archive (forget) one active fact with recall-honored retirement markers."""
        def _archive() -> bool:
            with self._get_lock(name):
                _facts_facade.assert_cloudsave_writable(
                    self._config_manager,
                    operation="archive",
                    target=f"memory/{name}/facts_archive.json",
                )
                current = self._load_facts_locked(name)
                matches = [
                    fact for fact in current
                    if isinstance(fact, dict) and fact.get("id") == fact_id
                ]
                if not matches:
                    return False
                if len(matches) > 1:
                    raise ValueError("Ambiguous duplicate fact_id across scopes")
                target = matches[0]
                removed = dict(target)
                kept = [fact for fact in current if fact is not target]
                now_iso = datetime.now().isoformat()
                removed["archived_at"] = now_iso
                removed["arbitration_archived_at"] = now_iso
                removed["arbitration_reason"] = "manual_forget"
                removed["archive_reason"] = "manual_forget"
                archive_path = self._facts_archive_path(name)
                existing_archive: list[dict] = []
                if os.path.exists(archive_path):
                    with open(archive_path, encoding="utf-8") as fh:
                        loaded = json.load(fh)
                    if not isinstance(loaded, list):
                        raise ValueError(
                            f"facts_archive.json expected list, got {type(loaded).__name__}"
                        )
                    existing_archive = loaded
                merged_archive = _merge_archive_entries(existing_archive, [removed])
                _facts_facade.atomic_write_json(
                    archive_path, merged_archive, indent=2, ensure_ascii=False,
                )
                # Mutate cached list in-place to preserve list identity for concurrent holders.
                current[:] = kept
                self.save_facts(name, _fact_lock_held=True)
                return True

        async def _commit_and_delete_index() -> bool:
            archived = await _facts_facade.asyncio.to_thread(_archive)
            if archived and self._time_indexed is not None:
                try:
                    await self._time_indexed.adelete_fact_from_index(name, fact_id)
                except Exception as exc:
                    _facts_facade.logger.warning(f"[FactStore] {name}: 清理归档事实 FTS 索引失败 ({fact_id}): {exc}")
            return archived

        async with self._get_persist_alock(name):
            return await self._run_shielded_async(_commit_and_delete_index)

    async def amerge_facts_manual(
        self, name: str, keep_id: str, drop_id: str, merged_text: str,
    ) -> dict | None:
        """Atomically update ``keep_id`` with ``merged_text`` and archive ``drop_id`` under locks."""
        from memory._embeddings.schema import clear_embedding_fields

        cleaned = str(merged_text or "").strip()
        if not cleaned or len(cleaned) > 1000:
            raise ValueError("Fact text must be 1..1000 chars")
        if keep_id == drop_id:
            raise ValueError("keep_id and drop_id must differ")

        def _merge() -> dict | None:
            with self._get_lock(name):
                _facts_facade.assert_cloudsave_writable(
                    self._config_manager,
                    operation="archive",
                    target=f"memory/{name}/facts_archive.json",
                )
                current = self._load_facts_locked(name)
                keep_matches = [
                    fact for fact in current
                    if isinstance(fact, dict) and fact.get("id") == keep_id
                ]
                drop_matches = [
                    fact for fact in current
                    if isinstance(fact, dict) and fact.get("id") == drop_id
                ]
                if not keep_matches:
                    raise LookupError("keep_id not found")
                if not drop_matches:
                    raise LookupError("drop_id not found")
                if len(keep_matches) > 1 or len(drop_matches) > 1:
                    raise ValueError("Ambiguous duplicate fact_id across scopes")
                keep_obj = keep_matches[0]
                drop_obj = drop_matches[0]
                if (
                    str(keep_obj.get("entity") or "master") != str(drop_obj.get("entity") or "master")
                    or subject_from_entry(keep_obj) != subject_from_entry(drop_obj)
                    or str(keep_obj.get("scope") or "") != str(drop_obj.get("scope") or "")
                ):
                    raise ValueError("Cannot merge facts from different subjects or scopes")

                archive_path = self._facts_archive_path(name)
                orig_archive_bytes: bytes | None = None
                existing_archive: list[dict] = []
                if os.path.exists(archive_path):
                    with open(archive_path, "rb") as raw_fh:
                        orig_archive_bytes = raw_fh.read()
                    loaded = json.loads(orig_archive_bytes.decode("utf-8"))
                    if not isinstance(loaded, list):
                        raise ValueError(
                            f"facts_archive.json expected list, got {type(loaded).__name__}"
                        )
                    existing_archive = loaded

                now_iso = datetime.now().isoformat()
                removed = dict(drop_obj)
                removed["archived_at"] = now_iso
                removed["arbitration_archived_at"] = now_iso
                removed["arbitration_reason"] = "manual_merge"
                removed["archive_reason"] = "manual_merge"
                removed["merged_into"] = keep_id

                orig_current = [dict(f) if isinstance(f, dict) else f for f in current]
                merged_archive = _merge_archive_entries(existing_archive, [removed])
                try:
                    _facts_facade.atomic_write_json(
                        archive_path, merged_archive, indent=2, ensure_ascii=False,
                    )
                    keep_obj["text"] = cleaned
                    keep_obj["hash"] = self._recompute_fact_hash(keep_obj, cleaned)
                    clear_embedding_fields(keep_obj)
                    keep_obj.pop("embedding_model", None)
                    keep_obj.pop("embedding_text_hash", None)

                    kept = [f for f in current if f is not drop_obj]
                    current[:] = kept
                    self.save_facts(name, _fact_lock_held=True)
                except Exception:
                    current[:] = orig_current
                    try:
                        if orig_archive_bytes is not None:
                            from utils.file_utils import atomic_write_bytes
                            atomic_write_bytes(archive_path, orig_archive_bytes)
                        elif os.path.exists(archive_path):
                            os.remove(archive_path)
                    except OSError as rb_exc:
                        _facts_facade.logger.warning(f"[FactStore] {name}: 合并回滚 archive 失败: {rb_exc}")
                    raise
                return dict(keep_obj)

        async def _commit_and_update_index() -> dict | None:
            kept_fact = await _facts_facade.asyncio.to_thread(_merge)
            if kept_fact is not None and self._time_indexed is not None:
                try:
                    await self._time_indexed.aindex_fact(
                        name, keep_id, kept_fact["text"], replace=True,
                    )
                    await self._time_indexed.adelete_fact_from_index(name, drop_id)
                except Exception as exc:
                    _facts_facade.logger.warning(f"[FactStore] {name}: 合并事实后刷新 FTS 索引失败: {exc}")
            return kept_fact

        async with self._get_persist_alock(name):
            return await self._run_shielded_async(_commit_and_update_index)
