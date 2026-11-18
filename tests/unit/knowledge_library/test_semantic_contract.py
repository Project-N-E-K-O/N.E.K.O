"""Protect token coverage, frozen build identity and derived vector compatibility."""

from copy import deepcopy
import math
from pathlib import Path
import hashlib
import json
import zipfile
import asyncio
from dataclasses import replace

import pytest

from services.knowledge_library.indexing import make_chunks, plan_chunks
from services.knowledge_library.models import LibraryError
from services.knowledge_library.package_build import build_package
from services.knowledge_library.package_validate import (
    CHUNKING_VERSION, bind_source_offsets, canonical_json, validate_package,
)
from tests.unit.knowledge_library.test_packages import Encoder, _built, _rewrite, _snapshot, _spec
from tests.unit.knowledge_library.test_library_domain import library, add_document
from tests.unit.knowledge_library.test_routes import client, HEADERS, import_text

pytestmark = [pytest.mark.unit, pytest.mark.unit_fast]


class SmallTokenEncoder(Encoder):
    """Use a tiny deterministic token budget to make every split observable."""

    async def plan_inputs(self, inputs, **_kwargs):
        return [[{"start": start, "end": min(len(row["body"]), start + 7)}
                 for start in range(0, len(row["body"]), 7)] for row in inputs]

    async def embed_batch(self, texts):
        assert all(len(text.split("\n", 1)[1]) <= 7 for text in texts)
        return await super().embed_batch(texts)


async def test_builder_plans_all_source_before_encoding_and_preserves_tail(tmp_path):
    snapshot = _snapshot()
    encoder = SmallTokenEncoder()
    built = await build_package(snapshot, tmp_path / "artifacts/build.zip", encoder)
    saved = validate_package(Path(built["artifact_path"])).to_snapshot()
    assert saved["chunking_version"] == CHUNKING_VERSION
    for original in snapshot["chunks"]:
        parts = [row for row in saved["chunks"] if row["document_id"] == original["document_id"]]
        assert "".join(row["text"] for row in parts) == original["text"]
        assert parts[-1]["text"].endswith(original["text"][-2:])
    assert len(saved["chunks"]) > len(snapshot["chunks"])


async def test_bad_planner_coverage_and_missing_planner_cannot_publish(tmp_path):
    encoder = Encoder()

    async def missing_tail(inputs, **_kwargs):
        return [[{"start": 0, "end": len(row["body"]) - 1}] for row in inputs]

    encoder.plan_inputs = missing_tail
    with pytest.raises(LibraryError, match="embedding_plan_invalid"):
        await build_package(_snapshot(), tmp_path / "bad/build.zip", encoder)
    encoder.plan_inputs = None
    with pytest.raises(LibraryError, match="embedding_unavailable"):
        await build_package(_snapshot(), tmp_path / "missing/build.zip", encoder)
    assert not list(tmp_path.rglob("*.neko-knowledge-pack.zip"))


async def test_frozen_build_spec_is_rejected_before_planning_or_inference(tmp_path):
    frozen = _spec()
    encoder = Encoder()
    encoder.spec["weights_sha256"]["model"] = "f" * 64
    with pytest.raises(LibraryError, match="package_embedding_identity_changed"):
        await build_package(_snapshot(), tmp_path / "build.zip", encoder, expected_spec=frozen)
    assert not encoder.calls


def test_prefix_projection_equals_direct_encoder_truncation_in_both_directions():
    from services.knowledge_library.embedding_compat import semantic_cosine

    small, large = _spec(), _spec()
    large.update(dimension=256, model_id="local-text-retrieval-v1-256d-int8-mlen1024")
    raw = [float(index + 1) for index in range(256)]
    full_norm = math.sqrt(sum(value * value for value in raw))
    prefix_norm = math.sqrt(sum(value * value for value in raw[:64]))
    document = [value / full_norm for value in raw]
    query = [value / prefix_norm for value in raw[:64]]
    assert semantic_cosine(document, large, query, small) == pytest.approx(1)
    assert semantic_cosine(query, small, document, large) == pytest.approx(1)


