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


"""Create, resolve and explicitly remove completed knowledge exports.

ExportManager uses the service's mutation/persistence locks, admission budget
and tracked task owners. Snapshot validation stays inside the existing lock;
the archive publication gate stays exclusive with cancellation. Neither file
retention nor job persistence is independently owned by this module.
The runtime admits and queues the frozen job; this module retains package
validation, the cancellation/publication gate and explicit artifact operations.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .jobs import ImportJob
from .models import KnowledgePackError, pack_sha256
from .pack_files import read_pack_bytes
from .registry import PACKS_DIR, utc_now

if TYPE_CHECKING:
    from .service import KnowledgeService


class ExportManager:
    """Coordinate exports against the original runtime and task records."""

    def __init__(self, runtime: KnowledgeService) -> None:
        self._runtime = runtime

    async def export_pack(self, pack_id: str, *, format_version: int = 2,
                          _retry_version: str | None = None) -> dict[str, Any]:
        """Queue v2 with a server-owned release version, frozen across retries."""
        self._runtime._require_ready()
        # Retired clients may still send a format selector. Refuse v1 instead
        # of silently producing a different file from the one they requested.
        if (type(format_version) is not int or format_version != 2
                or (_retry_version is not None and not isinstance(_retry_version, str))):
            raise KnowledgePackError("invalid_request")
        # Shared admission reads and writes stay with the runtime's one budget.
        record = self._runtime._export_admission_record(pack_id)
        stamp = utc_now()
        job_id = uuid.uuid4().hex
        # Reuse the existing unique task identity instead of maintaining a
        # counter that could reset after task cleanup, restore or pack import.
        # Persist this value before queueing; only retry_job supplies an old one.
        version = _retry_version if _retry_version is not None else f"auto-{job_id}"
        job = ImportJob(job_id, pack_id, "queued", stamp, stamp,
                        kind="pack_export", staged_sha256=record.pack_sha256,
                        export_version=version)
        return await self._runtime._admit_export(job)

    async def run(self, job: ImportJob) -> None:
        """Snapshot under the mutation lock, then serialize after releasing it."""
        from .archive import export_archive

        # Durable jobs are another input boundary: an old text-export task
        # cannot bypass admission by reaching the worker or retry path.
        if type(job.export_format) is not int or job.export_format != 2:
            raise KnowledgePackError("invalid_request")

        async def snapshot():
            record = self._runtime._registry.packs.get(job.pack_id)
            if record is None or record.pack_sha256 != job.staged_sha256:
                raise KnowledgePackError("pack_changed")
            def read():
                # Direct exports and retries must snapshot the registered,
                # healthy revision, just like the list's export-ready flag.
                if (job.pack_id in self._runtime._broken_packs
                        or self._runtime._store.pack_versions().get(job.pack_id) != record.pack_sha256):
                    raise KnowledgePackError("vectors_not_ready")
                # Metadata can change without invalidating stored text/vectors.
                # Check and decode the same bytes of the registered revision.
                raw = read_pack_bytes(self._runtime.root / PACKS_DIR / record.file_name)
                if pack_sha256(raw) != record.pack_sha256:
                    raise KnowledgePackError("pack_file_mismatch")
                payload = json.loads(raw)
                # Export the unique stored identity, even when another encoder
                # is loaded or local inference is unavailable or switched off.
                spec = self._runtime._store.vector_specification(None, pack_id=job.pack_id)
                vectors = self._runtime._store.vectors_for_pack(job.pack_id, spec["model_id"]) if spec else []
                return payload, spec, vectors
            return await self._runtime._thread(read)

        payload, spec, vectors = await self._runtime._locked(snapshot, wait=True)
        def check():
            if self._runtime._superseded(job) or self._runtime._stopping:
                raise InterruptedError("cancelled")
        def commit_gate():
            # The archive calls this immediately before publication. Keep
            # accepting cancellation through fsync, then make that decision
            # exclusive with cancel_job rather than waiting for thread return.
            with job.gate:
                if self._runtime._superseded(job) or self._runtime._stopping:
                    return False
                job.committed = True
                return True
        async def publish():
            # The runner may stop waiting after the publication gate. Keep
            # one owner for the native result and its durable download record.
            job.result = await self._runtime._thread(export_archive, self._runtime.root / "artifacts", payload,
                                            version=job.export_version, specification=spec,
                                            vectors=vectors, created_at=job.created_at,
                                            check_cancelled=check, commit_gate=commit_gate)
            job.published = True
            if self._runtime._stopping:
                self._runtime._finish_job(job, "active")
                await self._runtime._persist_job(job)

        owner = asyncio.create_task(publish(), name="knowledge-export-publication")
        self._runtime._own_background_task(owner)
        await asyncio.shield(owner)

    def artifact_path(self, artifact_id: str) -> Path:
        """Resolve only retained SHA identities; never accept an arbitrary path."""
        import re

        if not isinstance(artifact_id, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise KnowledgePackError("invalid_request")
        folder = self._runtime.root / "artifacts"
        # The download surface serves complete packages only. Previously
        # exported JSON files stay on disk without reviving their old route.
        path = folder / (artifact_id + ".neko-knowledge.zip")
        if path.is_file() and not path.is_symlink() and path.resolve().parent == folder.resolve():
            return path
        raise KnowledgePackError("not_found")

    async def artifact_download(self, artifact_id: str) -> tuple[Path, str]:
        """Resolve artifact bytes and their readable download metadata off-loop."""
        from .archive import archive_download_filename

        # Snapshot retained job metadata before entering a thread. The archive
        # supplies its frozen name even if today's pack has changed or vanished.
        created_at = next((job.created_at for job in self._runtime._jobs.values()
                           if job.kind == "pack_export" and isinstance(job.result, dict)
                           and job.result.get("artifact_id") == artifact_id), None)

        def read():
            path = self.artifact_path(artifact_id)
            return path, archive_download_filename(path, created_at=created_at)

        return await self._runtime._thread(read)

    async def delete_export(self, job_id: str) -> dict[str, Any]:
        """Remove one completed local export, retaining any shared recovery ZIP."""
        import re

        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise KnowledgePackError("invalid_request")

        async def run():
            job = self._runtime._jobs.get(job_id)
            if (job is None or job.kind != "pack_export" or job.state != "active"
                    or not isinstance(job.result, dict)):
                raise KnowledgePackError("invalid_request")
            artifact_id = job.result.get("artifact_id")
            if not isinstance(artifact_id, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
                raise KnowledgePackError("invalid_request")

            async with self._runtime._job_persist_lock:
                records = [self._runtime._job_record(other) for other in self._runtime._jobs.values() if other.job_id != job_id]

                def remove():
                    protected = self._runtime._archive_referenced_blocking(artifact_id, records, exclude_job_id=job_id)
                    path = self._runtime.root / "artifacts" / (artifact_id + ".neko-knowledge.zip")
                    if not protected:
                        if path.is_symlink():
                            raise KnowledgePackError("invalid_request")
                        # Missing files are already cleaned. If the later task
                        # unlink fails, preserve the card and allow a retry to
                        # finish rather than report success before persistence.
                        path.unlink(missing_ok=True)
                    (self._runtime.root / "jobs" / (job_id + ".json")).unlink(missing_ok=True)
                    return protected

                retained = await self._runtime._thread(remove)
                job.export_deleted = True
                self._runtime._jobs.pop(job_id, None)
                return {"ok": True, "job_id": job_id, "artifact_retained": retained}

        return await self._runtime._locked(run)
