"""Async personal-library operations, with blocking work owned by bounded jobs."""

from __future__ import annotations

import asyncio
import math
from pathlib import Path
from typing import Callable

from .documents import parse_source
from . import indexing
from .jobs import JobContext, JobManager, run_sync
from .models import EmbeddingAdapter, LibraryError, LibraryLimits, checked_id, digest, new_id, now
from .store import LibraryStore


class LibraryService:
    """Coordinate an explicitly opened personal domain without touching chat state."""

    def __init__(self, root: Path, embedder: EmbeddingAdapter | None = None, parser: Callable | None = None,
                 write_guard: Callable | None = None, limits: LibraryLimits | None = None):
        self.limits = limits or LibraryLimits()
        self.store = LibraryStore(root, self.limits, write_guard)
        self.embedder = embedder
        self.parser = parser or parse_source
        self.jobs = JobManager(self.store)
        self._admission = asyncio.Lock()
        self._preview_admissions: set[asyncio.Task] = set()
        self._preview_admissions_closed = False
        self._discarding_previews: set[str] = set()
        self._preview_reclaims: dict[str, asyncio.Task] = {}
        self._started = False

    async def start(self) -> None:
        """Open only the explicit managed root; imports alone never initialize storage."""
        if self._started:
            return
        await run_sync(self.store.initialize)
        await self.jobs.start()
        # The application Host already owns its writer lock. Recover tasks
        # first, then reclaim only obsolete derived indexes before admitting UI
        # work; saved material and retry snapshots remain untouched.
        await run_sync(self.store.prune_indexes)
        self._started = True

    async def close(self) -> None:
        """Stop local workers without closing the host-owned embedding adapter."""
        if self._started:
            self._preview_admissions_closed = True
            if self._preview_admissions:
                # A stage writer can outlive its HTTP caller. Keep jobs open and
                # retain root ownership until every saved original is registered
                # as a public job or explicitly reclaimed after failed admission.
                await asyncio.gather(*(asyncio.shield(task) for task in tuple(self._preview_admissions)),
                                     return_exceptions=True)
            await self.jobs.close()
            if self._preview_reclaims:
                # A disconnected discard caller does not own the parser's
                # lifetime. Keep the root until those owned cleanups finish too.
                await asyncio.gather(*tuple(self._preview_reclaims.values()), return_exceptions=True)
            self._started = False

    def _ready(self) -> None:
        if not self._started or self.jobs.closed:
            raise LibraryError("service_unavailable")

    @staticmethod
    def _document_summary(document: dict) -> dict:
        return {key: document[key] for key in ("document_id", "filename", "version_id", "sha256", "size", "semantic_state")} | {"chunks_count": len(document["chunks"])}

    def _summary(self, manifest: dict) -> dict:
        documents = self.store.documents(manifest)
        states = [document["semantic_state"] for document in documents]
        state = "ready" if states and all(value == "ready" for value in states) else "partial" if any(value in {"ready", "partial"} for value in states) else "unavailable"
        summary = {key: value for key, value in manifest.items() if key != "documents"} | {
            "documents_count": len(documents), "semantic_state": state,
        }
        if "installation" in summary:
            # Older trial manifests may contain an internal absolute artifact
            # path. Browsers only need stable IDs and installation facts.
            summary["installation"] = {key: value for key, value in summary["installation"].items()
                                       if key != "artifact_path"}
        return summary

    async def create_library(self, name: str) -> dict:
        """Create a local draft with a stable work identity and no active documents."""
        self._ready()
        return await run_sync(lambda: self._summary(self.store.create(name)))

    async def list_libraries(self) -> list[dict]:
        """List only this domain's committed, non-deleted libraries."""
        self._ready()
        return await run_sync(lambda: [self._summary(manifest) for manifest in self.store.list()])

    async def get_library(self, library_id: str) -> dict:
        """Return one consistent manifest and document summary projection."""
        self._ready()
        def read():
            with self.store.lock:
                manifest = self.store.manifest(library_id)
                return {"library": self._summary(manifest), "documents": [
                    self._document_summary(document) for document in self.store.documents(manifest)
                ]}
        return await run_sync(read)

    async def rename_library(self, library_id: str, name: str) -> dict:
        """Change a display name without changing stable source or vector identity."""
        self._ready()
        return await run_sync(lambda: self._summary(self.store.rename(library_id, name)))

    async def delete_library(self, library_id: str) -> None:
        """Invalidate workers and persist a tombstone before reclaiming any inputs."""
        self._ready()
        await self.jobs.invalidate(library_id)
        await run_sync(self.store.delete_library, library_id)

    async def delete_document(self, library_id: str, document_id: str) -> None:
        """Invalidate uncommitted document work before removing its active pointer."""
        self._ready()
        await self.jobs.invalidate(library_id, document_id)
        await run_sync(self.store.delete_document, library_id, document_id)

    async def get_document(self, library_id: str, document_id: str) -> dict:
        """Read complete committed source text; external files are never reopened."""
        self._ready()
        return await run_sync(self.store.document, library_id, document_id)

    async def submit_job(self, kind: str, library_id: str | None, runner: Callable, *, frozen_input: dict) -> dict:
        """Share one task queue with package operations; cancelled requests do not orphan admission."""
        self._ready()
        submission = asyncio.create_task(self.jobs.submit(kind, library_id, runner, frozen_input))
        try:
            return await asyncio.shield(submission)
        except asyncio.CancelledError:
            # Callers can hold an input admission lock. Do not release that lock
            # while a still-running submit can register a new reference to files
            # a concurrent discard would otherwise believe were unowned.
            while not submission.done():
                try:
                    await asyncio.shield(submission)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if submission.done() and not submission.cancelled():
                submission.exception()
            raise

    async def preview_document(self, library_id: str, filename: str, data: bytes, document_id: str | None = None) -> dict:
        """Persist an original first, then enqueue parsing; preview never enters search."""
        self._ready()
        if self._preview_admissions_closed:
            raise LibraryError("service_unavailable")
        # These tasks retain uploaded bytes while waiting for the shared input
        # lock, so bound them before creating a second queue beside real jobs.
        if len(self._preview_admissions) >= self.limits.max_pending_jobs:
            raise LibraryError("jobs_busy")

        async def admit():
            async with self._admission:
                if sum(record["state"] in {"queued", "running"} for record in self.jobs.records.values()) >= self.limits.max_pending_jobs:
                    raise LibraryError("jobs_busy")
                preview = await run_sync(self.store.stage, library_id, filename, data, document_id)
                frozen = {"preview_id": preview["preview_id"], "document_id": preview["document_id"]}
                try:
                    return await self.submit_job("preview", library_id, self._preview_runner(frozen), frozen_input=frozen)
                except BaseException:
                    # A failed registration owns only this newly staged input.
                    # If task cancellation arrived after durable registration,
                    # preserve its reachable job and restart/retry source instead.
                    if not any(record["frozen_input"].get("preview_id") == preview["preview_id"]
                               for record in self.jobs.records.values()):
                        await run_sync(self.store.discard_preview, preview["preview_id"])
                    raise

        # Retain the entire stage -> registration transaction. Shielding only a
        # filesystem thread would discard its returned preview identity when the
        # caller leaves, creating an unreachable input that still consumes quota.
        task = asyncio.create_task(admit(), name="personal-library-preview-admission")
        self._preview_admissions.add(task)
        def finished(completed):
            self._preview_admissions.discard(completed)
            if not completed.cancelled():
                completed.exception()  # Observe failures after a disconnected caller.
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    def _preview_runner(self, frozen: dict) -> Callable:
        async def parse(context: JobContext) -> dict:
            def extract():
                preview = self.store.preview(frozen["preview_id"])
                path = self.store.root / "staging/previews" / f"{preview['preview_id']}.original"
                with path.open("rb") as handle:
                    data = handle.read(self.limits.max_document_bytes + 1)
                if digest(data) != preview["sha256"]:
                    raise LibraryError("preview_changed")
                return self.parser(preview["filename"], data, self.limits)
            parsed = await context.run_sync(extract)
            preview = await context.run_sync(self.store.save_preview, frozen["preview_id"], parsed, context.check_cancelled)
            return {"preview_id": preview["preview_id"], "sha256": preview["sha256"], "filename": preview["filename"]}
        return parse

    async def get_preview(self, preview_id: str) -> dict:
        """Expose a saved preview for explicit confirmation of the original digest."""
        self._ready()
        self._check_preview_input(preview_id)
        result = await run_sync(self.store.preview, preview_id)
        if result["status"] != "ready":
            raise LibraryError("preview_not_ready")
        return result | {"locations": [section["location"] for section in result["sections"]]}

    async def confirm_preview(self, preview_id: str, sha256: str) -> dict:
        """Bind confirmation to the exact saved original and target document version."""
        self._ready()
        async with self._admission:
            preview = await self.get_preview(preview_id)
            if sha256 != preview["sha256"]:
                raise LibraryError("preview_changed")
            frozen = {"preview_id": preview_id, "sha256": sha256, "document_id": preview["document_id"]}
            return await self.submit_job("index", preview["library_id"], self._index_runner(frozen), frozen_input=frozen)

    def _check_preview_input(self, preview_id: str) -> None:
        """Reject new references once an input is being reclaimed or is consumed."""
        checked_id(preview_id)
        if preview_id in self._discarding_previews or any(
            record.get("input_discarded") and record["frozen_input"].get("preview_id") == preview_id
            for record in self.jobs.records.values() if record["kind"] in {"preview", "index"}
        ):
            raise LibraryError("preview_input_discarded")

    async def discard_preview(self, preview_id: str) -> None:
        """Release an abandoned managed preview after its real workers drain."""
        self._ready()
        checked_id(preview_id)
        task = self._preview_reclaims.get(preview_id)
        if task is None or task.done():
            task = asyncio.create_task(self._discard_preview(preview_id), name="personal-library-preview-discard")
            self._preview_reclaims[preview_id] = task
            def finished(completed):
                if self._preview_reclaims.get(preview_id) is completed:
                    self._preview_reclaims.pop(preview_id)
                if not completed.cancelled():
                    completed.exception()  # Observe failure even if the caller left.
            task.add_done_callback(finished)
        await asyncio.shield(task)

    async def _discard_preview(self, preview_id: str) -> None:
        """Invalidate new input users, then wait without blocking an index's cleanup."""
        waiting = []
        async with self._admission:
            self._discarding_previews.add(preview_id)
            for identifier, context in list(self.jobs.contexts.items()):
                if context.record["frozen_input"].get("preview_id") == preview_id:
                    try:
                        await self.jobs.cancel(identifier)
                    except LibraryError as error:
                        if error.code != "job_already_committed":
                            raise
                    waiting.append(context.finished.wait())
        if waiting:
            await asyncio.gather(*waiting)
        await self._reclaim_preview(preview_id)

    async def _reclaim_preview(self, preview_id: str) -> None:
        """Reclaim drained input without waiting on the current index runner itself."""
        # Only application-managed staging is removed. A confirmed document's
        # committed version and the user's external original are independent.
        async with self._admission:
            await run_sync(self.store.discard_preview, preview_id)
            await self.jobs.mark_preview_discarded(preview_id)
            self._discarding_previews.discard(preview_id)

    def _index_runner(self, frozen: dict) -> Callable:
        async def index(context: JobContext) -> dict:
            preview = await context.run_sync(self.store.preview, frozen["preview_id"])
            if preview["sha256"] != frozen["sha256"]:
                raise LibraryError("preview_changed")
            chunks = await context.run_sync(indexing.make_chunks, preview["document_id"], preview["filename"], preview["sections"])
            if len(chunks) > self.limits.max_chunks:
                raise LibraryError("capacity_chunks")
            version = {"document_id": preview["document_id"], "version_id": new_id(), "created_at": now(),
                       "filename": preview["filename"], "sha256": preview["sha256"], "size": preview["size"],
                       "format": preview["format"], "content": preview["content"], "sections": preview["sections"],
                       "chunks": chunks, "embedding_spec": None, "semantic_state": "unavailable"}
            await self._embed(version, context, preview["library_id"])
            result = await context.run_sync(self.store.commit_document, preview, version, context.commit_gate)
            await self._reclaim_preview(preview["preview_id"])
            return result
        return index

    async def _embed(self, version: dict, context: JobContext, library_id: str) -> None:
        """Reuse unchanged compatible vectors; inference failures keep honest keyword-only state."""
        if self.embedder is None:
            return
        try:
            specification = await self.embedder.specification()
            context.check_cancelled()
            version["chunks"] = await indexing.plan_chunks(
                version["chunks"], self.embedder, specification=specification, max_chunks=self.limits.max_chunks,
                check_cancelled=context.check_cancelled,
            )
            version["chunking_version"] = indexing.CHUNKING_VERSION
            old = await context.run_sync(lambda: self.store.documents(self.store.manifest(library_id)))
            reusable = {chunk["input_sha256"]: chunk["vector"] for document in old
                        if document.get("embedding_spec") == specification for chunk in document["chunks"] if chunk.get("vector")}
            version["embedding_spec"] = specification
            pending = []
            for chunk in version["chunks"]:
                chunk["vector"] = reusable.get(chunk["input_sha256"])
                if chunk["vector"] is None:
                    pending.append(chunk)
            for offset in range(0, len(pending), 4):
                context.check_cancelled()
                batch = pending[offset:offset + 4]
                compute = getattr(self.embedder, "embed_background", None)
                texts = [chunk["embedding_text"] for chunk in batch]
                rows = (await compute(texts, expected_spec=specification) if callable(compute)
                        else await self.embedder.embed_batch(texts))
                vectors = await context.run_sync(indexing.checked_vectors, rows, len(batch), specification)
                for chunk, vector in zip(batch, vectors):
                    chunk["vector"] = vector
        except LibraryError as exc:
            if exc.code == "job_cancelled":
                raise
        except Exception:
            pass  # Text is still usable; no invented/default vectors are admitted.
        count = sum(chunk["vector"] is not None for chunk in version["chunks"])
        version["semantic_state"] = "ready" if count == len(version["chunks"]) else "partial" if count else "unavailable"

    async def search(self, library_id: str, query: str, limit: int = 10) -> dict:
        """Search one explicit library without calling a reply model or public knowledge API."""
        self._ready()
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise LibraryError("invalid_query")
        if type(limit) is not int or not 1 <= limit <= 50:
            raise LibraryError("invalid_limit")
        vector = spec = None
        if self.embedder is not None:
            try:
                spec = await self.embedder.specification()
                # Bind the query's actual vector to the same identity used for
                # ranking, independently of shared-cache refresh timing.
                rows = await self.embedder.embed_batch([query], expected_spec=spec)
                vector = (await run_sync(indexing.checked_vectors, rows, 1, spec))[0]
            except Exception:
                vector = spec = None
        return await run_sync(self.store.search, library_id, query, vector, spec, limit)

    async def list_jobs(self) -> list[dict]:
        """Return durable task facts without exposing internal frozen-input records."""
        self._ready()
        return [self.jobs.public(record) for record in reversed(list(self.jobs.records.values()))]

    async def get_job(self, job_id: str) -> dict:
        """Get one task's real state, including interrupted work after restart."""
        self._ready()
        record = self.jobs.records.get(checked_id(job_id))
        if record is None:
            raise LibraryError("job_not_found")
        return self.jobs.public(record)

    async def cancel_job(self, job_id: str) -> dict:
        """Request cancellation while preserving the actual worker's resource ownership."""
        self._ready()
        return await self.jobs.cancel(checked_id(job_id))

    async def retry_job(self, job_id: str) -> dict:
        """Explicitly retry a built-in task using its original frozen preview identity."""
        self._ready()
        async with self._admission:
            record = self.jobs.records.get(checked_id(job_id))
            if record is not None and record["kind"] in {"preview", "index"}:
                self._check_preview_input(record["frozen_input"]["preview_id"])
            if record is None or record["state"] not in {"failed", "cancelled", "interrupted"}:
                raise LibraryError("job_not_retryable")
            frozen = record["frozen_input"]
            if record["kind"] not in {"preview", "index"}:
                raise LibraryError("job_retry_requires_frozen_snapshot")
            runner = self._preview_runner(frozen) if record["kind"] == "preview" else self._index_runner(frozen)
            return await self.submit_job(record["kind"], record["library_id"], runner, frozen_input=frozen)

    async def freeze_library(self, library_id: str, *, version: str = "1", source: dict | None = None,
                             document_ids: list[str] | None = None) -> dict:
        """Freeze one selected library version for an independent package build task."""
        self._ready()
        if not isinstance(version, str) or not version.strip() or len(version) > 100:
            raise LibraryError("invalid_version")
        try:
            version.encode("utf-8")
        except UnicodeError as error:
            raise LibraryError("invalid_version") from error
        source = source if source is not None else {"author": "", "license": "", "url": ""}
        if (not isinstance(source, dict) or set(source) - {"author", "license", "url"}
                or any(not isinstance(value, str) or len(value) > 2000 for value in source.values())):
            raise LibraryError("invalid_source")
        try:
            for value in source.values():
                value.encode("utf-8")
        except UnicodeError as error:
            raise LibraryError("invalid_source") from error
        return await run_sync(self.store.freeze, library_id, version, source, document_ids)

    async def install_snapshot(self, snapshot: dict, *, installation: dict, library_id: str | None = None,
                               context: JobContext | None = None) -> dict:
        """Activate a package validator's complete frozen data, never generating document vectors."""
        self._ready()
        def activate():
            if str(snapshot["version"]) != str(installation["version"]):
                raise LibraryError("package_identity_mismatch")
            if not snapshot["documents"] or len(snapshot["documents"]) > self.limits.max_documents:
                raise LibraryError("capacity_documents")
            if len(snapshot["chunks"]) > self.limits.max_chunks:
                raise LibraryError("capacity_chunks")
            # The package validator owns format and signature checks. Recheck the
            # domain's admitted vector shape without changing the precomputed bytes.
            for chunk in snapshot["chunks"]:
                row = chunk.get("vector")
                indexing.checked_vectors([row], 1, snapshot["embedding_spec"])
                if abs(math.sqrt(sum(value * value for value in row)) - 1) > 0.001:
                    raise LibraryError("invalid_vectors")
            manifest = self.store.install(snapshot, installation, library_id, context.commit_gate if context else None,
                                          context.record["created_at"] if context else None)
            return self._summary(manifest)
        return await (context.run_sync(activate) if context else run_sync(activate))
