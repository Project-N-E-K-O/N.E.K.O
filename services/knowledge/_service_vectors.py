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
"""Vector snapshot refresh, replanning, query leases, and catalog/status methods."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
from dataclasses import replace
from typing import Any, AsyncIterator, Callable, Coroutine, Mapping, Sequence
from .models import MATERIAL_TYPES, KnowledgePackError, decode_pack_bytes, pack_id_is_valid, pack_sha256
from .registry import PACKS_DIR, PackRecord, Registry
from .store import StoredEntry, VectorSnapshot, count_pack_chunks
from .querying import DEFAULT_QUERY_BUDGET_MS
from .indexing import _ReplanInputRefusal, _ReplanLimitRefusal
from .jobs import consume_task_result as _consume
from typing import TypeVar


_T = TypeVar("_T")

class _KnowledgeVectorsMixin:
    def _embedding_state(self) -> str:
        if self.embedder is None:
            return "unavailable"
        try:
            return self.embedder.state()
        except Exception:
            return "unavailable"

    def _current_model_id(self) -> str | None:
        if self._embedding_state() != "ready":
            return None
        try:
            return self.embedder.model_id() if self.embedder is not None else None
        except Exception:
            return None

    def _vector_snapshot(self) -> tuple[VectorSnapshot | None, tuple[int, str] | None]:
        """Capture a matrix and its calibration key without yielding between them."""
        return self._vectors, self._vectors_built_for

    def _vector_refresh_key(self) -> tuple[int, str] | None:
        """Name the current model/generation only while the runtime is ready."""
        model_id = self._current_model_id()
        if model_id is None or self._state != "ready":
            return None
        return self._vector_generation, model_id

    def _publish_vector_snapshot(self, snapshot: VectorSnapshot | None, key: tuple[int, str]) -> None:
        """Use ``snapshot`` unless one loaded for a later generation is in place."""
        built = self._vectors_built_for
        if built is not None and built[1] == key[1] and built[0] > key[0]:
            return
        self._vectors = snapshot
        # Transiently unverifiable resources are not a completed empty index.
        # Keep the same generation eligible for the next scheduled refresh.
        self._vectors_built_for = key if snapshot is not None else None

    async def _compatible_vector_snapshot(self, snapshot: VectorSnapshot | None) -> VectorSnapshot | None:
        """Fail closed if on-disk resources changed after the matrix was loaded."""
        provider = getattr(self.embedder, "spec", None)
        if snapshot is not None and callable(provider):
            current = await self._query_thread(provider)
            if current is None or current != snapshot.specification:
                return None
        return snapshot

    def _advance_dirty_vectors(self) -> None:
        """Consume one dirty generation before the indexer schedules its reload."""
        if self._vectors_dirty:
            self._vectors_dirty = False
            self._vector_generation += 1

    def _start_vector_refresh(
        self, key: tuple[int, str], rebuild: Callable[[], Coroutine[Any, Any, bool]],
        completed: Callable[[asyncio.Task[bool]], None],
    ) -> None:
        """Register the single matrix loader that stop() must cancel and drain.

        The factory is invoked only after the duplicate guard, so a skipped
        refresh never creates an unawaited coroutine. The indexer retains its
        success/retry callback and the original task name.
        """
        if self._vectors_built_for == key:
            return
        if self._vector_task is not None and not self._vector_task.done():
            return
        self._vector_task = asyncio.create_task(rebuild(), name="knowledge-vector-snapshot")
        self._vector_task.add_done_callback(completed)

    async def _replan_vector_inputs(
        self, pack_ids: list[str], *, triggers: Mapping[str, Sequence[tuple[int, str]]] | None = None,
        specification: Mapping | None = None,
    ) -> None:
        """Rebuild chunks after a tokenizer becomes available, retaining text.

        Imports made with missing model assets stay keyword-searchable. Before
        any later encoding, full input counts may require smaller chunks. The
        same total/per-pack limits remain authoritative for that rebuild.
        ``triggers`` are the over-long ``(id, text_hash)`` rows that asked for
        the replan; a pack whose rows were replaced meanwhile (for example by
        a newly installed revision with seeded vectors) is left untouched.
        """
        from . import service as _svc
        async def run():
            def rebuild():
                targets = list(pack_ids)
                if triggers is not None:
                    targets = [pack_id for pack_id in targets
                               if self._store.chunks_current(pack_id, triggers.get(pack_id, ()))]
                    if not targets:
                        return None  # Nothing rebuilt: the caller re-reads its batch.
                # A previous pack may have committed SQLite before its
                # registry flush failed. Capacity must use those real counts.
                self._reconcile_chunk_counts_blocking()
                fit = self._chunk_fit()
                if fit is None:
                    return self._registry
                # A waiter may observe changed capacity after taking the lock.
                # Cache refusals against this check, never the caller's old key.
                refusal_keys = {pack_id: self._replan_key(pack_id, specification) for pack_id in targets}
                planned = []
                for pack_id in targets:
                    record = self._registry.packs.get(pack_id)
                    if record is None:
                        continue
                    raw = _svc._read_bounded(self.root / PACKS_DIR / record.file_name)
                    if pack_sha256(raw) != record.pack_sha256:
                        raise KnowledgePackError("pack_file_mismatch")
                    pack = decode_pack_bytes(raw)
                    try:
                        chunks = count_pack_chunks(pack, fit=fit)
                    except ValueError as exc:
                        if str(exc) == "embedding_input_too_large":
                            raise _ReplanInputRefusal(refusal_keys) from exc
                        raise
                    if chunks > _svc.MAX_CHUNKS_PER_PACK:
                        raise _ReplanLimitRefusal("too_many_chunks", refusal_keys)
                    planned.append((pack, record, chunks))
                changed = {record.pack_id: chunks for _pack, record, chunks in planned}
                if sum(changed.get(key, record.chunks) for key, record in self._registry.packs.items()) > _svc.MAX_TOTAL_CHUNKS:
                    raise _ReplanLimitRefusal("capacity_chunks", refusal_keys)
                registry = self._registry
                for pack, record, chunks in planned:
                    self._store.replace_pack(pack, pack_sha256=record.pack_sha256,
                                             disabled_keys=record.disabled_titles, fit=fit)
                    self._pending_chunk_reconcile = True
                    registry = registry.with_pack(replace(record, chunks=chunks))
                _svc.save_registry(self.root, registry)
                self._pending_chunk_reconcile = False
                return registry

            stale = False
            try:
                registry = await self._thread(rebuild)
                if registry is None:
                    stale = True
                    return
                self._publish_registry(registry)
            finally:
                if not stale:
                    # Even a failed registry flush may follow committed row-ID
                    # changes. Old snapshots and previous seeding attempts cannot
                    # describe those new rows; reconcile before further encoding.
                    self._vectors = None
                    self._vectors_built_for = None
                    self._vector_generation += 1
                    self._seeded_artifacts.clear()
                    self._seed_failures.clear()
        await self._locked(run, wait=True)

    async def _reopen_refused_replans(self, specification: Mapping) -> None:
        """Retry a refused replan once what its refusal depended on changed.

        A per-pack limit depends on the pack and the encoder; a total-capacity
        refusal also on the other packs, so freeing capacity reopens it.
        """
        for pack_id, (key, reason) in list(self._refused_replans.items()):
            current = self._replan_key(pack_id, specification)
            if current is None:
                self._refused_replans.pop(pack_id, None)  # Removed pack: its rows are gone.
                continue
            relevant = slice(None) if reason == "capacity_chunks" else slice(0, 2)
            if current[relevant] == key[relevant]:
                continue
            await self._locked(lambda pack_id=pack_id: self._thread(self._store.reset_attempts, pack_id), wait=True)
            # Only after the reset succeeded: a failed write keeps the record
            # so the next batch tries to reopen the rows again.
            self._refused_replans.pop(pack_id, None)

    def _replan_key(self, pack_id: str, specification: Mapping | None) -> tuple[str, str, int] | None:
        """Identify the inputs a limit-refused token replan depended on."""
        record = self._registry.packs.get(pack_id)
        if record is None or not specification:
            return None
        others = sum(item.chunks for key, item in self._registry.packs.items() if key != pack_id)
        return record.pack_sha256, self._store._spec_hash(specification), others

    def _reconcile_chunk_counts_blocking(self) -> None:
        """Repair a failed replan flush under the existing mutation lock."""
        from . import service as _svc
        if not self._pending_chunk_reconcile:
            return
        counts = self._store.chunk_stats(None)
        registry = self._registry
        for pack_id, record in registry.packs.items():
            chunks = counts.get(pack_id, {}).get("total", 0)
            if pack_id in self._broken_packs:
                # A pack awaiting repair keeps its reservation, as at startup:
                # max(registered count, rows kept), never its dropped rows' 0.
                chunks = max(chunks, record.chunks)
            registry = registry.with_pack(replace(record, chunks=chunks))
        _svc.save_registry(self.root, registry)
        self._publish_registry(registry)
        self._pending_chunk_reconcile = False

    def _current_packs(self, pack_ids: Sequence[str], registry: Registry, *, management: bool = False) -> set[str]:
        """Packs whose indexed rows are the version ``registry`` describes.

        An import writes the rows before the registry, and queries do not take
        the write lock, so a query may see a newer (or rolled-back) version of
        a pack than its registry snapshot; such rows are not served.
        """
        indexed = self._store.pack_versions()
        return {
            pack_id
            for pack_id in pack_ids
            if (record := registry.packs.get(pack_id)) is not None
            and (current := self._registry.packs.get(pack_id)) is not None and (management or current.enabled)
            and indexed.get(pack_id) == record.pack_sha256
        }

    async def query(
        self,
        *,
        query: str,
        mode: str = "lookup",
        material_type: str = "auto",
        limit: int = 3,
        budget_ms: int = DEFAULT_QUERY_BUDGET_MS,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Delegate tool lookup; the service retains all runtime ownership."""
        return await self._queries.query(
            query=query, mode=mode, material_type=material_type, limit=limit,
            budget_ms=budget_ms, language=language,
        )

    async def turn_context(
        self,
        *,
        text: str,
        exclude_card_ids: Sequence[str] = (),
        budget_ms: int = DEFAULT_QUERY_BUDGET_MS,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Delegate passive selection without retaining per-session state."""
        return await self._queries.turn_context(
            text=text, exclude_card_ids=exclude_card_ids,
            budget_ms=budget_ms, language=language,
        )

    def _start_query_embedding(
        self, text: str, *, limit: int, probe_model_id: str | None = None,
    ) -> asyncio.Task[Any] | None:
        """Admit one query embedding and retain it after a caller's timeout.

        Callers supply the query-domain limit at the actual admission point.
        Passive context starts its calibration probes only after this slot is
        available; tool lookup keeps its existing model-readiness precondition.
        """
        if len(self._query_embeddings) >= limit:
            return None
        if probe_model_id is not None:
            if self.embedder is None:
                return None
            self._calibration.ensure_probes(probe_model_id, self.embedder.embed_batch)
        task = asyncio.create_task(getattr(self.embedder, "embed_query", self.embedder.embed)(text))
        task.add_done_callback(_consume)
        self._query_embeddings.add(task)
        task.add_done_callback(self._query_embeddings.discard)
        return task

    @contextlib.asynccontextmanager
    async def _query_lease(self) -> AsyncIterator[None]:
        """Hold a query slot until the query and all its thread work are done.

        A timeout only stops waiting for a thread; the work itself runs on.
        Releasing the slot only when that work ends keeps the number of
        running scans at ``QUERY_CONCURRENCY``, whatever the budgets.
        """
        from . import service as _svc
        await self._query_slots.acquire()
        work: list[concurrent.futures.Future[Any]] = []
        token = _svc._QUERY_WORK.set(work)
        try:
            yield
        finally:
            _svc._QUERY_WORK.reset(token)
            running = [future for future in work if not future.done()]
            if not running:
                self._query_slots.release()
            else:
                loop = asyncio.get_running_loop()
                left = [len(running)]

                def one_done(_future: concurrent.futures.Future[Any]) -> None:
                    left[0] -= 1
                    if left[0] == 0:
                        self._query_slots.release()

                for future in running:
                    future.add_done_callback(
                        lambda f: loop.call_soon_threadsafe(one_done, f)
                    )

    async def _query_thread(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run query work in a thread that the query's slot stays tied to."""
        from . import service as _svc
        work = _svc._QUERY_WORK.get()
        if work is None:
            return await self._thread(fn, *args, **kwargs)
        if self._query_pool is None:
            self._query_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=_svc.QUERY_CONCURRENCY, thread_name_prefix="knowledge-query"
            )
        future = self._query_pool.submit(contextvars.copy_context().run, fn, *args, **kwargs)
        work.append(future)
        self._track_thread_work(future)
        return await asyncio.wrap_future(future)

    def _entry_payload(self, entry: StoredEntry, *, detail: bool) -> dict[str, Any]:
        record = self._registry.packs.get(entry.pack_id)
        preview = " ".join(entry.content.split())
        payload = {
            "pack_id": entry.pack_id,
            "title": entry.title,
            "terms": entry.terms,
            "tags": entry.tags,
            "summary": entry.summary,
            "content_preview": preview[:180] + ("..." if len(preview) > 180 else ""),
            "material_type": record.effective_material_type if record else "knowledge",
            "source": {
                "name": record.source.name if record else entry.pack_id,
                "homepage": record.source.homepage if record else "",
                "license": record.source.license if record else "",
            },
            "disabled": entry.disabled,
        }
        if detail:
            payload["content"] = entry.content
        return payload

    async def list_entries(
        self, *, query: str = "", pack_id: str = "", limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        from . import service as _svc
        self._require_ready()
        if pack_id and not pack_id_is_valid(pack_id):
            raise _svc.KnowledgeUnavailable("invalid_request")
        query = query.strip()[:200]
        # Only registered packs whose rows are current: leftovers of a removal
        # whose cleanup failed (or of an import in flight) stay hidden.
        registry = self._registry
        visible = await self._thread(
            self._current_packs, [pack_id] if pack_id else list(registry.packs), registry, management=True
        )
        pack_ids = sorted(visible)
        if query:
            entries = await self._thread(
                self._store.search_entries, query, pack_ids=pack_ids, limit=limit + 1, offset=offset
            )
            has_more = len(entries) > limit
            entries = entries[:limit]
            total = None
        else:
            total = await self._thread(self._store.count_entries, pack_ids=pack_ids)
            entries = await self._thread(
                self._store.list_entries, pack_ids=pack_ids, limit=limit, offset=offset
            )
            has_more = offset + len(entries) < total
        # Recheck after the read: a pack replaced in between is left out
        # rather than shown with the snapshot's (older) metadata.
        still_current = await self._thread(
            self._current_packs, sorted({entry.pack_id for entry in entries}), registry, management=True
        )
        entries = [entry for entry in entries if entry.pack_id in still_current]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "items": [self._entry_payload(entry, detail=False) for entry in entries],
        }

    async def get_entry(self, pack_id: str, title: str) -> dict[str, Any]:
        from . import service as _svc
        self._require_ready()
        if not pack_id_is_valid(pack_id):
            raise _svc.KnowledgeUnavailable("invalid_request")
        registry = self._registry
        if not await self._thread(self._current_packs, [pack_id], registry, management=True):
            raise _svc.KnowledgeUnavailable("not_found")
        entry = await self._thread(self._store.get_entry, pack_id, title)
        if entry is None or not await self._thread(self._current_packs, [pack_id], registry, management=True):
            raise _svc.KnowledgeUnavailable("not_found")
        return {"entry": self._entry_payload(entry, detail=True)}

    async def list_packs(self) -> list[dict[str, Any]]:
        self._require_ready()
        model_id = self._current_model_id()
        provider = getattr(self.embedder, "spec", None)
        specification = await self._thread(provider) if callable(provider) else None
        # A profile name alone cannot certify vectors after resource changes.
        # Legacy providers have no spec API; modern unverifiable ones wait.
        if callable(provider) and specification is None:
            model_id = None
        counts, stats, indexed_versions = await asyncio.gather(
            self._thread(self._store.entry_counts),
            self._thread(self._store.chunk_stats, model_id, specification=specification, include_export_readiness=True),
            self._thread(self._store.pack_versions),
        )
        embedding_state = self._embedding_state()
        # Inspect local sidecar facts off-loop; imported ZIP/JSON origins alone
        # do not grant the document-management capability.
        def source_capabilities():
            self._source_capabilities = {key: value for key, value in self._source_capabilities.items() if key in self._registry.packs}
            # Bind each hint to the exact body examined in this worker. The
            # listing can observe a replacement registry after this returns.
            records = self._registry.packs
            return {key: record.pack_sha256 for key, record in records.items() if self._listed_sources_managed(key)}
        managed = await self._thread(source_capabilities)
        # No await separates capturing this pair from projecting its active
        # rows. Later publications cannot lend a newer counter to older policy.
        registry, counter = self._registry_snapshot
        packs: list[dict[str, Any]] = []
        for record in sorted(registry.packs.values(), key=lambda item: item.pack_id):
            entries, disabled = counts.get(record.pack_id, (0, 0))
            chunk = stats.get(record.pack_id, {"total": 0, "ready": 0, "failed": 0})
            packs.append(
                {
                    "pack_id": record.pack_id,
                    # Reuse the installed identity; listing never hashes files.
                    "pack_sha256": record.pack_sha256,
                    "policy_revision": {"epoch": self._policy_epoch, "counter": counter},
                    "source": {
                        "name": record.source.name,
                        "homepage": record.source.homepage,
                        "license": record.source.license,
                    },
                    "declared_material_type": record.declared_material_type,
                    "material_type_override": record.material_type_override,
                    "effective_material_type": record.effective_material_type,
                    "entries": entries,
                    "enabled": record.enabled,
                    "origin": record.origin,
                    "retained": False,
                    "sources_managed": managed.get(record.pack_id) == record.pack_sha256,
                    "disabled_entries": disabled,
                    "auto_context": record.auto_context,
                    "local_embedding": record.local_embedding,
                    "chunks_total": chunk["total"],
                    "chunks_ready": chunk["ready"],
                    "chunks_failed": chunk["failed"],
                    # A failed replacement may leave complete vectors for a
                    # different revision; only the healthy active revision is ready.
                    "export_ready": (bool(chunk.get("export_ready", 0))
                                     and indexed_versions.get(record.pack_id) == record.pack_sha256
                                     and record.pack_id not in self._broken_packs),
                    "vector_state": self._vector_state(
                        record, chunk, embedding_state, indexing=registry.enabled
                    ),
                    "broken": record.pack_id in self._broken_packs,
                    "subscription": record.subscription.to_json() if record.subscription else None,
                    "installed_at": record.installed_at,
                    "updated_at": record.updated_at,
                }
            )
        for retained in await self._thread(self._sources.retained_packs):
            if retained["pack_id"] in self._registry.packs:
                continue
            packs.append({"pack_id": retained["pack_id"], "source": {"name": retained["name"], "homepage": "", "license": ""},
                          "declared_material_type": "knowledge", "material_type_override": None, "effective_material_type": "knowledge",
                          "entries": 0, "disabled_entries": 0, "enabled": False, "origin": "imported", "retained": True,
                          "sources_managed": True,
                          "auto_context": False, "local_embedding": False, "chunks_total": 0, "chunks_ready": 0,
                          "chunks_failed": 0, "export_ready": False, "vector_state": "off", "broken": False, "subscription": None,
                          "installed_at": "", "updated_at": ""})
        return packs

    @staticmethod
    def _vector_state(
        record: PackRecord, chunk: dict[str, int], embedding_state: str, *, indexing: bool = True
    ) -> str:
        total, ready = chunk["total"], chunk["ready"]
        if total <= 0:
            return "none"
        # Policy first: vectors kept from before the switch are not used.
        if not record.local_embedding:
            return "off"
        if ready >= total:
            return "complete"
        if not indexing:
            # Knowledge is switched off, so nothing is being computed.
            return "paused"
        if embedding_state != "ready":
            return "waiting"
        if ready + chunk["failed"] >= total:
            return "partial"
        return "building"

    async def status(self) -> dict[str, Any]:
        from . import service as _svc
        base: dict[str, Any] = {
            "state": self._state,
            "error_code": self._error_code,
            "embedding": {"state": self._embedding_state(), "model_id": self._current_model_id()},
            **self.availability(),
        }
        if self._state != "ready":
            return base
        packs = await self.list_packs()
        by_type = {kind: [p for p in packs if p["effective_material_type"] == kind] for kind in MATERIAL_TYPES}
        # Packs with local vectors off are keyword-only and never get more
        # vectors; counting them would keep the overview below 100% forever.
        vector_packs = [p for p in packs if p["local_embedding"]]
        chunks_total = sum(p["chunks_total"] for p in vector_packs)
        chunks_ready = sum(p["chunks_ready"] for p in vector_packs)
        base.update(
            {
                "packs": len(packs),
                "entries": sum(p["entries"] for p in packs),
                "disabled_entries": sum(p["disabled_entries"] for p in packs),
                "knowledge_packs": len(by_type["knowledge"]),
                "corpus_packs": len(by_type["corpus"]),
                "knowledge_entries": sum(p["entries"] for p in by_type["knowledge"]),
                "corpus_entries": sum(p["entries"] for p in by_type["corpus"]),
                "chunks_total": chunks_total,
                "chunks_ready": chunks_ready,
                "chunks_failed": sum(p["chunks_failed"] for p in vector_packs),
                "indexed_percent": round(chunks_ready * 100 / chunks_total, 1) if chunks_total else 0.0,
                "broken_packs": list(self._broken_packs),
                # The largest packs only: this is an overview, not a pack list.
                "sources": [
                    {"pack_id": p["pack_id"], "name": p["source"]["name"], "entries": p["entries"]}
                    for p in sorted(packs, key=lambda p: -p["entries"])[:_svc.STATUS_SOURCES]
                ],
            }
        )
        return base
