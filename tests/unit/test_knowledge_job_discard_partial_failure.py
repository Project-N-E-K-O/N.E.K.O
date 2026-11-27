"""Keep failed discard steps visible without claiming their input was deleted."""

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from services.knowledge.models import KnowledgePackError
from services.knowledge.service import ImportJob, KnowledgeService
from tests.unit.test_knowledge_job_discard_durability import _retryable_job


pytestmark = pytest.mark.unit_fast


@pytest.mark.parametrize("failed_step", ["input", "record"])
async def test_discard_reports_each_unlink_failure_and_can_complete_later(tmp_path, monkeypatch, failed_step):
    service = KnowledgeService(tmp_path)
    await service.start()
    job, frozen = await _retryable_job(service)
    record = service.root / "jobs" / (job.job_id + ".json")
    input_bytes, record_bytes = frozen.read_bytes(), record.read_bytes()
    kept = ImportJob("e" * 32, job.pack_id, "failed", job.created_at, job.updated_at,
                     staged_sha256=job.staged_sha256)
    service._remember_job(kept)
    await service._persist_job(kept)
    kept_input = service.root / "jobs" / (kept.job_id + ".pack.json")
    kept_input.write_bytes(input_bytes)
    retained = {
        kept_input: input_bytes,
        service.root / "sources" / "retained" / "original": b"retained original",
        service.root / "sources" / "retained" / "history.json": b"retained history",
        service.root / "artifacts" / ("a" * 64 + ".neko-knowledge.zip"): b"retained ZIP",
    }
    for path, data in retained.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    target = frozen if failed_step == "input" else record
    original = Path.unlink
    deleted = []

    def refuse(path, *args, **kwargs):
        deleted.append(path)
        if path == target:
            raise PermissionError("synthetic transient unlink failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    monkeypatch.setattr(knowledge_routes, "_service", service)
    app = FastAPI()
    app.include_router(knowledge_routes.router)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            response = await http.post("/internal/knowledge/packs/jobs/discard", json={"job_id": job.job_id})
        await asyncio.gather(*tuple(service._detached), return_exceptions=True)
        assert response.status_code == 500 and response.json()["ok"] is False
        assert record.read_bytes() == record_bytes
        assert job.job_id in service._jobs and not job.export_deleted
        if failed_step == "input":
            # No metadata unlink is attempted before this job's input is gone.
            assert deleted == [frozen]
            assert frozen.read_bytes() == input_bytes
        else:
            assert deleted == [frozen, record]
            assert not frozen.exists()
            with pytest.raises(KnowledgePackError) as error:
                await service.retry_job(job.job_id)
            assert error.value.reason == "job_not_retryable"
        assert all(path.read_bytes() == data for path, data in retained.items())
        monkeypatch.setattr(Path, "unlink", original)
        assert await service.discard_job(job.job_id)
        assert job.job_id not in service._jobs and job.export_deleted
        assert not frozen.exists() and not record.exists()
        assert all(path.read_bytes() == data for path, data in retained.items())
    finally:
        await service.stop()
