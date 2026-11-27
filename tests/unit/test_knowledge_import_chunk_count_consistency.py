"""Reject a commit when token-fit planning changes after capacity checks."""

import pytest

from knowledge.models import decode_pack_bytes
from knowledge.service import KnowledgeService
from knowledge.store import count_pack_chunks
from tests.knowledge_helpers import _stop_automatic_indexer
from tests.knowledge_fixtures import _finished, _import, _pack, _raw


pytestmark = pytest.mark.unit


@pytest.fixture
async def service(tmp_path):
    value = KnowledgeService(tmp_path)
    await value.start()
    try:
        await _stop_automatic_indexer(value)
        yield value
    finally:
        await value.stop()


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("count_limit,write_limit", [(5000, 100), (100, 5000)])
async def test_changed_fit_cannot_publish_a_different_chunk_count(service, monkeypatch, installed, count_limit, write_limit):
    from knowledge import service as module

    pack_id = "imp.chunk-plan"
    if installed:
        await _import(service, _pack(pack_id, entries=[{"title": "Old", "content": "Retained original reference."}]))
    previous = service._registry.packs.get(pack_id)
    registry_path = service.root / "registry.json"
    registry_bytes = registry_path.read_bytes() if registry_path.exists() else None
    original = (service.root / "packs" / previous.file_name).read_bytes() if previous else None
    raw = _raw(_pack(pack_id, entries=[{"title": "Changing", "content": "Mountain reference " * 45}]))
    limit = [count_limit]

    def fit(text):
        return len(text) <= limit[0]

    monkeypatch.setattr(service, "_chunk_fit", lambda: fit)
    planned = count_pack_chunks(decode_pack_bytes(raw), fit=fit)
    # In the growth case even the existing per-pack limit is crossed by the
    # changed tokenizer; that must never become an accepted low-count record.
    monkeypatch.setattr(module, "MAX_CHUNKS_PER_PACK", planned)
    replace_pack = service._store.replace_pack
    written = []

    def tokenizer_changes_before_write(pack, **kwargs):
        if pack.entries[0].title == "Changing":
            limit[0] = write_limit
            actual = replace_pack(pack, **kwargs)
            written.append(actual)
            return actual
        return replace_pack(pack, **kwargs)

    with monkeypatch.context() as faults:
        faults.setattr(service._store, "replace_pack", tokenizer_changes_before_write)
        failed = await _finished(service, await service.import_pack(raw))

    assert len(written) == 1 and written[0] != planned
    assert failed["state"] == "failed", failed
    assert failed["reason"] == "knowledge_error"
    assert (registry_path.read_bytes() if registry_path.exists() else None) == registry_bytes
    assert service._registry.packs.get(pack_id) == previous
    versions = service._store.pack_versions()
    if previous:
        assert versions[pack_id] == previous.pack_sha256
        assert (service.root / "packs" / previous.file_name).read_bytes() == original
        assert (await service.import_pack(original)).get("unchanged") is True
    else:
        assert pack_id not in versions

    # Retry the same frozen input with a stable tokenizer. The commit-gate
    # flag of the failed job cannot make its new retry spuriously active.
    limit[0] = count_limit
    repaired = await _finished(service, await service.retry_job(failed["job_id"]))
    assert repaired["state"] == "active", repaired
    assert service._registry.packs[pack_id].chunks == service._store.chunk_stats(None)[pack_id]["total"] == planned


@pytest.mark.parametrize("token_limit", [None, 100])
async def test_stable_chunk_plan_still_commits_and_reimports_unchanged(service, monkeypatch, token_limit):
    if token_limit is not None:
        monkeypatch.setattr(service, "_chunk_fit", lambda: lambda text: len(text) <= token_limit)
    raw = _raw(_pack("imp.stable-plan", entries=[{"title": "Stable", "content": "Coastal reference " * 45}]))
    active = await _finished(service, await service.import_pack(raw))
    assert active["state"] == "active"
    record = service._registry.packs["imp.stable-plan"]
    assert record.chunks == service._store.chunk_stats(None)[record.pack_id]["total"] > 0
    assert (await service.import_pack(raw)).get("unchanged") is True