@pytest.mark.parametrize("change", ["quantization", "weights", "processing"])
def test_projection_never_crosses_other_encoder_identity_fields(change):
    from services.knowledge_library.embedding_compat import compatible_dimension

    a, b = _spec(), _spec()
    b.update(dimension=256, model_id="local-text-retrieval-v1-256d-int8-mlen1024")
    if change == "quantization":
        b.update(quantization="fp32", model_id="local-text-retrieval-v1-256d-fp32-mlen1024")
    elif change == "weights":
        b["weights_sha256"]["model"] = "f" * 64
    else:
        b["processing_version"] = "unknown"
    assert compatible_dimension(a, b) is None


def test_zero_or_nonfinite_prefix_is_not_a_usable_semantic_view():
    from services.knowledge_library.embedding_compat import semantic_cosine

    small, large = _spec(), _spec()
    large.update(dimension=256, model_id="local-text-retrieval-v1-256d-int8-mlen1024")
    query = [1.0] + [0.0] * 63
    assert semantic_cosine([0.0] * 64 + [1.0] + [0.0] * 191, large, query, small) is None
    assert semantic_cosine([float("nan")] + [0.0] * 255, large, query, small) is None
    assert semantic_cosine([1.0], large, query, small) is None


async def test_legacy_personal_package_remains_readable_and_is_replanned_on_build(tmp_path):
    snapshot = _snapshot()
    snapshot["chunking_version"] = "neko-paragraph-windows-v1-title-v1"
    built = await _built(tmp_path, snapshot=snapshot)
    assert validate_package(Path(built["artifact_path"])).manifest["chunking_version"] == CHUNKING_VERSION
    with zipfile.ZipFile(built["artifact_path"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    manifest["chunking_version"] = snapshot["chunking_version"]
    legacy = _rewrite(Path(built["artifact_path"]), tmp_path / "legacy.zip", manifest_raw=canonical_json(manifest))
    assert validate_package(legacy).manifest["chunking_version"] == snapshot["chunking_version"]


async def test_recomputed_hash_cannot_substitute_a_v2_embedding_input(tmp_path):
    built = await _built(tmp_path)

    def substitute(raw):
        rows = [json.loads(line) for line in raw.splitlines()]
        rows[0]["embedding_text"] = "unrelated private input"
        rows[0]["input_sha256"] = hashlib.sha256(rows[0]["embedding_text"].encode()).hexdigest()
        return b"".join(canonical_json(row) + b"\n" for row in rows)

    hostile = _rewrite(Path(built["artifact_path"]), tmp_path / "hostile.zip", member="chunks.jsonl", transform=substitute)
    with pytest.raises(LibraryError, match="package_source_mismatch"):
        validate_package(hostile)


@pytest.mark.parametrize("zero_prefix", [False, True])
async def test_package_compatibility_checks_the_actual_common_prefix(tmp_path, zero_prefix):
    class PrefixEncoder(Encoder):
        async def embed_batch(self, texts):
            row = ([0.0] * 32 + [1 / math.sqrt(32)] * 32 if zero_prefix
                   else [1 / 8] * 64)
            return [row[:] for _ in texts]

    built = await _built(tmp_path, encoder=PrefixEncoder())
    expected = _spec()
    expected.update(dimension=32, model_id="local-text-retrieval-v1-32d-int8-mlen1024")
    assert validate_package(Path(built["artifact_path"]), expected_spec=expected).semantic_available is not zero_prefix


async def test_export_capacity_checks_only_selected_documents_before_snapshot_write(library, monkeypatch):
    from services.knowledge_library import store

    monkeypatch.setattr(store, "PACKAGE_LIMITS", replace(store.PACKAGE_LIMITS, max_chunks=1))
    identifier = (await library.create_library("Local larger than export"))["library_id"]
    small = await add_document(library, identifier, "First short source")
    await add_document(library, identifier, "Second short source")
    before = set((library.store.root / "jobs/snapshots").glob("*.json"))
    with pytest.raises(LibraryError, match="package_capacity_exceeded"):
        await library.freeze_library(identifier)
    assert set((library.store.root / "jobs/snapshots").glob("*.json")) == before
    selected = await library.freeze_library(identifier, document_ids=[small["document_id"]])
    assert len(selected["chunks"]) == 1


async def test_build_retry_uses_first_persisted_spec_after_encoder_changes(client, monkeypatch):
    from main_routers import knowledge_library_router as routes

    http, app, _root = client
    identifier = (await http.post("/api/knowledge-library/libraries", json={"name": "Frozen build"}, headers=HEADERS)).json()["library"]["library_id"]
    await import_text(http, identifier)
    host = app.state.knowledge_library_host
    service = await host.service()
    original = routes.build_package
    first_spec = await host.embedder.specification()

    async def first_failure(*args, **kwargs):
        assert kwargs["expected_spec"] == first_spec
        raise LibraryError("embedding_unavailable")

    monkeypatch.setattr(routes, "build_package", first_failure)
    document_id = (await service.get_library(identifier))["documents"][0]["document_id"]
    response = await http.post(f"/api/knowledge-library/libraries/{identifier}/packages",
                               json={"version": "1", "document_ids": [document_id]}, headers=HEADERS)
    first_job = response.json()["job"]
    await service.jobs.queue.join()
    record = service.jobs.records[first_job["job_id"]]
    assert record["state"] == "failed"
    assert record["frozen_input"]["embedding_spec"] == first_spec
    first_spec["weights_sha256"]["model"] = "f" * 64

    async def changed_spec():
        return deepcopy(first_spec)

    monkeypatch.setattr(host.embedder, "specification", changed_spec)
    monkeypatch.setattr(routes, "build_package", original)
    before = host.embedder.calls
    retried = (await http.post(f"/api/knowledge-library/jobs/{first_job['job_id']}/retry", headers=HEADERS)).json()["job"]
    await service.jobs.queue.join()
    result = service.jobs.records[retried["job_id"]]
    assert result["state"] == "failed"
    assert result["reason"] == "package_embedding_identity_changed"
    assert host.embedder.calls == before


class SplitEncoder(Encoder):
    """Expose repeatable subdivisions to reproduce nested token replanning."""

    def __init__(self, size):
        super().__init__()
        self.size = size

    async def plan_inputs(self, inputs, **_kwargs):
        return [[{"start": start, "end": min(len(row["body"]), start + self.size)}
                 for start in range(0, len(row["body"]), self.size)] for row in inputs]


async def test_nested_replanning_preserves_unique_identity_and_absolute_source_span():
    chunks = make_chunks("doc", "source.txt", [{"text": "x" * 20, "location": {"paragraph": 1}}])
    old = await plan_chunks(chunks, SplitEncoder(10), specification=_spec(), max_chunks=100)
    new = await plan_chunks(old, SplitEncoder(5), specification=_spec(), max_chunks=100)
    assert len(new) == len({row["chunk_id"] for row in new}) == 4
    assert [row["location"]["source_start"] for row in new] == [0, 5, 10, 15]
    assert [row["location"]["token_path"] for row in new] == [[1, 1], [1, 2], [2, 1], [2, 2]]
    assert "".join(row["text"] for row in new) == "x" * 20
    unchanged = await plan_chunks(new, SplitEncoder(5), specification=_spec(), max_chunks=100)
    assert [row["chunk_id"] for row in unchanged] == [row["chunk_id"] for row in new]


@pytest.mark.parametrize("position", ["head", "middle", "tail"])
def test_source_span_union_rejects_nonwhitespace_holes(position):
    source = {"head": "MISSING left right", "middle": "left MISSING right", "tail": "left right MISSING"}[position]
    chunks = [{"document_id": "doc", "text": text,
               "location": {"source_start": source.index(text), "source_end": source.index(text) + len(text)}}
              for text in ("left", "right")]
    with pytest.raises(LibraryError, match="package_source_incomplete"):
        bind_source_offsets([{"document_id": "doc", "content": source}], chunks, require_offsets=True)


async def test_builder_rejects_frozen_source_tail_without_any_covering_chunk(tmp_path):
    snapshot = _snapshot()
    snapshot["documents"][0]["content"] += " MISSING_SOURCE_TAIL"
    with pytest.raises(LibraryError, match="package_source_incomplete"):
        await _built(tmp_path, snapshot=snapshot)


async def test_validator_rejects_self_consistent_uncovered_document_tail(tmp_path):
    built = await _built(tmp_path)

    def add_tail(raw):
        rows = [json.loads(line) for line in raw.splitlines()]
        rows[0]["content"] += " MISSING_SOURCE_TAIL"
        rows[0]["content_sha256"] = hashlib.sha256(rows[0]["content"].encode()).hexdigest()
        return b"".join(canonical_json(row) + b"\n" for row in rows)

    hostile = _rewrite(Path(built["artifact_path"]), tmp_path / "missing-tail.zip",
                       member="documents.jsonl", transform=add_tail)
    with pytest.raises(LibraryError, match="package_source_incomplete"):
        validate_package(hostile, expected_spec=_spec())


@pytest.mark.parametrize("legacy_offsets", [False, True])
async def test_repeated_paragraphs_and_overlapping_windows_keep_complete_source(tmp_path, legacy_offsets):
    texts = ["abcabc", "abcabc", "x" * 2600 + "END"]
    source = "\n\n".join(texts)
    chunks = make_chunks("document-0", "note-0.txt", [
        {"text": text, "location": {"paragraph": index + 1}} for index, text in enumerate(texts)
    ])
    if legacy_offsets:
        for chunk in chunks:
            chunk["location"].pop("source_start")
            chunk["location"].pop("source_end")
    snapshot = _snapshot()
    snapshot["documents"] = [{**snapshot["documents"][0], "content": source}]
    snapshot["chunks"] = chunks
    built = await _built(tmp_path, snapshot=snapshot, encoder=SplitEncoder(113))
    saved = validate_package(Path(built["artifact_path"]), expected_spec=_spec())
    assert saved.semantic_available
    assert len(saved.chunks) == len({row["chunk_id"] for row in saved.chunks})
    spans = [(row["location"]["source_start"], row["location"]["source_end"]) for row in saved.chunks]
    assert all(source[start:end] == row["text"] for (start, end), row in zip(spans, saved.chunks))
    assert max(end for _start, end in spans) == len(source)
    assert sum(end - start for start, end in spans) > len(source)  # Character windows still overlap.


async def test_query_explicitly_binds_spec_when_an_adapter_yields_before_encoding(library):
    """A delayed adapter must not substitute a refreshed shared-cache identity."""
    identifier = (await library.create_library("Bound queries"))["library_id"]
    document = await add_document(library, identifier, "Coastal navigation archive")
    spec = await library.embedder.specification()
    entered, refreshed = asyncio.Event(), asyncio.Event()
    seen = []

    class DelayedAdapter:
        def __init__(self):
            self.cached_spec = spec

        async def specification(self):
            return deepcopy(spec)

        async def embed_batch(self, texts, *, expected_spec=None):
            # Model an adapter boundary with scheduling before cache access.
            # The production adapter currently captures earlier; the domain
            # contract must not depend on that implementation-specific timing.
            entered.set()
            await refreshed.wait()
            seen.append(expected_spec)
            selected = expected_spec if expected_spec is not None else self.cached_spec
            return [[1.0, 0.0] if selected == spec else [0.0, 1.0] for _ in texts]

    adapter = DelayedAdapter()
    library.embedder = adapter

    async def refresh_shared_cache():
        await entered.wait()
        adapter.cached_spec = {**spec, "model_id": "different-encoder"}
        refreshed.set()

    concurrent = asyncio.create_task(refresh_shared_cache())
    try:
        result = await asyncio.wait_for(library.search(identifier, "semanticneedle"), 3)
    finally:
        await concurrent
    assert seen == [spec]
    assert result["hits"][0]["document_id"] == document["document_id"]
    assert result["hits"][0]["match_type"] == "semantic"
