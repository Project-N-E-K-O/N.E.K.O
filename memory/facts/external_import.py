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
"""External daily markdown import and AI-aware known-pool extraction methods."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import datetime
from config import EVIDENCE_EXTRACT_FACTS_MODEL_TIER, EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY
from memory.scopes import MemorySubject, coerce_subject
from utils.cloudsave_runtime import MaintenanceModeError

from memory import facts as _facts_facade
from ._shared import (
    EXTRACTION_PATH_B,
    _detect_fact_extraction_prompt_language,
)

class ExternalImportMixin:
    def _external_import_state_path(self, name: str) -> str:
        from memory import ensure_character_dir
        return os.path.join(
            ensure_character_dir(self._config_manager.memory_dir, name),
            'external_import_state.json',
        )

    def _load_external_import_state(self, name: str) -> dict:
        """Best-effort read of the per-character external-import sidecar.

        Missing / corrupt / non-dict payloads degrade to ``{}``: the worst
        case is re-extracting already-imported days (wasted LLM calls), never
        data loss, so this read must not fail an import.
        """
        path = self._external_import_state_path(name)
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
                # UnicodeDecodeError (invalid UTF-8, ValueError subclass, not
                # JSONDecodeError/OSError) must also degrade: _acollect_day_fp_sources runs
                # before per-day isolation, so one corrupt sidecar would abort entire import,
                # violating docstring's empty-set fallback promise (Codex P2).
                _facts_facade.logger.warning(
                    f"[FactStore] {name}: 读取 external_import_state 失败，降级为空: {e}"
                )
        return {}

    @staticmethod
    def _state_daily_fingerprints(state: dict) -> set[str]:
        """Processed-day fingerprint set held by the sidecar state dict."""
        daily = state.get('daily')
        fps = daily.get('imported_day_fingerprints') if isinstance(daily, dict) else None
        if not isinstance(fps, list):
            return set()
        return {str(x) for x in fps if x}

    @staticmethod
    def _facts_have_day_fingerprint(facts: list[dict], fingerprint: str) -> bool:
        """Whether any fact's ``external_import`` provenance carries this day's
        fingerprint — i.e. the day IS carried by a fact and must NOT get a
        sidecar entry.

        Covers both a newly appended fact and an existing fact whose provenance
        was upgraded in place (e.g. ai_disclosure→user_observation on a same-day
        exact-hash hit): both stamp ``day_fingerprint``, both ride facts.json's
        rollback lifecycle and self-heal, so a sidecar entry for such a day
        would outlive a rollback and suppress the re-extraction (Codex P2)."""
        for f in facts:
            meta = f.get('external_import')
            if isinstance(meta, dict) and str(meta.get('day_fingerprint') or '') == fingerprint:
                return True
        return False

    async def _acollect_day_fp_sources(self, name: str) -> tuple[set[str], set[str]]:
        """The two idempotency carriers, returned separately as
        ``(sidecar_fps, provenance_fps)``.

        provenance_fps scans active + archive (``aload_facts_full``) so a day
        whose facts were archived still counts as carried. Callers union the two
        for the skip filter, and diff them (``sidecar ∩ provenance``) to detect
        stale sidecar entries a fact now carries — see the up-front self-heal in
        ``aimport_external_daily``."""
        state = await _facts_facade.asyncio.to_thread(self._load_external_import_state, name)
        sidecar_fps = self._state_daily_fingerprints(state)
        provenance_fps: set[str] = set()
        for f in await self.aload_facts_full(name, include_cold_stubs=True):
            meta = f.get('external_import')
            if isinstance(meta, dict) and meta.get('day_fingerprint'):
                provenance_fps.add(str(meta['day_fingerprint']))
        return sidecar_fps, provenance_fps

    async def _aload_imported_day_fps(self, name: str) -> set[str]:
        """Union of processed-day fingerprints from two carriers, split by
        whether the day persisted any fact:

        - **Fact provenance** (``external_import.day_fingerprint`` inside
          facts.json / facts_archive.json) is authoritative for days that
          persisted at least one fact. It rides the fact data's own cloudsave
          sync / restore / rollback lifecycle, so a rolled-back day loses its
          fingerprint together with its facts and re-imports self-heal —
          exactly #2383's behavior. Days-with-facts therefore do **not** get a
          sidecar entry (see ``_arecord_unpersisted_day_fp``): a sidecar record
          that outlived a facts.json rollback would permanently suppress the
          re-extraction that restores the day.
        - **Sidecar** (``external_import_state.json``) carries **only** days the
          LLM judged to hold no fact at all (empty extraction). A day whose
          extracted facts were *all deduped away* is NOT recorded here — its
          content is already carried by the deduping facts (under a different
          fingerprint, so a rollback of those facts couldn't self-heal a sidecar
          entry), so it re-extracts instead (added=0, no data lost — chosen over
          a fact-carried multi-fingerprint scheme). Re-extracting a truly
          fact-less day is harmless, so a stale / desynced sidecar never loses
          data (Codex P2 follow-up).

        Provenance is read over active + archive (``aload_facts_full``) so a day
        whose facts were archived by ``_archive_absorbed`` (>7 days old) still
        skips instead of re-extracting. The production skip filter computes this
        same union itself from ``_acollect_day_fp_sources`` (it also needs the
        ``sidecar ∩ provenance`` intersection for the up-front self-heal), so
        this wrapper is the read-side contract exercised by the sidecar
        degradation tests. The persist-time concurrent re-check reads active
        provenance directly (a rival import that just persisted writes the
        active list, and a sidecar-only rival must not suppress this request's
        real facts).
        """
        sidecar_fps, provenance_fps = await self._acollect_day_fp_sources(name)
        return sidecar_fps | provenance_fps

    async def _arecord_unpersisted_day_fp(self, name: str, fingerprint: str) -> None:
        """Best-effort: record a day's fingerprint in the sidecar **iff no fact
        carries it**.

        Called only for days the LLM judged fact-less (empty extraction); a day
        whose extracted facts all deduped away re-extracts instead of being
        sidecar-recorded (see ``_aload_imported_day_fps``). The
        active-provenance re-check runs **inside** the persist alock (the same
        lock as fact persistence): a concurrent same-character import that
        persists this day's facts either lands before us — we see its provenance
        and skip the sidecar — or after us, blocked on the lock. So a day that
        ends up with a fact carrier never also gets a sidecar entry (which would
        outlive a facts.json rollback and suppress the self-healing
        re-extraction, Codex P2). No TOCTOU; the same critical section also
        absorbs the sidecar's own read-modify-write.

        Best-effort by design: the sidecar is a pure idempotency accelerator.
        A write failure (disk full / permission; the cloudsave fence is already
        rejected at the import entrypoint) degrades to re-extracting this day on
        the next import — the exact #2383-era cost — so it must never escalate
        a day that otherwise succeeded into a failed_day / HTTP 500. Fact
        persistence keeps its own hard ``assert_cloudsave_writable`` semantics;
        only this accelerator layer is soft.
        """
        try:
            async with self._get_persist_alock(name):
                active = await self.aload_facts(name)
                if self._facts_have_day_fingerprint(active, fingerprint):
                    return
                await _facts_facade.asyncio.to_thread(
                    self._record_imported_day_fp_locked, name, fingerprint
                )
        except (MaintenanceModeError, OSError) as exc:
            _facts_facade.logger.warning(
                f"[FactStore] {name}: sidecar 落盘失败（降级为下次重导重抽该天）: {exc}"
            )

    def _record_imported_day_fp_locked(self, name: str, fingerprint: str) -> None:
        _facts_facade.assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/external_import_state.json",
        )
        state = self._load_external_import_state(name)
        fps = self._state_daily_fingerprints(state)
        if fingerprint in fps:
            return
        fps.add(fingerprint)
        state.setdefault('version', 1)
        daily = state.get('daily')
        if not isinstance(daily, dict):
            daily = {}
            state['daily'] = daily
        # Symmetric with persona folded_fingerprints: set semantics, sorted persistence for stable diffs.
        daily['imported_day_fingerprints'] = sorted(fps)
        _facts_facade.atomic_write_json(
            self._external_import_state_path(name), state,
            indent=2, ensure_ascii=False,
        )

    async def _aclear_day_fps(self, name: str, fingerprints: set[str]) -> None:
        """Best-effort: drop day fingerprints from the sidecar because a fact now
        carries them, or the day re-extracts — either way they must not linger as
        stale sidecar entries that a facts rollback couldn't self-heal (Codex P2).

        Fires the write only when the sidecar actually holds one of them. Used
        both for the up-front self-heal (``sidecar ∩ provenance``) and for the
        per-day clear after a day yields facts. Same per-character alock +
        best-effort contract as ``_arecord_unpersisted_day_fp``."""
        if not fingerprints:
            return
        try:
            async with self._get_persist_alock(name):
                await _facts_facade.asyncio.to_thread(self._clear_day_fps_locked, name, fingerprints)
        except (MaintenanceModeError, OSError) as exc:
            _facts_facade.logger.warning(
                f"[FactStore] {name}: sidecar 清理陈旧指纹失败（无害，下次导入再清）: {exc}"
            )

    def _clear_day_fps_locked(self, name: str, fingerprints: set[str]) -> None:
        path = self._external_import_state_path(name)
        if not os.path.exists(path):
            return  # Missing sidecar → one stat only, no disk content reads.
        state = self._load_external_import_state(name)
        fps = self._state_daily_fingerprints(state)
        to_drop = fps & fingerprints
        if not to_drop:
            return
        _facts_facade.assert_cloudsave_writable(
            self._config_manager,
            operation="save",
            target=f"memory/{name}/external_import_state.json",
        )
        fps -= to_drop
        daily = state.get('daily')
        if not isinstance(daily, dict):
            return
        daily['imported_day_fingerprints'] = sorted(fps)
        _facts_facade.atomic_write_json(
            self._external_import_state_path(name), state,
            indent=2, ensure_ascii=False,
        )

    async def aimport_external_daily(
        self, lanlan_name: str, candidates: list[dict], source_format: str,
        imported_at: str,
        on_progress: Callable[[dict], None] | None = None,
    ) -> dict:
        """LLM-extract facts from imported daily journals (Stage-1, no signals).

        Mirrors the persona side (``afuse_external_facts``): external daily files
        (``memory/`` or ``memories/YYYY-MM-DD.md``) are free-form journal prose,
        so rather than appending their raw fragments verbatim they are run through
        the conversation fact-extraction LLM. Candidates are grouped by source
        file (one file == one day); each day's fragments are joined into a single
        user turn and extracted independently so the day's ``event_date`` can be
        stamped onto every fact it yields. A day whose joined fragments exceed
        ``EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS`` — or the per-batch fragment
        cap in ``batch_daily_fragments`` (dense days otherwise overflow the
        4096 output guard in a single call) — is split into multiple
        extraction batches (``batch_daily_fragments``) rather than truncated —
        no journal tail is silently dropped (Greptile P1). Days run concurrently
        under ``EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY`` (a month of journals run
        sequentially would blow past the upstream 240s forwarding window);
        batches within a day run sequentially; persistence stays serialized by
        the per-character persist lock. Best-effort per day, atomic within a
        day: when any batch fails (None) or crashes the whole day persists
        nothing — no facts, no fingerprint — and is counted in ``failed_days``
        so the caller surfaces a retryable partial result; the retry re-extracts
        that day from scratch (a partially-persisted day would fingerprint-skip
        forever, Greptile P1).

        Idempotency mirrors persona ``folded_fingerprints`` via each day's
        content fingerprint, held by the fact carrier when there is one and by
        the sidecar only when there is none: days that persisted (or upgraded in
        place) a fact carry it in their ``external_import`` provenance (inside
        facts.json, so it shares the fact data's cloudsave lifecycle and
        self-heals on rollback); days the LLM judged fact-less (empty
        extraction) record it in the ``external_import_state.json`` sidecar,
        which would otherwise have no home and re-run the LLM + burn cap quota on
        every re-import (Codex P2 follow-up). A day whose extracted facts all
        deduped away is left to re-extract (its content is already carried by
        the deduping facts under a different fingerprint; recording a sidecar
        entry that a facts rollback couldn't self-heal was rejected). The read
        side unions provenance + sidecar (see ``_aload_imported_day_fps``). A
        re-imported day whose fragments are unchanged is skipped outright — zero
        LLM calls (Codex P2). Changed content re-extracts; near-identical
        re-extraction output is absorbed by the same-date FTS5 dedup in
        ``_apersist_new_facts``.

        After fingerprint filtering, the genuinely-new days are capped at
        ``EXTERNAL_IMPORT_DAILY_MAX_FILES`` extraction calls per import (an
        unbounded workspace would mean hundreds of LLM calls and blow the
        forwarding window even under bounded concurrency, Codex P2). Over the
        cap, the oldest days that fit are imported and the rest are deferred
        (``deferred_days``): the caller reports a retryable partial result, and
        each re-import skips the finished days by fingerprint and continues
        with the next ones, so any history converges in a few imports. Files
        never imported before go first, then previously imported files whose
        content changed (their facts often all dedupe and leave no fingerprint,
        so putting them first would re-select them forever). A day that alone
        needs more calls than the cap can never run; it is set aside
        (``oversized_days``) instead of blocking later days, and only when
        nothing else is left does ``ExternalMemoryImportTooLargeError`` raise.

        Returns ``{'added': int, 'days': int, 'failed_days': int,
        'skipped_days': int, 'deferred_days': int, 'oversized_days': int}``.
        """
        from memory.external_markdown_import import batch_daily_fragments
        from memory.persona.fusion import ExternalMemoryImportTooLargeError
        from utils.llm_client import convert_to_messages

        by_file: dict[str, list[dict]] = {}
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            by_file.setdefault(str(cand.get("source_file") or ""), []).append(cand)

        # Daily fingerprint idempotency (symmetric with persona folded_fingerprints): unchanged already imported
        # days skip LLM. Two carriers: days with facts use provenance (including archives), days without
        # facts use sidecars.
        sidecar_fps, provenance_fps = await self._acollect_day_fp_sources(lanlan_name)
        # Self-heal before skipping: sidecar fingerprints also present in fact provenance are stale (day already has
        # a fact carrier, so sidecar unnecessary); clear them. Covers exact-hash upgrade residue, concurrent
        # sidecar-only residue, and failed immediate cleanup/process interruption after successful days; that post-persist
        # cleanup would otherwise be bypassed forever by skips here (CodeRabbit + Codex).
        stale_sidecar = sidecar_fps & provenance_fps
        if stale_sidecar:
            await self._aclear_day_fps(lanlan_name, stale_sidecar)
        imported_day_fps = sidecar_fps | provenance_fps

        day_dates = {
            source_file: next(
                (str(g["event_date"]) for g in group if g.get("event_date")), None
            )
            for source_file, group in by_file.items()
        }
        # Include event_date in fingerprints: identical routine diary text on different dates represents distinct
        # days, which must not skip one another (Codex P2), just as fact deduplication keys include dates.
        day_fps = {
            source_file: self._daily_fingerprint(
                [str(g.get("text") or "") for g in group],
                event_date=day_dates[source_file],
            )
            for source_file, group in by_file.items()
        }
        pending = {
            source_file: group
            for source_file, group in by_file.items()
            if day_fps[source_file] not in imported_day_fps
        }
        skipped_days = len(by_file) - len(pending)

        # Precompute batches and cap total extraction calls, not days: one huge diary file can split into
        # dozens of serial calls, with len(pending) failing to prevent the forwarding wall (Codex P2). tiktoken
        # encoding is synchronous CPU work; offload to thread pool. Batches keep fragment groups (not joined
        # text) so a failed batch can be split in half adaptively without re-parsing the joined string.
        groups_by_file: dict[str, list[list[str]]] = await _facts_facade.asyncio.to_thread(
            lambda: {
                source_file: batch_daily_fragments(
                    [p for p in (str(g.get("text") or "").strip() for g in group) if p],
                    _facts_facade.EXTERNAL_IMPORT_DAILY_INPUT_MAX_TOKENS,
                )
                for source_file, group in pending.items()
            }
        )
        total_batches = sum(len(b) for b in groups_by_file.values())
        deferred_days = 0
        oversized_days = 0
        if total_batches > _facts_facade.EXTERNAL_IMPORT_DAILY_MAX_FILES:
            # Segmented resume: over the per-import call cap, the days that fit run now and the rest are deferred to the next
            # import. Finished days skip by daily fingerprint, so every re-import moves further ahead and
            # any number of days converges within a few imports, as long as each day fits one import (Molili archives cannot be split; rejecting the whole archive would
            # leave long-time users unable to import ever).
            # - Days that alone exceed the cap are set aside (oversized): retrying cannot progress them, but they must not
            #   block later dates; only when nothing else is left are they rejected as too_large.
            # - Never-imported files go first, then files imported before whose content changed, each from
            #   earliest to latest: facts extracted from edited old diaries often all dedupe against existing facts,
            #   such days record no completion marker (see _aload_imported_day_fps, kept for rollback self-healing),
            #   and if they came first they would be re-selected every time and fill the budget, starving the genuinely new dates.
            # Days just tried in this process whose facts all deduped go last: otherwise they are re-selected every time and
            # fill the budget, so deferred days never get their turn (e.g. the same history was already imported from another source).
            recently_deduped = self._external_daily_deduped_fps.get(lanlan_name, set())
            previously_imported_files = {
                str(meta.get('file'))
                for f in await self.aload_facts_full(lanlan_name, include_cold_stubs=True)
                if isinstance(meta := f.get('external_import'), dict) and meta.get('file')
            }
            ordered = sorted(
                groups_by_file,
                key=lambda f: (
                    day_fps[f] in recently_deduped,
                    f in previously_imported_files,
                    day_dates.get(f) or "",
                    f,
                ),
            )
            oversized = [
                f for f in ordered if len(groups_by_file[f]) > _facts_facade.EXTERNAL_IMPORT_DAILY_MAX_FILES
            ]
            oversized_set = set(oversized)
            selectable = [f for f in ordered if f not in oversized_set]
            if not selectable:
                raise ExternalMemoryImportTooLargeError(
                    f"daily journal {oversized[0]} alone needs "
                    f"{len(groups_by_file[oversized[0]])} extraction calls "
                    f"(cap {_facts_facade.EXTERNAL_IMPORT_DAILY_MAX_FILES}); split that day"
                )
            if all(day_fps[f] in recently_deduped for f in selectable):
                # Every remaining day already ran in this process and all of its facts
                # deduped against existing ones, so re-running them cannot add anything and
                # (having no completion fingerprint) they would be re-selected forever. Count
                # them as skipped and let this import finish. The hint is in-memory only:
                # a reload / restart clears it and the next import tries them again.
                _facts_facade.logger.info(
                    f"[FactStore] {lanlan_name}: {len(selectable)} remaining daily journal(s) "
                    "already deduped entirely in this process; finishing the import"
                )
                skipped_days += len(selectable)
                selectable = []
            selected: list[str] = []
            used_calls = 0
            for source_file in selectable:
                calls = len(groups_by_file[source_file])
                if used_calls + calls > _facts_facade.EXTERNAL_IMPORT_DAILY_MAX_FILES:
                    break
                selected.append(source_file)
                used_calls += calls
            deferred_days = len(selectable) - len(selected)
            oversized_days = len(oversized)
            _facts_facade.logger.info(
                f"[FactStore] {lanlan_name}: 外部 daily 导入超过单次 "
                f"{_facts_facade.EXTERNAL_IMPORT_DAILY_MAX_FILES} 次调用上限，本次先导 "
                f"{len(selected)} 天（{used_calls} 次调用），{deferred_days} 天顺延到下次导入，"
                f"{oversized_days} 天单独超上限"
            )
            pending = {source_file: pending[source_file] for source_file in selected}
            groups_by_file = {source_file: groups_by_file[source_file] for source_file in selected}
            total_batches = used_calls
        # Progress reporting (for the external import progress bar): report the total first, then +1 per finished batch
        if on_progress is not None:
            on_progress({'daily_total': total_batches, 'daily_done': 0})

        daily_progress = {'done': 0}
        llm_slots = _facts_facade.asyncio.Semaphore(EXTERNAL_IMPORT_DAILY_MAX_CONCURRENCY)
        # Extra call budget for split-in-half recursion: the cap above only counts the pre-split batches, and each split of a failed batch
        # adds 2 calls. The budget matches the cap (total calls <= 2x cap); once spent, failed batches
        # fail the day directly (day-atomic, retryable) instead of recursing unboundedly into the forwarding timeout.
        split_budget = {'left': _facts_facade.EXTERNAL_IMPORT_DAILY_MAX_FILES}

        # Free-tier courtesy interval: the minimum gap between any two extraction call starts (across days / batches, and across
        # concurrent imports of several characters, since the lock and timestamp live on the shared FactStore), turning
        # concurrency bursts into a steady pace. Concurrency slots bound how many calls are in flight; this interval bounds the
        # start rate; together they actually spare free quotas / WAFs. The lock only covers the time arithmetic and
        # sleep, never the LLM call itself (so it does not extend concurrency slot residency).
        call_gate = self._external_daily_call_gate

        async def _polite_gap() -> None:
            if _facts_facade.EXTERNAL_IMPORT_DAILY_CALL_INTERVAL <= 0:
                return
            async with call_gate:
                now = _facts_facade.asyncio.get_running_loop().time()
                wait = call_gate.last_call_at + _facts_facade.EXTERNAL_IMPORT_DAILY_CALL_INTERVAL - now
                if wait > 0:
                    await _facts_facade.asyncio.sleep(wait)
                call_gate.last_call_at = _facts_facade.asyncio.get_running_loop().time()

        async def _extract_one_day(source_file: str, group: list[dict]) -> tuple[int, bool]:
            """One day's extraction+persist; returns (added, day_failed)."""
            event_date = day_dates[source_file]

            async def _extract_group(fragments: list[str], depth: int) -> list[dict] | None:
                """One batch's extraction; ``None`` = day-terminal failure.

                The dominant failure shape is the batch's output JSON hitting
                the 4096 output guard mid-JSON -- blind retries of the same
                batch re-truncate. Split in half and retry each half instead:
                halves halve the output size and dense days converge under the
                guard; a single fragment that still fails is a real failure.
                Day-atomic semantics unchanged -- any leaf failure propagates
                up, nothing persists, no fingerprint residue."""
                batch_text = "\n".join(fragments)
                messages = convert_to_messages(
                    [{"role": "user", "content": batch_text}]
                )
                # Only LLM round trips occupy concurrency slots; _apersist_new_facts serializes disk writes under
                # per-character locks. Keep writes outside slots so other days' LLM calls start sooner.
                # Malformed non-arrays are retryable failed days, never empty-extraction sidecar checkpoints,
                # which would skip later LLM calls and silently lose that day's facts (Codex P2).
                # Arrays without object elements, or mixing elements persistence would drop, are equally
                # strict (split fallback): _apersist_new_facts_locked silently drops non-dicts and dicts
                # without a valid text, so accepting the batch and recording its fingerprint would make
                # re-imports skip the dropped content forever (wehos 14 / coderabbit).
                def _persistable_element(element: object) -> bool:
                    # Must mirror _apersist_new_facts_locked's drop conditions one-to-one (not
                    # _as_fact_entry, which promotes bare strings; this path does not).
                    if not isinstance(element, dict):
                        return False
                    text = element.get('text')
                    return isinstance(text, str) and bool(text.strip())

                failure: dict = {}
                async with llm_slots:
                    extracted = await self._allm_extract_facts(
                        lanlan_name, messages, treat_malformed_as_failure=True,
                        failure_info=failure,
                        # The courtesy interval runs before every actual request (including internal network retries): under rate limiting
                        # retries must queue too instead of starting alongside other batches.
                        before_attempt=_polite_gap,
                        # Truncation-type failures go to _extract_group's split-in-half recursion (each half is itself
                        # a subset retry); blindly retrying the same batch 3 times is pure waste, so split after 1 parse failure.
                        # Parse failures only: network errors / 429 / 5xx still retry with full backoff,
                        # since a single-fragment batch cannot be split and one hiccup must not drop the whole day.
                        parse_max_retries=1,
                    )
                has_malformed_elements = (
                    extracted is not None and any(not _persistable_element(f) for f in extracted)
                )
                if extracted is None or has_malformed_elements:
                    # Only output-type failures (parse failure / wrong shape) might be fixed by splitting; network /
                    # 429 / 5xx failures after full backoff would only burn more quota and time if split.
                    output_failure = extracted is not None or failure.get("reason") in {"parse", "schema"}
                    if output_failure and len(fragments) >= 2 and depth < 6 and split_budget['left'] >= 2:
                        split_budget['left'] -= 2
                        mid = len(fragments) // 2
                        _facts_facade.logger.warning(
                            f"[FactStore] {lanlan_name}: 外部 daily 抽取批次失败，"
                            f"拆半重试（{len(fragments)} 片段 → {mid}+{len(fragments) - mid}）"
                        )
                        left = await _extract_group(fragments[:mid], depth + 1)
                        if left is None:
                            return None
                        right = await _extract_group(fragments[mid:], depth + 1)
                        if right is None:
                            return None
                        return left + right
                    _facts_facade.logger.warning(
                        f"[FactStore] {lanlan_name}: 外部 daily 抽取 LLM 失败，"
                        f"放弃 {source_file}（整天重试重抽）"
                    )
                    return None
                return [f for f in extracted if isinstance(f, dict)]

            # Extract all batches for a day before persistence; any failed batch prevents persisting the entire day.
            # Persisting early batches with full-day fingerprints before later failure makes retries skip the whole day,
            # permanently losing failed content (Greptile P1). Atomic days leave no facts/fingerprints on failure, retrying
            # from scratch; gather likewise counts persistence crashes as failed_days without fingerprint residue.
            # Failed batches no longer retry blindly; they split in half recursively (see _extract_group).
            day_extracted: list[dict] = []
            for fragments in groups_by_file[source_file]:
                batch_facts = await _extract_group(fragments, 0)
                if batch_facts is None:
                    return 0, True
                day_extracted.extend(batch_facts)
                daily_progress['done'] += 1
                if on_progress is not None:
                    on_progress({'daily_done': daily_progress['done']})
            if not day_extracted:
                # Empty-extraction days: LLM found no facts, leaving sidecar as sole fingerprint carrier;
                # otherwise every reimport re-extracts the day and consumes cap allowance (Codex P2 follow-up).
                # _arecord rechecks under lock that active facts lack that day's provenance before writing
                # (covering concurrent real fact persistence). Best-effort write failure falls back to re-extraction, no escalation.
                # failed_day。
                await self._arecord_unpersisted_day_fp(lanlan_name, day_fps[source_file])
                return 0, False
            # Narrow concurrency race by rechecking: two same-character commits may both read imported_day_fps
            # initially then call LLM. Before persistence reread; if another already persisted actual facts,
            # abandon this write (differently worded facts bypass exact deduplication). Remaining tiny
            # TOCTOU window uses same-date FTS5 near-dedup fallback; frontend single-flight lock already prevents
            # concurrent imports from one client (Codex P2).
            # Check active fact provenance only, never sidecars: this request extracted nonempty
            # facts, which concurrent sidecar-only results (empty/all-dedup, no fact carrier)
            # must not displace; otherwise fact-free sidecars suppress real extracted facts (Codex).
            active_now = await self.aload_facts(lanlan_name)
            if self._facts_have_day_fingerprint(active_now, day_fps[source_file]):
                _facts_facade.logger.info(
                    f"[FactStore] {lanlan_name}: {source_file} 已被并发导入落盘，"
                    "放弃本次写入"
                )
                return 0, False
            for fact in day_extracted:
                # Stamp provenance; _apersist_new_facts_locked turns event_date
                # into event_start_at and tags the entry as external_import.
                # day_fingerprint supplies reimport idempotency (see docstring).
                fact["_external_import"] = {
                    "format": source_format,
                    "file": source_file,
                    "section": "daily",
                    "event_date": event_date,
                    "imported_at": imported_at,
                    "day_fingerprint": day_fps[source_file],
                }
            try:
                new_facts = await self._apersist_new_facts(
                    lanlan_name, day_extracted, semantic_dedup=True,
                )
            except Exception:
                # Clear this day's sidecar on persistence failure too (FTS/JSON write errors, etc.): real facts were extracted,
                # so day has content; if concurrent empty extraction wrote sidecar first, failed persistence leaves it
                # the sole carrier, suppressing retries of unchanged diaries forever (Codex). Narrows rather than eliminates:
                # cannot cover empty-result sidecars persisted after this cleanup; failed-day markers lack persistence
                # and cross-request fences, matching the accepted possibility of later imports legitimately
                # checkpointing when LLM returns empty. gather counts failed_days; retries re-extract from scratch.
                await self._aclear_day_fps(lanlan_name, {day_fps[source_file]})
                raise
            # Days with extracted facts (new persistence or all deduplicated) clear possible residual
            # sidecar fingerprints from concurrent empty extraction; these days do not belong in sidecars:
            #  - Success/upgrade: fingerprints live in fact provenance, in the same rollback unit;
            #    rollback removes both, reimport self-heals; leftover sidecars block extraction and lose memory.
            #  - All deduplicated: no sidecar; repeat extraction self-heals rollback (reappend rolled-back facts).
            #    A concurrent empty-extraction sidecar would defeat that recovery.
            # Most days lack this sidecar fingerprint, returning after one stat (Codex).
            await self._aclear_day_fps(lanlan_name, {day_fps[source_file]})
            if not new_facts:
                # Facts were extracted but all deduped: record no completion marker (see above), only the in-process ordering hint
                self._external_daily_deduped_fps.setdefault(lanlan_name, set()).add(
                    day_fps[source_file]
                )
            return len(new_facts), False

        outcomes = await _facts_facade.asyncio.gather(
            *(_extract_one_day(f, g) for f, g in pending.items()),
            return_exceptions=True,
        )
        added = 0
        failed_days = 0
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                failed_days += 1
                _facts_facade.logger.error(
                    f"[FactStore] {lanlan_name}: 外部 daily 抽取单日崩溃，已跳过该日",
                    exc_info=outcome,
                )
                continue
            day_added, day_failed = outcome
            added += day_added
            if day_failed:
                failed_days += 1
        return {
            "added": added,
            "days": len(by_file),
            "failed_days": failed_days,
            "skipped_days": skipped_days,
            "deferred_days": deferred_days,
            "oversized_days": oversized_days,
        }

    @staticmethod
    def _daily_fingerprint(texts: list[str], *, event_date: str | None = None) -> str:
        """Whitespace/case-normalized, **order-preserving** fingerprint over one
        day's fragment texts, salted with the day's ``event_date``. Journals are
        narrative — reordering entries (e.g. "stopped medication" vs "started
        medication" swapped) changes meaning, so an edited order must re-extract
        instead of fingerprint-skipping (Greptile P1); and a routine journal
        repeated verbatim on a different date is a **new** day, not a duplicate
        (Codex P2). persona's ``_fusion_fingerprint`` stays sorted and unsalted
        by design: its candidates are an unordered, date-less set."""
        norm = [" ".join((t or "").casefold().split()) for t in texts]
        payload = f"{event_date or ''}\n" + "\n".join(norm)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def aextract_facts_with_known_pool(
        self,
        lanlan_name: str,
        messages: list,
        known_pool: list[dict],
        *,
        subject: MemorySubject | dict | None = None,
        conversation_at: datetime | None = None,
    ) -> list[dict] | None:
        """AI-aware Stage-1 (path B) extraction — input is the role-tagged full
        user+ai message set; the prompt embeds ``known_pool`` (facts path A
        already extracted in the same window) as a "do-not-repeat" list so the
        LLM dedupes proactively at the output layer.

        Differences from ``extract_facts``:
        - Uses the new prompt (``FACT_EXTRACTION_AI_AWARE_PROMPT``) instead of the basic one
        - The prompt adds a known-pool section + trust-tier guidance + a source-field output requirement
        - Persists with default_source='ai_disclosure' (a source explicitly emitted by the LLM still wins)
        - No Stage-2 in this call. Facts the model labels user_observation are
          still written with signal_processed=False, so the next path-A Stage-2
          drain uses them as the user's evidence; rows are stamped
          ``extraction_path=EXTRACTION_PATH_B`` so those labels can be audited

        Returns:
            - ``None``: Stage-1 terminal failure — retries exhausted / LLM
              returned a non-array (e.g. wrapped as ``{"facts": [...]}``). The
              caller should keep the cursor so the next trigger retries the same
              window.
            - ``[]``: Stage-1 succeeded and the LLM judged the window to contain
              0 new facts (fully deduped). The caller may advance the cursor.
            - ``list[dict]``: succeeded with N new facts extracted and persisted.

            The None / [] distinction is critical: collapsing None into [] would
            make path B treat a failed window as "successfully extracted 0" on
            LLM transient failures / malformed payloads and advance the cursor,
            permanently skipping those messages (CodeRabbit / Codex P1 round-2 +
            Codex P1 round-9 on PR #1408).
        """
        extracted = await self._allm_extract_facts_with_known_pool(
            lanlan_name, messages, known_pool,
        )
        if extracted is None:
            return None
        if not extracted:
            # A run that found nothing new is data too: how often path B
            # adds nothing. Failures (None, above) stay unlogged here.
            self._log_path_b_labels(lanlan_name, [], [])
            if coerce_subject(subject) is None:
                # Same as a non-empty run: held-back candidates drain, progress is recorded.
                drained: list[dict] = []
                await self._apersist_new_facts(lanlan_name, [], default_source='ai_disclosure', source_messages=messages,
                                               extraction_path=EXTRACTION_PATH_B, progress_pass='b', source_accepted=drained)
                await self._aadmit_pinned_candidates(lanlan_name, drained, default_source='ai_disclosure',
                                                     conversation_at=conversation_at)
            return []
        source_accepted: list[dict] = []
        persisted = await self._apersist_new_facts(
            lanlan_name, extracted, default_source='ai_disclosure', subject=subject, source_messages=messages,
            source_accepted=source_accepted,
            extraction_path=EXTRACTION_PATH_B,
            progress_pass='b',
        )
        self._log_path_b_labels(lanlan_name, extracted, persisted)
        if coerce_subject(subject) is None:
            # Same private-only rule and source default as the persistence above:
            # a fact without an explicit user_observation source is never pinned.
            await self._aadmit_pinned_candidates(
                lanlan_name, source_accepted, default_source='ai_disclosure',
                conversation_at=conversation_at,
            )
        return persisted

    def _log_path_b_labels(
        self, lanlan_name: str, extracted: list, persisted: list[dict],
    ) -> None:
        """Log how path B's model labeled the facts it returned, and what was
        written: the data for deciding whether its own ``source`` labels can
        be trusted (owner decision 2026-11-07: observe before changing them).

        ``unlabeled`` facts (no valid ``source``) fall back to ``ai_disclosure``.
        """
        labels = {'user_observation': 0, 'ai_disclosure': 0, 'unlabeled': 0}
        for item in extracted:
            source = item.get('source') if isinstance(item, dict) else None
            # A list or dict source is unhashable: check the type first, or a
            # malformed item would crash the whole path-B call.
            known = isinstance(source, str) and source in self._SOURCE_VALUES
            labels[source if known else 'unlabeled'] += 1
        written = {'user_observation': 0, 'ai_disclosure': 0}
        for fact in persisted or ():
            if isinstance(fact, dict) and fact.get('source') in written:
                written[fact['source']] += 1
        _facts_facade.logger.info(
            f"[PathB] {lanlan_name}: 模型标注 用户 {labels['user_observation']} / "
            f"猫娘自述 {labels['ai_disclosure']} / 未标 {labels['unlabeled']}；"
            f"新写入 用户 {written['user_observation']} / "
            f"猫娘自述 {written['ai_disclosure']}"
        )

    async def _allm_extract_facts_with_known_pool(
        self,
        lanlan_name: str,
        messages: list,
        known_pool: list[dict],
    ) -> list[dict] | None:
        """Stage-1 LLM call for path B: role-tagged conversation + known pool。

        Returns raw LLM-extracted list, or None on terminal failure
        (caller swallows in aextract_facts_with_known_pool)."""
        from config.prompts.prompts_memory import get_fact_extraction_ai_aware_prompt

        _, _, _, _, name_mapping, _, _, _, _ = await self._config_manager.aget_character_data()
        name_mapping['ai'] = lanlan_name
        conversation_text = self._format_conversation(messages, name_mapping)
        from memory.long_term_sync import retirement_source_prompt
        conversation_text, source_instruction = retirement_source_prompt(
            os.path.join(self._config_manager.memory_dir, lanlan_name) if getattr(self._config_manager, 'memory_dir', None) is not None else None, messages, conversation_text, name_mapping)
        prompt_lang = _detect_fact_extraction_prompt_language(
            self._messages_locale_text(
                messages,
                roles=frozenset({'ai', 'assistant'}),
            ),
            ui_language=_facts_facade.get_global_language_full(),
        )

        # Render known pool sorted importance DESC, strongest signals first
        # for LLM. Caller already capped it; no duplicate cap here.
        known_lines = []
        for f in known_pool:
            text = f.get('text', '') or ''
            if not text:
                continue
            imp = f.get('importance', 5)
            known_lines.append(f"- {text} (importance: {imp})")
        known_block = "\n".join(known_lines) if known_lines else "(none)"

        prompt = (
            get_fact_extraction_ai_aware_prompt(prompt_lang)
            .replace('{CONVERSATION}', conversation_text)
            .replace('{KNOWN_POOL}', known_block)
            .replace('{LANLAN_NAME}', lanlan_name)
            .replace('{MASTER_NAME}', name_mapping.get('human', '主人'))
        )

        prompt += source_instruction

        extracted = await self._allm_call_with_retries(
            prompt, lanlan_name,
            tier=EVIDENCE_EXTRACT_FACTS_MODEL_TIER,
            call_type="memory_fact_extraction_ai_aware",
        )
        if extracted is None:
            return None
        if not isinstance(extracted, list):
            # Non-array payload (e.g. wrapper `{"facts": [...]}` or occasional model garbage)
            # is Stage-1 terminal failure: return None so `_run_path_b`
            # retains cursor and retries same window next trigger, rather than successful zero extraction
            # advancing cursor and permanently skipping messages (Codex P1 round-9 on PR #1408).
            _facts_facade.logger.warning(
                f"[FactStore] {lanlan_name}: path-B Stage-1 返回非数组 "
                f"{type(extracted).__name__}，按 terminal failure 处理 (cursor 不推进)"
            )
            return None
        return extracted
