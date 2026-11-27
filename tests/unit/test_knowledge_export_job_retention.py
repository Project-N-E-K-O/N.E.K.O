"""Keep completed exports and failed import recovery independently visible.

These regressions retain the original isolated service, fault injection and
cleanup contracts. Shared factories live outside collected test modules.
"""

from __future__ import annotations

import pytest

from services.knowledge.service import KnowledgeService
from tests.knowledge_archive_fixtures import _prepare_export_vectors
from tests.knowledge_fixtures import _finished, _import, _pack

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("restart", [False, True])
async def test_finished_exports_outlive_the_bounded_task_window(tmp_path, restart):
    from services.knowledge.service import MAX_TRACKED_JOBS, ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        export = ImportJob("e" * 32, "demo-memes", "active", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z",
                           kind="pack_export", result={"artifact_id": "b" * 64, "format_version": 2})
        failed_export = ImportJob("f" * 32, "demo-memes", "failed", "2026-01-01T00:00:01Z",
                                  "2026-01-01T00:00:01Z", kind="pack_export")
        for job in (export, failed_export):
            service._remember_job(job)
            await service._persist_job(job)
        for index in range(MAX_TRACKED_JOBS + 5):
            stamp = f"2026-01-02T00:00:{index:02d}Z"
            job = ImportJob(f"{index:032x}", "demo-memes", "active", stamp, stamp)
            service._remember_job(job)
            await service._persist_job(job)
        if restart:
            await service.stop()
            service = KnowledgeService(tmp_path)
            await service.start()
        ids = [job["job_id"] for job in service.list_jobs()]
        # Imports no longer evict exports: the failed export lives in the
        # export window, and the retained export is never evicted.
        assert export.job_id in ids and failed_export.job_id in ids
        assert len(ids) == MAX_TRACKED_JOBS + 2
        # Discarding is not a way to drop a finished export and orphan its ZIP.
        assert await service.discard_job(export.job_id) is False
        assert export.job_id in [job["job_id"] for job in service.list_jobs()]
    finally:
        await service.stop()


async def test_a_real_export_from_a_full_window_keeps_the_oldest_failed_import_retryable(tmp_path):
    from services.knowledge.service import MAX_TRACKED_JOBS, ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        await _import(service, _pack())
        _prepare_export_vectors(service, "demo-memes")
        service._jobs.clear()
        # A full ordinary window whose oldest task is a retryable failed import.
        failed = ImportJob("f" * 32, "demo-memes", "failed", "2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z")
        service._remember_job(failed)
        for index in range(MAX_TRACKED_JOBS - 1):
            stamp = f"2001-01-01T00:{index // 60:02d}:{index % 60:02d}Z"
            service._remember_job(ImportJob(f"a{index:031x}", "demo-memes", "active", stamp, stamp))
        assert len(service._jobs) == MAX_TRACKED_JOBS
        exported = await _finished(service, await service.export_pack("demo-memes"))
        assert exported["state"] == "active"
        assert failed.job_id in [job["job_id"] for job in service.list_jobs()]
    finally:
        await service.stop()


async def test_retained_exports_do_not_crowd_out_recent_failed_imports(tmp_path):
    from services.knowledge.service import MAX_TRACKED_JOBS, ImportJob

    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        for index in range(MAX_TRACKED_JOBS + 3):
            stamp = f"2026-01-01T00:{index // 60:02d}:{index % 60:02d}Z"
            service._remember_job(ImportJob(f"e{index:031x}", "demo-memes", "active", stamp, stamp, kind="pack_export",
                                            result={"artifact_id": f"{index:064x}", "format_version": 2}))
        failed = ImportJob("f" * 32, "demo-memes", "failed", "2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z")
        service._remember_job(failed)
        service._remember_job(ImportJob("a" * 32, "demo-memes", "active", "2026-01-02T00:00:01Z", "2026-01-02T00:00:01Z"))
        ids = [job["job_id"] for job in service.list_jobs()]
        assert failed.job_id in ids and len(ids) == MAX_TRACKED_JOBS + 5
    finally:
        await service.stop()
