"""Durable task records stay readable for the largest supported source selection."""

import asyncio
import json
import logging
import uuid

import pytest

from knowledge.models import MAX_ENTRIES_PER_PACK
from knowledge.service import MAX_JOB_RECORD_BYTES, KnowledgeService


pytestmark = pytest.mark.unit


def _documents(count: int) -> list[dict]:
    return [{"doc_id": uuid.uuid4().hex, "version": 1, "name": f"n{index}", "content": f"w{index}"}
            for index in range(count)]


async def test_largest_source_selection_survives_restart_and_can_be_discarded(tmp_path):
    service = KnowledgeService(tmp_path)
    await service.start()

    async def hold(job):
        await asyncio.Event().wait()

    service._commit_job = hold
    try:
        result = await service._project_sources("imp." + uuid.uuid4().hex, "many", _documents(MAX_ENTRIES_PER_PACK))
    finally:
        await service.stop()
    record = tmp_path / "jobs" / (result["job_id"] + ".json")
    assert record.stat().st_size <= MAX_JOB_RECORD_BYTES
    # The selection stays frozen in the retry input, not the task record.
    frozen = json.loads((tmp_path / "jobs" / (result["job_id"] + ".pack.json")).read_bytes())
    assert len(frozen["entries"]) == MAX_ENTRIES_PER_PACK

    reopened = KnowledgeService(tmp_path)
    await reopened.start()
    try:
        row = next(item for item in reopened.list_jobs() if item["job_id"] == result["job_id"])
        assert row["state"] == "failed"
        await reopened.discard_job(result["job_id"])
        assert all(item["job_id"] != result["job_id"] for item in reopened.list_jobs())
        assert not (tmp_path / "jobs" / (result["job_id"] + ".pack.json")).exists()
    finally:
        await reopened.stop()


async def test_oversized_job_record_is_skipped_with_identity_only_warning(tmp_path, caplog):
    (tmp_path / "jobs").mkdir(parents=True)
    job_id = uuid.uuid4().hex
    secret = "private-document-title"
    (tmp_path / "jobs" / (job_id + ".json")).write_text(
        json.dumps({"job_id": job_id, "reason": secret * (MAX_JOB_RECORD_BYTES // len(secret) + 1)}))
    service = KnowledgeService(tmp_path)
    with caplog.at_level(logging.WARNING):
        await service.start()
    try:
        assert all(item["job_id"] != job_id for item in service.list_jobs())
        messages = [record.getMessage() for record in caplog.records if "oversized job record" in record.getMessage()]
        assert messages and job_id in messages[0]
        assert secret not in caplog.text
    finally:
        await service.stop()
