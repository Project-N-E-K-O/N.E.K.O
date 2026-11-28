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
"""Pack policy, source management, removal, export, and import entry methods."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import inspect
import json
import hashlib
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Awaitable, Callable
from .models import (
    MATERIAL_TYPES,
    KnowledgePack,
    KnowledgePackError,
    parse_pack,
    decode_pack_bytes,
    pack_sha256,
)
from .registry import PACKS_DIR, PackRecord
from .subscriptions import KnowledgeSubscription, Release, identity_conflict
from .text import title_key
from .importing.projection import project_pack
from .pack_files import retain_revision as _retain_revision
from .jobs import ACTIVE_JOB_STATES, ImportJob, consume_task_result as _consume


class _KnowledgePacksMixin:
    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        from . import service as _svc
        async def run() -> None:
            registry = replace(self._registry, enabled=bool(enabled))
            await self._thread(_svc.save_registry, self.root, registry)
            self._publish_registry(registry)

        await self._locked(run)
        self._index_wakeup.set()
        return {"enabled": bool(enabled)}

    async def set_pack_auto_context(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        record = await self._update_record(pack_id, auto_context=bool(enabled))
        return {"pack_id": pack_id, "auto_context": record.auto_context}

    async def set_pack_enabled(
        self, pack_id: str, enabled: bool, *, expected_pack_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Gate all retrieval while preserving the pack and its vector policy."""
        from . import service as _svc
        async def run() -> tuple[PackRecord, int]:
            record = self._registry.packs.get(pack_id)
            if record is None:
                raise _svc.KnowledgeUnavailable("not_found")
            # An earlier disclosure must not enable a replacement body. Check
            # and save under the same lock, including after waiting for imports.
            # Trusted internal callers retain direct toggles; browser enables
            # always supply an expected hash validated by the HTTP route.
            if enabled and expected_pack_sha256 is not None and record.pack_sha256 != expected_pack_sha256:
                raise _svc.KnowledgeUnavailable("pack_content_changed")
            updated = await self._apply_record_update(pack_id, enabled=bool(enabled))
            # Capture this publication before releasing the mutation lock;
            # another window may publish newer policy before we send a reply.
            return updated, self._registry_snapshot[1]

        record, counter = await self._locked(run)
        # The HTTP envelope reserves enabled for global availability; keep the
        # persisted pack policy distinct so the UI cannot read the other switch.
        return {"pack_id": pack_id, "pack_enabled": record.enabled,
                "policy_revision": {"epoch": self._policy_epoch, "counter": counter}}

    async def set_pack_local_embedding(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        from . import service as _svc
        async def run() -> PackRecord:
            record = await self._apply_record_update(pack_id, local_embedding=bool(enabled))
            if enabled:
                # Turning vectors back on is the user's retry for chunks that
                # failed to embed. The policy is already saved, so a failed
                # reset must not report the change itself as failed.
                try:
                    await self._thread(self._store.reset_attempts, pack_id)
                except Exception:
                    _svc.logger.warning("[Knowledge] could not reset embed attempts of %s", pack_id)
            return record

        record = await self._locked(run)
        self._vector_generation += 1
        self._indexer._schedule_vector_refresh()
        self._index_wakeup.set()
        return {"pack_id": pack_id, "local_embedding": record.local_embedding}

    async def set_pack_material_type(self, pack_id: str, material_type: str | None) -> dict[str, Any]:
        from . import service as _svc
        if material_type is not None and material_type not in MATERIAL_TYPES:
            raise _svc.KnowledgeUnavailable("invalid_request")
        record = await self._update_record(pack_id, material_type_override=material_type)
        return {
            "pack_id": pack_id,
            "material_type_override": record.material_type_override,
            "effective_material_type": record.effective_material_type,
        }

    async def set_entry_disabled(self, pack_id: str, title: str, disabled: bool) -> dict[str, Any]:
        from . import service as _svc
        async def run() -> dict[str, Any]:
            record = self._registry.packs.get(pack_id)
            if record is None:
                raise _svc.KnowledgeUnavailable("not_found")
            key = title_key(title)
            was_disabled = key in record.disabled_titles
            found = await self._thread(self._store.set_disabled, pack_id, title, bool(disabled))
            if not found:
                raise _svc.KnowledgeUnavailable("not_found")
            keys = set(record.disabled_titles)
            if disabled:
                keys.add(key)
            else:
                keys.discard(key)
            updated = replace(record, disabled_titles=tuple(sorted(keys)), updated_at=_svc.utc_now())
            registry = self._registry.with_pack(updated)
            try:
                await self._thread(_svc.save_registry, self.root, registry)
            except BaseException:
                # The registry still says the old thing; so must the index.
                await self._thread(self._store.set_disabled, pack_id, title, was_disabled)
                raise
            self._publish_registry(registry)
            return {"pack_id": pack_id, "disabled": bool(disabled), "disabled_entries": len(keys)}

        return await self._locked(run)

    async def remove_pack(self, pack_id: str, *, _retain_sources: bool = False) -> dict[str, Any]:
        """Remove the pack row; only last-document removal keeps visible history."""
        # Nothing is marked up front, so a failed removal has nothing to undo.
        # While this removal is pending, earlier imports of the pack yield (one
        # may be holding the lock this removal waits for); once it succeeds,
        # every import that arrived before it stands down. Imports that
        # arrived after it are left alone: they are the newer request.
        from . import service as _svc
        if pack_id not in self._registry.packs:
            # Not installed (a first import may still be building): there is
            # nothing this removal could commit, so it must not make that
            # import yield either.
            raise _svc.KnowledgeUnavailable("not_found")

        def decide(record: PackRecord | None) -> tuple[PackRecord | None, str]:
            if record is None:
                raise _svc.KnowledgeUnavailable("not_found")
            return record, ""

        return await self._remove(pack_id, None, decide, retain_sources=_retain_sources)

    async def remove_subscription(
        self, pack_id: str, identity: tuple[str, str, str], releases: frozenset[Release]
    ) -> dict[str, Any]:
        """Unsubscribe: remove the named releases of a market package, installed or on the way.

        One request settles it all under the write lock: imports of the pack
        that arrived before this request and carry one of ``releases`` stand
        down (queued, building, or still being admitted and not yet a job),
        and the installed pack is removed if it is one of those releases.

        Safe to repeat: a pack that is not installed, or that is installed as
        a release not named here (a later subscription), is left alone and
        reported as success. A pack that is there under another identity (a
        local pack, another market package) is an error, as is any import of
        this package under another pack id.

        The lock is waited for as long as any request waits for it
        (``WRITE_LOCK_TIMEOUT_SECONDS``): a large import committing past its
        point of no return can hold it longer, and then the answer is
        ``knowledge_busy``, for the caller to repeat the same request.
        """
        self._require_ready()
        self._check_unsubscribe_target(pack_id, identity)

        def decide(record: PackRecord | None) -> tuple[PackRecord | None, str]:
            # Checked again under the lock: an import may have landed since.
            self._check_unsubscribe_target(pack_id, identity)
            if record is None:
                return None, "not_installed"
            if record.subscription is None or record.subscription.release not in releases:
                return None, "other_release"
            return record, ""

        result = await self._remove(pack_id, releases, decide)
        return {**result, "removed": not result.get("kept")}

    def _check_unsubscribe_target(self, pack_id: str, identity: tuple[str, str, str]) -> None:
        from . import service as _svc
        record = self._registry.packs.get(pack_id)
        if record is not None and (record.subscription is None or record.subscription.identity != identity):
            raise _svc.KnowledgeUnavailable("subscription_identity_mismatch")
        provider, package_id = identity[0], identity[1]
        claims = [
            (subscription.pack_id, subscription.identity)
            for subscription in (
                *(item.subscription for item in self._registry.packs.values()),
                *(job.subscription for job in self._jobs.values() if job.state in ACTIVE_JOB_STATES),
                *self._incoming.values(),
            )
            if subscription is not None
            and subscription.provider == provider
            and subscription.provider_package_id == package_id
        ]
        # The whole identity: a removal for an old remote id must not match
        # an import of the package that is not installed yet either.
        if any(claim != (pack_id, identity) for claim in claims):
            raise _svc.KnowledgeUnavailable("subscription_identity_mismatch")

    async def _remove(
        self,
        pack_id: str,
        releases: frozenset[Release] | None,
        decide: Callable[[PackRecord | None], tuple[PackRecord | None, str]
                         | Awaitable[tuple[PackRecord | None, str]]],
        *,
        retain_sources: bool = False,
        arrived_at: int | None = None,
    ) -> dict[str, Any]:
        """Remove ``pack_id`` as ``decide`` rules under the lock, overriding the
        imports it covers (``releases``; ``None`` covers every import of the pack).

        Use the caller's original arrival, or reserve one before any await.
        """
        # Last-source removal already waited for its initial selection lock;
        # taking another number here would overtake imports that arrived later.
        from . import service as _svc
        my_seq = self._reserve_arrival() if arrived_at is None else arrived_at
        removal = (my_seq, releases)
        self._pending_removals[pack_id] = self._pending_removals.get(pack_id, frozenset()) | {removal}

        async def run() -> dict[str, Any]:
            decided = decide(self._registry.packs.get(pack_id))
            # An async rule re-reads what it needs under this same lock.
            record, kept = await decided if inspect.isawaitable(decided) else decided
            registry = self._registry.without_pack(pack_id) if record is not None else None
            if registry is not None:
                # Commit the history-list preference before removing the active
                # registry entry. A failure leaves that active row authoritative;
                # no post-commit sidecar error can turn removal into a failure.
                await self._thread(self._sources.set_retained, pack_id, retain_sources)
                await self._thread(_svc.save_registry, self.root, registry)
            marks = dict(self._removed_at.get(pack_id, {}))
            for key in releases if releases is not None else (None,):
                marks[key] = max(marks.get(key, 0), my_seq)
            self._removed_at[pack_id] = marks
            for job in list(self._jobs.values()):
                if (
                    job.pack_id == pack_id
                    and job.state == "queued"
                    and job.arrived_at < my_seq
                    and _svc._covers(releases, job.subscription)
                ):
                    job.cancel_requested = True
                    self._finish_job(job, "cancelled")
                    self._unqueue(job.job_id)
                    await self._discard_staging(job.job_id)
                    with contextlib.suppress(OSError):
                        await self._persist_job(job)
            if record is None or registry is None:
                return {"pack_id": pack_id, "removed_entries": 0, "kept": kept}
            file_name = record.file_name

            def cleanup() -> None:
                # Best effort: the removal is committed by the registry write.
                # The startup reconcile drops leftover index rows; a pack file
                # left behind is retained and no longer counts toward capacity.
                try:
                    self._store.delete_pack(pack_id)
                except Exception as exc:
                    _svc.logger.warning("[Knowledge] index cleanup of %s failed (%s)", pack_id, type(exc).__name__)
                try:
                    original = self.root / PACKS_DIR / file_name
                    if original.is_file():
                        # Pack removal changes the active registry selection;
                        # retain its immutable revision before dropping a name.
                        _retain_revision(self.root / "history" / pack_id / file_name,
                                         _svc._read_bounded(original), record.pack_sha256)
                        original.unlink(missing_ok=True)
                except OSError:
                    _svc.logger.warning("[Knowledge] could not delete the file of %s", pack_id)

            self._broken_packs = tuple(p for p in self._broken_packs if p != pack_id)
            self._publish_registry(registry)
            self._vector_generation += 1
            await self._thread(cleanup)
            return {"pack_id": pack_id, "removed_entries": record.entries}

        try:
            result = await self._locked(run)
        finally:
            pending = self._pending_removals.get(pack_id, frozenset()) - {removal}
            if pending:
                self._pending_removals[pack_id] = pending
            else:
                self._pending_removals.pop(pack_id, None)
            settled, self._removal_settled = self._removal_settled, asyncio.Event()
            settled.set()
        self._indexer._schedule_vector_refresh()
        return result

    def _identity_conflict(self, pack_id: str, subscription: KnowledgeSubscription | None) -> bool:
        """Whether installing this would break the rules in ``services.knowledge.subscriptions``."""
        installed = {key: record.subscription for key, record in self._registry.packs.items()}
        if identity_conflict(pack_id, subscription, installed):
            return True
        # An import of another pack may already be installing this package.
        return subscription is not None and any(
            job.state in ACTIVE_JOB_STATES
            and job.pack_id != pack_id
            and job.subscription is not None
            and job.subscription.provider_package_id == subscription.provider_package_id
            for job in self._jobs.values()
        )

    def _removal_committed_over(
        self, pack_id: str, subscription: KnowledgeSubscription | None, arrived_at: int
    ) -> bool:
        """Whether a committed removal that arrived after this import covers it."""
        marks = self._removed_at.get(pack_id, {})
        latest = marks.get(None, -1)
        if subscription is not None:
            latest = max(latest, marks.get(subscription.release, -1))
        return latest > arrived_at

    def _removal_pending_over(self, job: ImportJob) -> bool:
        """Whether a removal still waiting, that arrived after this import, covers it."""
        from . import service as _svc
        return any(
            seq > job.arrived_at and _svc._covers(releases, job.subscription)
            for seq, releases in self._pending_removals.get(job.pack_id, ())
        )

    def _superseded(self, job: ImportJob) -> bool:
        """Whether a removal of the job's pack overrides this import."""
        return (
            job.cancel_requested
            or self._removal_pending_over(job)
            or self._removal_committed_over(job.pack_id, job.subscription, job.arrived_at)
        )

    def _sources_managed(self, pack_id: str) -> bool:
        """Only local originals that still project the current pack are editable."""
        if not self._sources.has_history(pack_id):
            return False
        try:
            self._source_pack(pack_id)
            return True
        except (KnowledgePackError, OSError, ValueError):
            return False

    def _listed_sources_managed(self, pack_id: str) -> bool:
        """Cache listing hints; modifying operations still validate full source authority."""
        record = self._registry.packs.get(pack_id)
        if record is None or not self._sources.has_history(pack_id):
            return False
        folder = self.root / "sources" / pack_id
        try:
            paths = [self.root / PACKS_DIR / record.file_name, folder / "manifest.json", folder / "versions"]
            paths.extend(sorted((folder / "versions").iterdir()))
            signature = (record.pack_sha256, *(
                (str(path), info.st_mtime_ns, info.st_ctime_ns, info.st_size)
                for path in paths for info in (path.stat(),)
            ))
        except OSError:
            return False
        cached = self._source_capabilities.get(pack_id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        managed = self._sources_managed(pack_id)
        self._source_capabilities[pack_id] = (signature, managed)
        return managed

    def _source_pack(self, pack_id: str) -> KnowledgePack | None:
        """Read the active source projection, never an uncommitted sidecar."""
        from . import service as _svc
        record = self._registry.packs.get(pack_id)
        if record is None:
            return None
        if not self._sources.has_history(pack_id):
            raise KnowledgePackError("invalid_request")
        raw = _svc._read_bounded(self.root / PACKS_DIR / record.file_name)
        if pack_sha256(raw) != record.pack_sha256:
            raise KnowledgePackError("pack_file_mismatch")
        pack = decode_pack_bytes(raw)
        self._source_documents(pack, pack_id)
        return pack

    def _source_documents(self, pack: KnowledgePack, pack_id: str) -> list[dict]:
        """Validate source authority before returning the exact frozen selection."""
        from . import service as _svc
        documents = self._sources.documents(pack, pack_id)
        projection = project_pack(pack_id, pack.source.name, documents)
        expected = parse_pack(json.loads(_svc.canonical_pack_bytes(projection)))
        if pack.entries != expected.entries:
            # Existing personal packs used hard windows before word-aware
            # projection. Verify that exact historical rendering too, without
            # rewriting originals or granting unrelated offline packs access.
            legacy = project_pack(pack_id, pack.source.name, documents, legacy_windows=True)
            if pack.entries != parse_pack(json.loads(_svc.canonical_pack_bytes(legacy))).entries:
                raise KnowledgePackError("invalid_request")
        return documents

    async def preview_source(self, filename: str, raw: bytes | Path, *, pack_id: str = "", doc_id: str = "", name: str = "") -> dict[str, Any]:
        """Parse an original without installing it or requesting model loading."""
        self._require_ready()
        if pack_id:
            record = self._registry.packs.get(pack_id)
            if record is None or not await self._thread(self._sources_managed, pack_id):
                raise KnowledgePackError("invalid_request")
            name = record.source.name
        value = await self._thread(self._sources.preview, filename, raw, pack_id=pack_id, doc_id=doc_id, name=name)
        return {"ok": True, "pack_id": value["pack_id"], "preview": {
            key: value[key] for key in ("preview_id", "doc_id", "sha256", "name", "content", "chars", "sections")
        }}

    async def list_sources(self, pack_id: str) -> dict[str, Any]:
        """List current source selections, retaining all immutable past versions."""
        def read():
            if not self._sources_managed(pack_id):
                return []  # Finished packages contain no editable originals.
            pack = self._source_pack(pack_id)
            documents = self._sources.documents(pack, pack_id)
            active = {row["doc_id"]: row for row in documents}
            return [{"doc_id": row["doc_id"], "name": active.get(row["doc_id"], row)["name"],
                     "current_version": active[row["doc_id"]]["version"] if row["doc_id"] in active else None,
                     "removed": row["doc_id"] not in active,
                     "versions_count": len(self._sources.versions(pack_id, row["doc_id"])),
                     "chars": active.get(row["doc_id"], row)["chars"], "updated_at": row["created_at"]}
                    for row in self._sources.all_documents(pack_id)]
        return {"ok": True, "pack_id": pack_id, "sources": await self._thread(read)}

    async def source_versions(self, pack_id: str, doc_id: str) -> dict[str, Any]:
        """Expose version metadata only; original paths never cross the API."""
        def read():
            if not self._sources_managed(pack_id):
                return []
            active = self._sources.documents(self._source_pack(pack_id), pack_id)
            current = next((row["version"] for row in active if row["doc_id"] == doc_id), None)
            return [{**{key: row[key] for key in ("version", "name", "chars", "sha256", "created_at")},
                     "current": row["version"] == current} for row in self._sources.versions(pack_id, doc_id)]
        return {"ok": True, "pack_id": pack_id, "doc_id": doc_id, "versions": await self._thread(read)}

    async def _project_sources(
        self, pack_id: str, name: str, documents: list[dict], *, enabled: bool = False,
        source_confirmation: bool | None = None, source_target: tuple[str, int] | None = None,
        arrived_at: int | None = None,
    ) -> dict[str, Any]:
        """Send a source projection through the one existing import runner."""
        from . import service as _svc
        pack = await self._thread(project_pack, pack_id, name, documents)
        # v1 validation is reused before staging; no source-specific bypass of
        # entry, chunk or total pack budgets is admitted.
        canonical = await self._thread(lambda: _svc.canonical_pack_bytes(parse_pack(json.loads(_svc.canonical_pack_bytes(pack)))))
        # The canonical pack's doc:/ver: tags already freeze the exact selection
        # in the job's .pack.json. Repeating it here made task records grow
        # with the document count beyond what restart recovery reads back.
        frozen: dict[str, Any] = {}
        if source_confirmation is not None:
            # Consent is independent of the requested initial enabled flag;
            # another window can change that flag before this task activates.
            frozen.update(source_confirmation_required=True, enabled_confirmed=source_confirmation)
            if source_target is not None:
                # One acknowledgement covers this operation's selected version,
                # not other documents retained in the frozen whole-pack input.
                frozen["source_target"] = {"doc_id": source_target[0], "version": source_target[1]}
        return await self.import_pack(canonical, _options={"kind": "import_commit", "origin": "imported", "enabled": enabled,
                                                           "frozen_input": frozen}, _arrived_at=arrived_at)

    async def discard_preview(self, preview_id: str) -> dict[str, Any]:
        """Delete a cancelled or replaced preview; committed ones are kept.

        Shares the write lock with ``commit_source`` so a concurrent commit
        either froze the preview first (kept) or finds it already gone.
        """
        deleted = await self._locked(lambda: self._thread(self._sources.discard_preview, preview_id))
        return {"deleted": deleted}

    async def commit_source(
        self, preview_id: str, sha256: str, *, enabled: bool = False, enabled_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Retain the frozen original, then enqueue a disabled-by-default pack."""
        # Reserve before waiting for the source lock, so later pack removals
        # supersede this request even when staging finishes after deletion.
        arrived_at = self._reserve_arrival()
        async def run():
            preview = await self._thread(self._sources.find_preview, preview_id)
            # Check append intent under the same write lock, before retaining
            # a version. Legacy previews lack this field and keep their prior
            # creation semantics; no existing preview or history is migrated.
            if preview.get("requires_existing_pack", False) and preview["pack_id"] not in self._registry.packs:
                raise KnowledgePackError("source_not_found")
            value = await self._thread(self._sources.retain_preview, preview_id, sha256)
            pack_id = value["pack_id"]
            pack = await self._thread(self._source_pack, pack_id)
            documents = await self._thread(self._sources.documents, pack, pack_id)
            documents = [row for row in documents if row["doc_id"] != value["doc_id"]] + [value]
            return await self._project_sources(pack_id, value["pack_name"], documents, enabled=enabled,
                                               source_confirmation=enabled_confirmed,
                                               source_target=(value["doc_id"], value["version"]), arrived_at=arrived_at)
        return await self._locked(run)

    async def remove_source(self, pack_id: str, doc_id: str) -> dict[str, Any]:
        """Remove an active selection without deleting its original or history."""
        # Both a projection and last-source removal retain the original order.
        from . import service as _svc
        arrived_at = self._reserve_arrival()
        async def selection() -> list[dict]:
            pack = await self._thread(self._source_pack, pack_id)
            if pack is None:
                raise KnowledgePackError("source_not_found")
            documents = await self._thread(self._sources.documents, pack, pack_id)
            retained = [row for row in documents if row["doc_id"] != doc_id]
            if len(retained) == len(documents):
                raise KnowledgePackError("source_not_found")
            if not retained and (pack_id in self._admitting or any(
                    job.pack_id == pack_id and job.kind != "pack_export" and job.state in ACTIVE_JOB_STATES
                    for job in self._jobs.values())):
                # Removing the last active document removes the pack, which
                # would cancel an append of another document still waiting.
                raise KnowledgePackError("job_in_progress")
            return retained

        for _attempt in range(3):
            async def run():
                retained = await selection()
                if not retained:
                    return None
                pack = await self._thread(self._source_pack, pack_id)
                return await self._project_sources(pack_id, pack.source.name, retained, arrived_at=arrived_at)
            result = await self._locked(run)
            if result is not None:
                return result
            if pack_id not in self._registry.packs:
                raise KnowledgePackError("source_not_found")

            async def decide(record: PackRecord | None) -> tuple[PackRecord | None, str]:
                # Checked again under the removal's own lock: an append that
                # took the lock in between must not be removed with A, and
                # must not make A reappear after this removal.
                if record is None:
                    raise KnowledgePackError("source_not_found")
                if await selection():
                    raise _svc._SelectionChanged()
                return record, ""

            try:
                # An empty schema-v1 pack is invalid. Removing its last source
                # uses the existing pack-removal ordering and keeps revisions.
                await self._remove(pack_id, None, decide, retain_sources=True, arrived_at=arrived_at)
                break
            except _svc._SelectionChanged:
                continue  # Another document became active; remove only this one.
        else:
            raise KnowledgePackError("job_in_progress")
        stamp = _svc.utc_now()
        job = ImportJob(uuid.uuid4().hex, pack_id, "active", stamp, stamp, kind="import_commit")
        self._remember_job(job)
        with contextlib.suppress(OSError):
            await self._persist_job(job)
        return {"ok": True, **job.to_json()}

    async def restore_source(
        self, pack_id: str, doc_id: str, version: int, *, enabled_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Select an immutable past version through the same pack import path."""
        # Restoring history must not overtake a later pack removal either.
        arrived_at = self._reserve_arrival()
        async def run():
            value = await self._thread(self._sources.version, pack_id, doc_id, version)
            pack = await self._thread(self._source_pack, pack_id)
            documents = await self._thread(self._sources.documents, pack, pack_id)
            documents = [row for row in documents if row["doc_id"] != doc_id] + [value]
            # Retained history can include never-activated, unconfirmed text.
            # Freeze consent for the existing activation-time enabled check.
            return await self._project_sources(pack_id, pack.source.name if pack else value["pack_name"], documents,
                                               source_confirmation=enabled_confirmed,
                                               source_target=(value["doc_id"], value["version"]), arrived_at=arrived_at)
        return await self._locked(run)

    async def _import_archive_input(
        self, raw: bytes | Path, *, subscription: KnowledgeSubscription | None, options: dict,
        arrived_at: int, parse_work: list[concurrent.futures.Future[Any]],
    ) -> dict:
        """Validate ZIP files off-loop and keep their exact bytes for recovery."""
        from . import service as _svc
        from .archive import read_archive

        async def clean_input(path, running):
            # Cancellation cannot stop a queued native reader/writer. Its
            # scratch belongs to those real futures until they have ended.
            if running:
                await asyncio.gather(*(asyncio.wrap_future(future) for future in running), return_exceptions=True)
                running.clear()
            with contextlib.suppress(OSError):
                await self._thread(path.unlink, missing_ok=True)

        temporary = None
        try:
            if isinstance(raw, bytes):
                folder = self.root / _svc.STAGING_DIR
                await self._thread(folder.mkdir, parents=True, exist_ok=True)
                temporary = folder / (uuid.uuid4().hex + ".upload.zip")
                await self._parse_thread(parse_work, _svc.atomic_write_bytes, temporary, raw)
                path = temporary
            else:
                path = raw
            def magic():
                with path.open("rb") as stream:
                    return stream.read(4)
            if await self._parse_thread(parse_work, magic) != b"PK\x03\x04":
                return await self._import_parsed(
                    await self._parse_thread(parse_work, _svc._read_bounded, path), arrived_at, parse_work, subscription, options,
                )
            spec = await self._thread(getattr(self.embedder, "spec", lambda: None))
            checked = await self._parse_thread(parse_work, lambda: read_archive(path, expected_spec=spec))
            digest = checked["artifact_sha256"]
            if subscription is not None and digest != subscription.artifact_sha256:
                return {"ok": False, "reason": "artifact_hash_mismatch"}
            if subscription is not None:
                staged = self._staged_subscription_job(subscription)
                if staged is not None:
                    return {"ok": True, **staged.to_json(), "existing": True}
            def retain():
                output = self.root / "artifacts" / (digest + ".neko-knowledge.zip")
                existed = output.exists()
                output.parent.mkdir(parents=True, exist_ok=True)
                stage = output.with_suffix(".tmp")
                created_stage = False
                try:
                    with path.open("rb") as source, stage.open("wb") as target:
                        created_stage = True
                        _svc.shutil.copyfileobj(source, target, 64 * 1024)
                        target.flush()
                        import os
                        os.fsync(target.fileno())
                    hasher = hashlib.sha256()
                    with stage.open("rb") as source:
                        for block in iter(lambda: source.read(64 * 1024), b""):
                            hasher.update(block)
                    if hasher.hexdigest() != digest:
                        raise KnowledgePackError("artifact_hash_mismatch")
                    stage.replace(output)
                    return not existed
                finally:
                    # Only this temporary copy is ours to clean. Never delete
                    # the retained target or mask the original I/O failure.
                    if created_stage:
                        with contextlib.suppress(OSError):
                            stage.unlink(missing_ok=True)
            async def retain_and_admit():
                # Export deletion uses the same mutation lock. Keep the exact
                # ZIP alive until its import job owns the recovery reference;
                # otherwise deletion could slip between retaining and admission.
                created = await self._thread(retain)
                accepted = False
                try:
                    canonical = await self._thread(lambda: _svc.canonical_pack_bytes(parse_pack(checked["pack"])))
                    options.update(archive_sha256=digest, artifact_sha256=digest, seed_vectors=checked["seed_vectors"])
                    result = await self._import_parsed(canonical, arrived_at, parse_work, subscription, options)
                    accepted = bool(result.get("ok"))
                    return result
                finally:
                    if created and not accepted:
                        # This copy never became an accepted user artifact.
                        # Preserve pre-existing files and every durable task
                        # reference; cleanup cannot change the refusal result.
                        async with self._job_persist_lock:
                            records = [self._job_record(job) for job in self._jobs.values()]
                            def cleanup():
                                if not self._archive_referenced_blocking(digest, records):
                                    (self.root / "artifacts" / (digest + ".neko-knowledge.zip")).unlink(missing_ok=True)
                            with contextlib.suppress(OSError):
                                await self._thread(cleanup)

            # Transfer before the next await, even if the owner has not yet
            # started. The original admission reservation remains visible and
            # counted until its final filesystem work and cleanup have ended.
            owned_temporary = temporary
            completion: concurrent.futures.Future[None] = concurrent.futures.Future()
            completion.set_running_or_notify_cancel()
            parse_work.append(completion)
            self._admission_owned.add(arrived_at)

            async def own_retention():
                try:
                    return await self._locked(retain_and_admit, wait=True)
                finally:
                    try:
                        if owned_temporary is not None:
                            await clean_input(owned_temporary, [])
                    finally:
                        completion.set_result(None)

            owner = asyncio.create_task(own_retention(), name="knowledge-archive-import")
            self._detached.add(owner)
            owner.add_done_callback(self._detached.discard)
            owner.add_done_callback(lambda _done: self._admission_settled(arrived_at))
            owner.add_done_callback(_consume)
            temporary = None
            return await asyncio.shield(owner)
        finally:
            if temporary is not None:
                running = [future for future in parse_work if not future.done()]
                cleanup = asyncio.create_task(clean_input(temporary, running), name="knowledge-archive-input-cleanup")
                self._own_background_task(cleanup)
                if not running:
                    await asyncio.shield(cleanup)

    async def export_pack(self, pack_id: str, *, format_version: int = 2,
                          _retry_version: str | None = None) -> dict[str, Any]:
        """Delegate export work while retaining the shared runtime owner."""
        return await self._exports.export_pack(pack_id, format_version=format_version, _retry_version=_retry_version)

    def artifact_path(self, artifact_id: str) -> Path:
        """Delegate export work while retaining the shared runtime owner."""
        return self._exports.artifact_path(artifact_id)

    async def artifact_download(self, artifact_id: str) -> tuple[Path, str]:
        """Delegate export work while retaining the shared runtime owner."""
        return await self._exports.artifact_download(artifact_id)

    async def import_pack(
        self, raw: bytes | Path, *, subscription: KnowledgeSubscription | None = None,
        _options: dict[str, Any] | None = None, _arrived_at: int | None = None,
    ) -> dict[str, Any]:
        """Import a pack; ``subscription`` marks a verified plugin-market artifact."""
        self._require_ready()
        options = dict(_options or {})
        # Parsing a large pack holds several copies of it in memory: refuse
        # before parsing when imports are already at their limit, and never
        # parse more than that many at once.
        if self.import_busy():
            # A repeat of an apply whose import is queued or building is still
            # answered with that import: the slots may be full of exactly it.
            staged = await self._staged_repeat(raw, subscription) if subscription is not None else None
            return staged or {"ok": False, "reason": "knowledge_busy"}
        # Source operations already reserved their order before waiting for
        # the write lock; ordinary imports and explicit retries reserve here.
        arrived_at = self._reserve_arrival() if _arrived_at is None else _arrived_at
        # The reservation lasts until the request is answered (including the
        # unchanged-file check, which reads from disk) or admitted, so parsed
        # copies of a pack never pile up uncounted.
        self._parsing += 1
        parse_work: list[concurrent.futures.Future[Any]] = []
        deferred = False
        if subscription is not None:
            self._incoming[arrived_at] = subscription
        try:
            # ZIP verification belongs to the original arrival and admission
            # slot. Recursing after verification would let an earlier upload
            # overtake an unsubscribe and lose bounded parse accounting.
            if isinstance(raw, Path) or raw.startswith(b"PK\x03\x04"):
                return await self._import_archive_input(
                    raw, subscription=subscription, options=options,
                    arrived_at=arrived_at, parse_work=parse_work,
                )
            return await self._import_parsed(raw, arrived_at, parse_work, subscription, options or None)
        except asyncio.CancelledError:
            # A cancelled request stops waiting, but its parse thread runs on
            # with the pack in memory: the slot is freed when that ends.
            running = [future for future in parse_work if not future.done()]
            if running:
                deferred = True
                loop = asyncio.get_running_loop()
                running[0].add_done_callback(
                    lambda _future: loop.call_soon_threadsafe(self._release_parse_slot)
                )
            raise
        finally:
            if not deferred:
                self._parsing -= 1
            if arrived_at not in self._admission_owned:
                self._incoming.pop(arrived_at, None)
