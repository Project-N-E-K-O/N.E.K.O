"""Child-process scenarios for opt-in native knowledge acceptance and measurements.

The parent pytest case creates every writable knowledge root and supplies existing model
assets. Install process isolation before importing product code, since a child
does not execute conftest. No stage starts servers or downloads model assets.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import statistics
import time
from unittest.mock import patch

from tests import process_isolation

process_isolation.isolate_test_process()
assert process_isolation.PRODUCT_MODULES_BEFORE_ISOLATION == ()

import httpx  # noqa: E402
import psutil  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from app.memory_server import knowledge_routes  # noqa: E402
from app.memory_server.knowledge_routes import _SharedEmbedder  # noqa: E402
from knowledge.archive import export_archive, read_archive  # noqa: E402
from knowledge.service import DATABASE_FILE, KnowledgeService  # noqa: E402
from memory.embeddings import EmbeddingService  # noqa: E402
from tests.knowledge_helpers import _stop_automatic_indexer  # noqa: E402
from utils.knowledge_package import validate_package  # noqa: E402


def _hashes(folder: Path) -> dict[str, str]:
    """Observe immutable synthetic inputs independently of service readiness."""
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob("*") if path.is_file()}


def _read(root: Path, stage: str) -> dict:
    return json.loads((root / f"{stage}.json").read_text(encoding="utf-8"))


def _record(root: Path, stage: str, data: dict) -> None:
    """Keep stage evidence after the child and all its native owners exit."""
    with (root / f"{stage}.json").open("x", encoding="utf-8") as output:
        json.dump({"pid": os.getpid(), **data}, output, indent=2)


@asynccontextmanager
async def _library(root: Path, *, automatic_index: bool = False):
    """Own native resources; measurement builds keep the original index loop."""
    encoder = EmbeddingService(
        model_dir=os.environ["NEKO_REAL_EMBEDDING_ROOT"], enabled=True, ram_gb=8,
        embedding_dim_setting=128, quantization_setting="int8",
        has_vnni=False, has_avx2=False,
    )
    calls = []
    infer = encoder._infer_blocking

    def counted(texts):
        calls.append(len(texts))
        return infer(texts)

    service = KnowledgeService(root, embedder=_SharedEmbedder(encoder))
    with patch.object(encoder, "_infer_blocking", counted):
        try:
            await service.start()
            if not automatic_index:
                await _stop_automatic_indexer(service)
            yield service, encoder, calls
        finally:
            try:
                await service.stop()
            finally:
                await encoder.close()


async def _terminal(service, response):
    """Wait for a durable job result, preserving failures as test failures."""
    assert response["ok"], response
    async with asyncio.timeout(30):
        while True:
            job = next(row for row in service.list_jobs() if row["job_id"] == response["job_id"])
            if job["state"] not in {"queued", "building"}:
                assert job["state"] == "active", job
                return job
            await asyncio.sleep(0.01)


async def _query(service, mode, text="violet lighthouse"):
    result = await service.query(query=text, budget_ms=5000)
    assert result["result"] == "matched", result
    assert result["retrieval_mode"] == mode, result
    return result


async def _author(root: Path) -> dict:
    """Preserve two source versions and export real vectors without re-encoding."""
    folder = root / "author"
    assert not folder.exists()
    async with _library(folder) as (service, encoder, calls):
        spec = await asyncio.to_thread(service.embedder.spec)
        assert spec and encoder._session is None
        first = (await service.preview_source(
            "coast.md", b"A violet lighthouse guides ships near Copperpine Observatory.",
            name="Synthetic coastal reference",
        ))["preview"]
        await _terminal(service, await service.commit_source(first["preview_id"], first["sha256"]))
        pack = (await service.list_packs())[0]
        second = (await service.preview_source(
            "coast.md", b"A violet lighthouse guides ships near Copperpine Observatory. It flashes twice at dusk.",
            pack_id=pack["pack_id"], doc_id=first["doc_id"],
        ))["preview"]
        await _terminal(service, await service.commit_source(second["preview_id"], second["sha256"]))
        pack = (await service.list_packs())[0]
        assert pack["enabled"] is False and pack["auto_context"] is False
        assert calls == [] and encoder._session is None
        assert (await service.query(query="violet lighthouse"))["result"] == "miss"
        versions = (await service.source_versions(pack["pack_id"], first["doc_id"]))["versions"]
        assert [row["version"] for row in versions] == [1, 2]
        assert await encoder.request_load()
        assert await service._indexer._index_round() > 0
        vectors = service._store.vectors_for_pack(pack["pack_id"], spec["model_id"])
        assert vectors and calls
        before = len(calls)
        exported = await _terminal(service, await service.export_pack(pack["pack_id"]))
        path, friendly = await service.artifact_download(exported["result"]["artifact_id"])
        checked = validate_package(path, expected_spec=spec)
        assert checked.semantic_available and len(checked.chunks) == len(vectors)
        assert checked.manifest["version"].startswith("auto-")
        assert friendly.endswith(".neko-knowledge.zip") and friendly != path.name
        # The existing native-market contract requests version 1.0.0. Build
        # that separate fixture from these exact certified vectors, keeping
        # the user's automatic export and its version unchanged.
        contents = read_archive(path, expected_spec=spec)
        fixture = root / "market-fixture"
        built = export_archive(fixture, contents["pack"], version="1.0.0",
                               specification=spec, vectors=contents["seed_vectors"])
        shutil.copyfile(fixture / (built["sha256"] + ".neko-knowledge.zip"),
                        fixture / "package-1.0.0.neko-knowledge.zip")
        assert len(calls) == before
        return {"pack_id": pack["pack_id"], "doc_id": first["doc_id"], "spec": spec,
                "chunks": len(vectors), "artifact": path.relative_to(root).as_posix(),
                "artifact_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "filename": friendly, "version": checked.manifest["version"],
                "source_hashes": _hashes(folder / "sources"),
                "cold_native_calls": 0, "export_extra_native_calls": len(calls) - before}


async def _install(root: Path) -> dict:
    """Use the actual upload route, which owns local-import privacy defaults."""
    author = _read(root, "author")
    folder = root / "reader"
    assert not folder.exists()
    async with _library(folder) as (service, encoder, calls):
        raw = (root / author["artifact"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == author["artifact_sha256"]
        app = FastAPI()
        app.include_router(knowledge_routes.router)
        # Replace only the endpoint's service owner; parsing, jobs and storage
        # are real. These globals never outlive this isolated child process.
        with patch.object(knowledge_routes, "_service", service), patch.object(
            knowledge_routes, "_import_slots", asyncio.Semaphore(knowledge_routes.MAX_PENDING_IMPORTS),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1",
            ) as client:
                response = await client.post("/internal/knowledge/packs/import", content=raw,
                                             headers={"content-type": "application/zip"})
                assert response.status_code == 200, response.text
                await _terminal(service, response.json())
        pack = (await service.list_packs())[0]
        assert pack["pack_id"] == author["pack_id"]
        assert pack["enabled"] is False and pack["auto_context"] is False
        assert pack["sources_managed"] is False
        rows = service._store.vectors_for_pack(pack["pack_id"], author["spec"]["model_id"])
        assert len(rows) == author["chunks"] and calls == [] and encoder._session is None
        enabled = await service.set_pack_enabled(pack["pack_id"], True)
        assert enabled["pack_enabled"] is True, enabled
        await _query(service, "bm25")
        assert calls == []
        assert await encoder.request_load()
        await service._indexer._refresh_vectors_now()
        await _query(service, "hybrid")
        assert len(calls) == 1
        return {"pack_id": pack["pack_id"], "chunks": len(rows), "install_native_calls": 0,
                "cold_query": "bm25", "warm_query": "hybrid", "query_native_calls": len(calls)}


async def _recover(root: Path) -> dict:
    """Rebuild only the test-owned derived index; retain policy and source history."""
    author = _read(root, "author")
    folder = root / "reader"
    protected = {name: _hashes(folder / name) for name in ("packs", "artifacts")}
    assert all(protected.values())
    # Construct without starting: remove only this run's derived database,
    # never the saved Pack/ZIP inputs or a previously existing runtime root.
    from knowledge.store import KnowledgeStore

    store = KnowledgeStore(folder / DATABASE_FILE)
    assert store.database_path.exists()
    store.remove_files()
    assert not store.database_path.exists()
    async with _library(folder) as (service, encoder, calls):
        assert service.availability()["ready"]
        pack = (await service.list_packs())[0]
        assert pack["enabled"] is True and pack["auto_context"] is False
        # Cold public readiness describes the loaded model. Certify durable
        # vectors by their stored specification before explicitly loading it.
        rows = service._store.vectors_for_pack(pack["pack_id"], author["spec"]["model_id"])
        assert len(rows) == author["chunks"]
        assert service._store.vector_specification(
            author["spec"]["model_id"], pack_id=pack["pack_id"],
        ) == author["spec"]
        assert calls == [] and encoder._session is None
        assert protected == {name: _hashes(folder / name) for name in protected}
        assert await encoder.request_load()
        await service._indexer._refresh_vectors_now()
        pack = (await service.list_packs())[0]
        assert pack["chunks_ready"] == pack["chunks_total"] == author["chunks"]
        await _query(service, "hybrid")
        assert len(calls) == 1
    async with _library(root / "author") as (history, _, _calls):
        versions = (await history.source_versions(author["pack_id"], author["doc_id"]))["versions"]
        assert [row["version"] for row in versions] == [1, 2]
        assert len(list((root / "author" / "sources").rglob("original"))) == 2
        assert _hashes(root / "author" / "sources") == author["source_hashes"]
    return {"derived_index_rebuilt": True, "ready_chunks": author["chunks"],
            "policy_preserved": True, "protected_inputs_unchanged": True,
            "history_versions": [1, 2], "query": "hybrid", "query_native_calls": 1}


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


async def _measure_build(root: Path) -> dict:
    """Measure real background backfill, including unchanged production pauses."""
    from knowledge import indexing
    from tests.knowledge_fixtures import _entries, _pack, _raw

    count = _read(root, "run")["entries"]
    folder = root / "library"
    assert not folder.exists()
    process = psutil.Process()
    rss = {"before_library": process.memory_info().rss}
    opened = time.perf_counter()
    async with _library(folder, automatic_index=True) as (service, encoder, calls):
        empty_open_ms = _elapsed_ms(opened)
        started = time.perf_counter()
        payload = _pack("measure-coast", entries=_entries("Station", count, "violet lighthouse"))
        await _terminal(service, await service.import_pack(_raw(payload)))
        import_ms = _elapsed_ms(started)
        pack = (await service.list_packs())[0]
        assert pack["entries"] == count and pack["chunks_total"] == count
        # Loading and enabling are explicit setup. The production loop owns
        # every batch, duty pause and round delay; no timer is monkeypatched.
        await service.set_pack_local_embedding(pack["pack_id"], False)
        assert not calls and encoder._session is None
        rss["before_model_load"] = process.memory_info().rss
        started = time.perf_counter()
        assert await encoder.request_load()
        model_load_ms = _elapsed_ms(started)
        rss["after_model_load"] = process.memory_info().rss
        started = time.perf_counter()
        await service.set_pack_local_embedding(pack["pack_id"], True)
        async with asyncio.timeout(70):
            while True:
                pack = (await service.list_packs())[0]
                assert pack["chunks_failed"] == 0, pack
                if pack["chunks_ready"] == count:
                    break
                await asyncio.sleep(0.2)
        index_ms = _elapsed_ms(started)
        assert sum(calls) == count
        rss["after_index"] = process.memory_info().rss
        spec = await asyncio.to_thread(service.embedder.spec)
        return {"entries": count, "chunks": pack["chunks_total"], "spec": spec,
                "empty_library_open_ms": empty_open_ms, "json_import_ms": import_ms,
                "model_load_ms": model_load_ms, "index_complete_ms": index_ms,
                "native_batches": len(calls), "native_texts": sum(calls), "rss_bytes": rss,
                "scheduler": {"batch_size": indexing.INDEX_BATCH_SIZE,
                              "round_chunks": indexing.INDEX_ROUND_CHUNKS,
                              "batch_min_pause_s": indexing.INDEX_BATCH_PAUSE_SECONDS,
                              "round_pause_s": indexing.INDEX_ROUND_PAUSE_SECONDS,
                              "target_duty": indexing.INDEX_TARGET_DUTY}}


async def _measure_read(root: Path) -> dict:
    """Measure a new interpreter opening the persisted index and issuing queries.

    OS file caches are not flushed. RSS is sampled at named milestones, not a
    process peak or model-only allocation. Twenty varied queries follow the
    first hybrid query; their raw samples and nearest-rank p95 are retained.
    """
    build = _read(root, "measure-build")
    process = psutil.Process()
    rss = {"before_library": process.memory_info().rss}
    opened = time.perf_counter()
    async with _library(root / "library") as (service, encoder, calls):
        reopen_ms = _elapsed_ms(opened)
        rss["after_cold_open"] = process.memory_info().rss
        assert not calls and encoder._session is None
        pack = (await service.list_packs())[0]
        assert pack["enabled"] is True and pack["entries"] == build["entries"]
        assert len(service._store.vectors_for_pack(pack["pack_id"], build["spec"]["model_id"])) == build["chunks"]
        started = time.perf_counter()
        await _query(service, "bm25")
        first_bm25_ms = _elapsed_ms(started)
        assert calls == []
        started = time.perf_counter()
        assert await encoder.request_load()
        model_load_ms = _elapsed_ms(started)
        assert await asyncio.to_thread(service.embedder.spec) == build["spec"]
        rss["after_model_load"] = process.memory_info().rss
        started = time.perf_counter()
        await service._indexer._refresh_vectors_now()
        snapshot_ms = _elapsed_ms(started)
        assert len(service._vectors.entry_ids) == build["chunks"]
        started = time.perf_counter()
        await _query(service, "hybrid")
        first_hybrid_ms = _elapsed_ms(started)
        samples = []
        for index in range(20):
            started = time.perf_counter()
            await _query(service, "hybrid", f"violet lighthouse station {index % build['entries']}")
            samples.append(_elapsed_ms(started))
        assert len(calls) == 21  # All inference here belongs to queries, not backfill.
        rss["after_queries"] = process.memory_info().rss
        return {"entries": build["entries"], "chunks": build["chunks"], "spec": build["spec"],
                "restart_library_open_ms": reopen_ms, "first_bm25_ms": first_bm25_ms,
                "model_load_ms": model_load_ms, "snapshot_load_ms": snapshot_ms,
                "first_hybrid_ms": first_hybrid_ms, "hybrid_samples_ms": samples,
                "hybrid_p50_ms": statistics.median(samples),
                "hybrid_p95_ms": sorted(samples)[math.ceil(len(samples) * 0.95) - 1],
                "rss_bytes": rss, "query_native_calls": len(calls),
                "environment": {"platform": platform.platform(), "python": platform.python_version(),
                                "cpu_logical": psutil.cpu_count(), "ram_bytes": psutil.virtual_memory().total,
                                "encoder_dimension": 128, "encoder_quantization": "int8",
                                "has_vnni": False, "has_avx2": False}}


def main() -> None:
    """Run exactly one stage in a root created and marked by the parent test."""
    parser = argparse.ArgumentParser(description=__doc__)
    stages = {"author": _author, "install": _install, "recover": _recover,
              "measure-build": _measure_build, "measure-read": _measure_read}
    parser.add_argument("stage", choices=stages)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    assert _read(root, "run")["kind"] == "knowledge-native-probe-v1"
    result = asyncio.run(stages[args.stage](root))
    _record(root, args.stage, result)


if __name__ == "__main__":
    main()
