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


"""Background vector batches, retained seeds and searchable matrix snapshots.

VectorIndexer owns index scheduling, not the model or persistent state. The
service retains the shared embedder, tasks, registry snapshots, mutation lock
and capacity accounting. Token replanning that changes SQLite and registry
rows is called through that same service owner so admission keeps one budget.
Matrix/key publication and the single refresh-task registration also use that
owner, so scheduling cannot bypass shutdown tracking or split snapshot identity.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Mapping

from utils.embedding_contract import EmbedDeferred
from .models import KnowledgePackError
from .pack_files import file_identity as _file_identity
from .store import VectorSnapshot, normalize_vector

if TYPE_CHECKING:
    from .service import KnowledgeService

logger = logging.getLogger("N.E.K.O.Knowledge")

# These are knowledge-only scheduling bounds. They never throttle memory's
# own model calls; the pause is a duty target, not a CPU usage guarantee.
INDEX_BATCH_SIZE = 4
INDEX_ROUND_CHUNKS = 64
# Scale the minimum pause with measured inference time. Wall time includes
# model contention and laptop sleep, so cap it to avoid stalling the backlog.
INDEX_BATCH_PAUSE_SECONDS = 0.5
INDEX_TARGET_DUTY = 0.25
INDEX_MAX_BATCH_PAUSE_SECONDS = 30.0
INDEX_ROUND_PAUSE_SECONDS = 5.0
INDEX_IDLE_SECONDS = 30.0
VECTOR_REFRESH_SECONDS = 60.0


class _ReplanLimitRefusal(KnowledgePackError):
    """A pack limit refusal with the inputs checked under the mutation lock."""

    def __init__(self, reason: str, keys: Mapping[str, tuple[str, str, int] | None]) -> None:
        super().__init__(reason)
        self.replan_keys = keys


class _ReplanInputRefusal(ValueError):
    """An unfittable header retaining the original ValueError contract."""

    def __init__(self, keys: Mapping[str, tuple[str, str, int] | None]) -> None:
        super().__init__("embedding_input_too_large")
        self.replan_keys = keys


def _batch_pause(inference_seconds: float) -> float:
    """Pause after an index batch: idle at least (1 - duty) / duty times as long
    as the batch computed, so indexing stays at or below INDEX_TARGET_DUTY."""
    idle_ratio = (1.0 - INDEX_TARGET_DUTY) / INDEX_TARGET_DUTY
    pause = max(INDEX_BATCH_PAUSE_SECONDS, inference_seconds * idle_ratio)
    return min(pause, INDEX_MAX_BATCH_PAUSE_SECONDS)


class VectorIndexer:
    """Schedule work against the single runtime's current policy and model."""

    def __init__(self, runtime: KnowledgeService) -> None:
        self._runtime = runtime

    async def _refresh_vectors_now(self) -> None:
        key = self._runtime._vector_refresh_key()
        if key is None:
            return
        _generation, model_id = key
        try:
            snapshot = await self._runtime._thread(self._load_vector_snapshot, model_id)
        except Exception as exc:
            # The scheduled refresh retries; lookups meanwhile skip ids that
            # no longer exist.
            logger.warning("[Knowledge] vector snapshot reload failed (%s)", type(exc).__name__)
            return
        self._runtime._publish_vector_snapshot(snapshot, key)
        await self._clear_damaged_vectors(snapshot)

    def _load_vector_snapshot(self, model_id: str) -> VectorSnapshot | None:
        """Run off-loop: a profile name cannot certify a different resource set."""
        provider = getattr(self._runtime.embedder, "spec", None)
        if callable(provider):
            specification = provider()
            if specification is None:
                return None
            return self._runtime._store.load_vectors(model_id, specification=specification)
        return self._runtime._store.load_vectors(model_id)


    async def _clear_damaged_vectors(self, snapshot: VectorSnapshot | None) -> None:
        """Hand unusable stored vectors back to the indexer, under the write lock.

        Best effort: the snapshot is already in use without them, so a failed
        clear only means they are skipped again on the next load.
        """
        if snapshot is None or not snapshot.damaged:
            return
        damaged = snapshot.damaged
        try:
            await self._runtime._locked(
                lambda: self._runtime._thread(self._runtime._store.clear_vectors, damaged), wait=True
            )
        except Exception as exc:
            logger.warning("[Knowledge] could not clear damaged vectors (%s)", type(exc).__name__)
            return
        self._runtime._index_wakeup.set()

    def _schedule_vector_refresh(self) -> None:
        key = self._runtime._vector_refresh_key()
        if key is None:
            return
        _generation, model_id = key

        async def rebuild() -> bool:
            try:
                snapshot = await self._runtime._thread(self._load_vector_snapshot, model_id)
            except Exception as exc:
                logger.warning("[Knowledge] vector snapshot rebuild failed (%s)", type(exc).__name__)
                return False
            self._runtime._publish_vector_snapshot(snapshot, key)
            await self._clear_damaged_vectors(snapshot)
            return snapshot is not None  # Do not immediately reschedule an unavailable snapshot.

        def rebuilt(task: asyncio.Task[bool]) -> None:
            # A change that arrived while this rebuild ran was skipped above.
            # Re-check once the task is done (from inside it, it still counts
            # as running); a failed rebuild waits for the indexer instead of
            # retrying in a tight loop.
            if task.cancelled() or task.exception() is not None or not task.result():
                return
            if not self._runtime._stopping:
                self._schedule_vector_refresh()

        # Registration and duplicate suppression share the runtime's shutdown owner.
        self._runtime._start_vector_refresh(key, rebuild, rebuilt)

    async def _index_loop(self) -> None:
        last_refresh = time.monotonic()
        while not self._runtime._stopping:
            # Consume only the previous round's wake-up; a busy batch can
            # schedule the next round while this one is still running.
            self._runtime._index_wakeup.clear()
            try:
                processed = await self._index_round(until=last_refresh + VECTOR_REFRESH_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[Knowledge] indexer round failed (%s)", type(exc).__name__)
                processed = 0
            # New vectors become searchable once the backlog drains, or at
            # least every VECTOR_REFRESH_SECONDS while a long backfill runs;
            # reloading the matrix after every batch would cost more than the
            # batch itself.
            if processed == 0 or time.monotonic() - last_refresh >= VECTOR_REFRESH_SECONDS:
                self._runtime._advance_dirty_vectors()
                self._schedule_vector_refresh()
                last_refresh = time.monotonic()
            delay = INDEX_ROUND_PAUSE_SECONDS if processed else INDEX_IDLE_SECONDS
            try:
                await asyncio.wait_for(self._runtime._index_wakeup.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass  # the idle delay elapsed without a wake-up; run another round

    def _vector_pack_ids(self) -> list[str] | None:
        """Packs whose chunks may be embedded now; ``None`` when indexing is off."""
        if self._runtime._stopping or self._runtime._state != "ready" or not self._runtime._registry.enabled:
            return None
        return [record.pack_id for record in self._runtime._registry.packs.values()
                if record.local_embedding and record.pack_id not in self._runtime._broken_packs]

    async def _seed_retained_packages(
        self, pack_ids: list[str], specification: dict, observed_ready: set[tuple[str, str, str, str]] | None = None,
    ) -> None:
        """Recover product-package vectors when assets arrive after text install.

        ``observed_ready`` lives for one index round: complete packs are not
        re-aggregated every batch, yet the next round observes them afresh.
        """
        from .archive import read_archive

        identity = self._runtime._store._spec_hash(specification)
        readiness = None
        for pack_id in pack_ids:
            record = self._runtime._registry.packs.get(pack_id)
            if record is None or not record.archive_sha256:
                continue
            key = (record.archive_sha256, identity)
            if key in self._runtime._seeded_artifacts:
                continue
            ready_key = (pack_id, record.pack_sha256, record.archive_sha256, identity)
            if observed_ready is not None and ready_key in observed_ready:
                continue
            if readiness is None:
                # One aggregate scan for all candidates, without loading each
                # pack's vector BLOBs or revalidating already-installed ZIPs.
                readiness = await self._runtime._thread(lambda: (
                    self._runtime._store.chunk_stats(specification["model_id"], specification=specification),
                    self._runtime._store.pack_versions(),
                ))
            if self._runtime._registry.packs.get(pack_id) != record:
                continue
            counts, indexed = readiness
            stats = counts.get(pack_id, {})
            if (indexed.get(pack_id) == record.pack_sha256 and record.chunks > 0
                    and stats.get("ready") == stats.get("total") == record.chunks):
                # Do not cache this observation beyond the round: missing
                # vectors, changed resources or shared snapshot damage cleanup
                # must reopen it.
                if observed_ready is not None:
                    observed_ready.add(ready_key)
                continue
            # Identity of the expected location: a missing optional ZIP is
            # remembered as None and retried once a file appears there.
            stamp = await self._runtime._thread(
                _file_identity, self._runtime.root / "artifacts" / (record.archive_sha256 + ".neko-knowledge.zip"))
            if key in self._runtime._seed_failures and self._runtime._seed_failures[key] == stamp:
                continue  # A damaged ZIP is not re-hashed and re-validated every batch.
            try:
                # Resolved inside the guard: a missing retained ZIP must not
                # stop ordinary backfill of this or any other pack.
                path = self._runtime.artifact_path(record.archive_sha256)
                checked = await self._runtime._thread(read_archive, path, expected_spec=specification)
                if checked["artifact_sha256"] != record.archive_sha256:
                    self._runtime._seed_failures[key] = stamp
                    continue
                async def write():
                    if self._runtime._registry.packs.get(pack_id) != record:
                        return None  # Retry the same archive against the new policy record.
                    return await self._runtime._thread(self._runtime._store.seed_pack_vectors, pack_id, checked["seed_vectors"])
                stored = await self._runtime._locked(write, wait=True)
                if stored is None:
                    continue
                if stored:
                    self._runtime._vectors_dirty = True
                self._runtime._seeded_artifacts.add(key)
                self._runtime._seed_failures.pop(key, None)
            except KnowledgePackError:
                # Valid canonical text can still use ordinary backfill. Only a
                # verdict on the bytes is remembered; I/O errors (a file locked
                # by a scanner) are retried on the next batch.
                self._runtime._seed_failures[key] = stamp
            except OSError:
                pass

    async def _index_round(self, until: float | None = None) -> int:
        """Embed pending chunks; return how many were tried.

        The round ends early once ``until`` (when the vector snapshot is due
        for a reload) has passed, so a long backfill with slow inference still
        makes its new vectors searchable every VECTOR_REFRESH_SECONDS.
        """
        if self._runtime._pending_chunk_reconcile:
            await self._runtime._locked(lambda: self._runtime._thread(self._runtime._reconcile_chunk_counts_blocking), wait=True)
        if self._runtime._state != "ready" or not self._runtime._registry.enabled or self._runtime.embedder is None:
            return 0
        model_id = self._runtime._current_model_id()
        if model_id is None:
            return 0
        if self._runtime._index_model_id != model_id:
            provider = getattr(self._runtime.embedder, "spec", None)
            if callable(provider) and await self._runtime._thread(provider) is None:
                # A modern provider has not certified this model transition
                # yet. Preserve failures until its resources are available.
                return 0
            # Failure counts belong to the model (and process) that produced
            # them: a new model, or a restart, retries every chunk once more.
            await self._runtime._locked(lambda: self._runtime._thread(self._runtime._store.reset_attempts), wait=True)
            self._runtime._index_model_id = model_id
        processed = 0
        deferred_packs: set[str] = set()
        observed_ready: set[tuple[str, str, str, str]] = set()
        while processed < INDEX_ROUND_CHUNKS and not self._runtime._stopping:
            # Re-read the switches every batch: the user may turn knowledge or
            # a pack's vectors off while a batch is embedding.
            pack_ids = self._vector_pack_ids()
            if pack_ids is None:
                break
            pack_ids = [pack_id for pack_id in pack_ids if pack_id not in deferred_packs]
            specification = await self._runtime._thread(getattr(self._runtime.embedder, "spec", lambda: None))
            if callable(getattr(self._runtime.embedder, "spec", None)) and specification is None:
                # A modern provider that cannot prove its loaded resources
                # must wait, rather than emit uncertified/truncated vectors.
                break
            if specification is not None:
                if self._runtime._refused_replans:
                    await self._runtime._reopen_refused_replans(specification)
                await self._seed_retained_packages(pack_ids, specification, observed_ready)
            rows = await self._runtime._thread(
                self._runtime._store.pending_chunks, model_id=model_id, pack_ids=pack_ids, limit=INDEX_BATCH_SIZE,
                require_spec=specification is not None,
                specification=specification,
            )
            if not rows:
                break
            counter = getattr(self._runtime.embedder, "count_tokens", None)
            if specification is not None and callable(counter):
                counts = await self._runtime._thread(counter, [text for _id, _hash, text in rows])
                if counts is None:
                    break  # Missing assets are deferred, never failed attempts.
                if any(size > specification["max_length"] for size in counts):
                    owners = await self._runtime._thread(self._runtime._store.chunk_pack_ids, [
                        row[0] for row, size in zip(rows, counts) if size > specification["max_length"]
                    ])
                    if not owners:
                        break  # Concurrent replacement removed this batch's rows.
                    refused: set[str] = set()
                    for pack_id in sorted(set(owners.values())):
                        observed_key = self._runtime._replan_key(pack_id, specification)
                        if observed_key is not None and pack_id in self._runtime._refused_replans:
                            # Entries whose inputs changed were reopened above.
                            refused.add(pack_id)
                            continue
                        trigger_rows = [(row[0], row[1]) for row, size in zip(rows, counts)
                                        if size > specification["max_length"] and owners.get(row[0]) == pack_id]
                        try:
                            await self._runtime._replan_vector_inputs([pack_id], triggers={pack_id: trigger_rows},
                                                             specification=specification)
                        except KnowledgePackError as exc:
                            key = exc.replan_keys.get(pack_id) if isinstance(exc, _ReplanLimitRefusal) else None
                            if exc.reason in {"too_many_chunks", "capacity_chunks"} and key is not None:
                                # Remember the limit refusal: re-tokenizing the
                                # whole pack every round cannot change it.
                                self._runtime._refused_replans[pack_id] = (key, exc.reason)
                                refused.add(pack_id)
                            else:
                                deferred_packs.add(pack_id)
                            if self._runtime._pending_chunk_reconcile:
                                return processed  # Reconcile before planning or encoding another pack.
                        except ValueError as exc:
                            key = exc.replan_keys.get(pack_id) if isinstance(exc, _ReplanInputRefusal) else None
                            if str(exc) == "embedding_input_too_large" and key is not None:
                                # An entry header alone exceeds the token budget:
                                # like a limit refusal, re-tokenizing the pack
                                # cannot change it until the pack or encoder does.
                                self._runtime._refused_replans[pack_id] = (key, "embedding_input_too_large")
                                refused.add(pack_id)
                            else:
                                deferred_packs.add(pack_id)
                            if self._runtime._pending_chunk_reconcile:
                                return processed  # Reconcile before planning or encoding another pack.
                        except OSError:
                            # One missing source cannot starve unrelated packs.
                            # Exclude it for this round so re-reading the same
                            # rows cannot spin forever.
                            deferred_packs.add(pack_id)
                            if self._runtime._pending_chunk_reconcile:
                                return processed  # Reconcile before planning or encoding another pack.
                    if refused:
                        # Over-long rows of a refused pack stay keyword-only and
                        # show as failed, so progress can finish; its other rows
                        # still get vectors. A new model or restart retries once.
                        over_long = [(row[0], row[1], owners[row[0]]) for row, size in zip(rows, counts)
                                     if size > specification["max_length"] and owners.get(row[0]) in refused]
                        marked = await self._runtime._locked(
                            lambda ids=over_long: self._runtime._thread(self._runtime._store.exhaust_attempts, ids), wait=True)
                        if marked:
                            # Progress: those rows leave the pending set, so the
                            # loop moves on to the pack's next batch this round.
                            processed += marked
                        else:
                            # Nothing changed; skip the pack so the loop cannot
                            # re-read the same rows.
                            deferred_packs.update(refused)
                    continue  # Re-read the new complete inputs before inference.
            started = time.monotonic()
            try:
                compute = getattr(self._runtime.embedder, "embed_documents", self._runtime.embedder.embed_batch)
                vectors = await compute([text for _id, _hash, text in rows])
            except EmbedDeferred as deferred:
                # Scheduling pressure is not an encoding attempt. Keep the
                # same pending rows and let the next round retry them.
                await asyncio.sleep(max(0.05, deferred.retry_after_ms / 1000))
                self._runtime._index_wakeup.set()
                return processed
            after_specification = await self._runtime._thread(getattr(self._runtime.embedder, "spec", lambda: None))
            if after_specification != specification:
                break  # Changed resources cannot certify the batch just computed.
            inference_seconds = time.monotonic() - started
            if self._runtime._current_model_id() != model_id:
                break
            pack_ids = self._vector_pack_ids()
            if pack_ids is None:
                break
            blobs = [
                (chunk_id, text_hash, normalize_vector(vector) if vector is not None else None)
                for (chunk_id, text_hash, _text), vector in zip(rows, vectors)
            ]
            stored, failed = await self._runtime._locked(
                lambda blobs=blobs, pack_ids=pack_ids: self._runtime._thread(
                    self._runtime._store.store_vectors, model_id=model_id, rows=blobs, pack_ids=pack_ids,
                    specification=specification,
                ),
                wait=True,
            )
            if stored:
                self._runtime._vectors_dirty = True
            self._runtime.diagnostics.record_index_batch(
                model_id=model_id,
                selected=len(rows),
                stored=stored,
                failed=failed,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            processed += len(rows)
            # Every batch that ran inference pauses, also one that stored
            # nothing: failing chunks must not push indexing past its duty.
            await asyncio.sleep(_batch_pause(inference_seconds))
            if stored == 0:
                break
            if until is not None and time.monotonic() >= until:
                break
        return processed
