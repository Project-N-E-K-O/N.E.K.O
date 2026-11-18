"""Keep encoder pressure inside one cancellable job rather than degrading or retrying it."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from services.knowledge_library.models import LibraryError
from services.knowledge_library.jobs import wait_for_embedding
from services.knowledge_library.package_build import build_package
from services.knowledge_library.package_validate import validate_package
from services.knowledge_library.service import LibraryService
from tests.unit.knowledge_library.test_packages import Encoder, _snapshot, _spec

pytestmark = [pytest.mark.unit, pytest.mark.unit_fast]


class BusyEncoder(Encoder):
    """Hold one real computation boundary busy until the test releases it."""

    def __init__(self, stage):
        super().__init__()
        self.stage = stage
        self.released = asyncio.Event()
        self.waiting = asyncio.Event()
        self.pressure = 0
        self.payloads = []

    def _pressure(self, stage):
        # Batch pressure starts after one successful batch so rerunning an
        # entire runner would observably recompute its already finished work.
        if self.stage != stage or self.released.is_set() or stage == "batch" and not self.calls:
            return
        self.pressure += 1
        if self.pressure >= 2:
            self.waiting.set()
        raise LibraryError("embedding_busy")

    async def specification(self):
        self._pressure("spec")
        return await super().specification()

    async def plan_inputs(self, inputs, **kwargs):
        assert kwargs["expected_spec"] == self.spec
        self._pressure("plan")
        return await super().plan_inputs(inputs, **kwargs)

    async def embed_background(self, texts, *, expected_spec):
        self.payloads.append((deepcopy(texts), deepcopy(expected_spec)))
        self._pressure("batch")
        assert expected_spec == self.spec
        return await super().embed_batch(texts)


def _six_chunk_snapshot():
    """Make two build batches without relying on a native tokenizer or model."""
    snapshot = _snapshot()
    for index in range(2, 6):
        document = deepcopy(snapshot["documents"][0])
        document.update(document_id=f"document-{index}", name=f"note-{index}.txt")
        chunk = deepcopy(snapshot["chunks"][0])
        chunk.update(chunk_id=f"chunk-{index}", document_id=document["document_id"],
                     embedding_text=document["name"] + "\n" + chunk["text"])
        snapshot["documents"].append(document)
        snapshot["chunks"].append(chunk)
    return snapshot


async def _job(service, kind, tmp_path):
    """Start a real domain job; the caller will inspect its persisted identity."""
    if kind == "build":
        async def build(context):
            return await build_package(_six_chunk_snapshot(), tmp_path / "artifacts/build.zip", service.embedder,
                                       expected_spec=_spec(), check_cancelled=context.check_cancelled,
                                       commit_gate=context.commit_gate)
        return await service.submit_job("build", None, build, frozen_input={"embedding_spec": _spec()})
    library_id = (await service.create_library("Busy fixture"))["library_id"]
    content = "\n\n".join(f"Section {index} retains its complete source material." for index in range(6))
    preview = await service.preview_document(library_id, "notes.txt", content.encode())
    await service.jobs.queue.join()
    preview_id = (await service.get_job(preview["job_id"]))["result"]["preview_id"]
    staged = await service.get_preview(preview_id)
    return await service.confirm_preview(preview_id, staged["sha256"])


@pytest.mark.parametrize("kind", ["index", "build"])
@pytest.mark.parametrize("stage", ["spec", "plan", "batch"])
async def test_busy_waits_in_same_job_without_degrading_or_recomputing(tmp_path, kind, stage):
    encoder = BusyEncoder(stage)
    service = LibraryService(tmp_path, encoder)
    await service.start()
    try:
        job = await _job(service, kind, tmp_path)
        frozen = deepcopy(service.jobs.records[job["job_id"]]["frozen_input"])
        count = len(service.jobs.records)
        await asyncio.wait_for(encoder.waiting.wait(), 1)
        current = await service.get_job(job["job_id"])
        assert current["state"] == "running" and not current["reason"]
        assert len(service.jobs.records) == count
        assert service.jobs.records[job["job_id"]]["frozen_input"] == frozen
        encoder.released.set()
        await asyncio.wait_for(service.jobs.queue.join(), 2)
        current = await service.get_job(job["job_id"])
        assert current["state"] == "completed", current
        assert len(encoder.calls) == 2 and [len(batch) for batch in encoder.calls] == [4, 2]
        assert all(spec == _spec() for _texts, spec in encoder.payloads)
        if stage == "batch":
            assert encoder.payloads[1:] == [encoder.payloads[1]] * (len(encoder.payloads) - 1)
        if kind == "index":
            document = await service.get_document(current["library_id"], current["result"]["document_id"])
            assert document["semantic_state"] == "ready"
        else:
            package = validate_package(Path(current["result"]["artifact_path"]))
            assert package.manifest["counts"]["chunks"] == 6
        assert len(service.jobs.records) == count
    finally:
        encoder.released.set()
        await service.close()


@pytest.mark.parametrize("reason", ["embedding_unavailable", "embedding_failed", "embedding_incompatible"])
async def test_only_busy_is_waited_without_masking_other_encoder_outcomes(reason):
    """A missing model or genuine error must not become an infinite busy wait."""
    calls = 0

    async def unavailable():
        nonlocal calls
        calls += 1
        raise LibraryError(reason)

    with pytest.raises(LibraryError, match=reason):
        await asyncio.wait_for(wait_for_embedding(unavailable), 0.5)
    assert calls == 1


@pytest.mark.parametrize("kind", ["index", "build"])
async def test_busy_wait_responds_to_job_cancel_without_another_attempt(tmp_path, kind):
    encoder = BusyEncoder("batch")
    service = LibraryService(tmp_path, encoder)
    await service.start()
    try:
        job = await _job(service, kind, tmp_path)
        count = len(service.jobs.records)
        await asyncio.wait_for(encoder.waiting.wait(), 1)
        await service.cancel_job(job["job_id"])
        await asyncio.wait_for(service.jobs.queue.join(), 1)
        current = await service.get_job(job["job_id"])
        assert current["state"] == "cancelled" and current["reason"] == "job_cancelled"
        assert not encoder.released.is_set() and len(encoder.calls) == 1
        assert len(service.jobs.records) == count
        assert not list(tmp_path.rglob("*.neko-knowledge-pack.zip"))
    finally:
        encoder.released.set()
        await service.close()
