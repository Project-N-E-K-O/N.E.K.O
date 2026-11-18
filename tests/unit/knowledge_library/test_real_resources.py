"""Opt-in acceptance against read-only local tokenizer and ONNX resources."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
from types import SimpleNamespace
import time

import httpx
import psutil
import pytest
from fastapi import FastAPI

from app.memory_server import library_embeddings as compute
from memory.embeddings import EmbeddingService
from services.knowledge_library.embedding_compat import project_vector
from services.knowledge_library.host import HttpEmbeddingAdapter
from services.knowledge_library.package_build import build_package
from services.knowledge_library.package_install import install_package
from services.knowledge_library.service import LibraryService


pytestmark = [pytest.mark.integration_serial, pytest.mark.skipif(
    not os.environ.get("NEKO_REAL_EMBEDDING_ROOT"),
    reason="requires an explicit read-only embedding resource root",
)]


async def test_real_tokenizer_covers_long_multilingual_tails():
    """Validate the production planner with the actual tokenizer, without ONNX."""
    path = Path(os.environ["NEKO_REAL_EMBEDDING_ROOT"]) / "local-text-retrieval-v1/tokenizer.json"
    owner = SimpleNamespace(_tokenizer_file_path=lambda: str(path))
    spec = {"max_length": 1024, "weights_sha256": {"tokenizer": hashlib.sha256(path.read_bytes()).hexdigest()}}
    inputs = [{"prefix": "reference.txt\n", "body": text} for text in ("龘靐齉爩" * 300, "🧑🏽‍💻" * 240)]
    planned = await asyncio.to_thread(compute._plan_parts, owner, spec, inputs)
    tokenizer = compute._full_tokenizer(owner, spec)
    assert any(len(tokenizer.encode(row["prefix"] + row["body"]).ids) > 1024 for row in inputs)
    for row, parts in zip(inputs, planned):
        assert "".join(row["body"][part["start"]:part["end"]] for part in parts) == row["body"]
        assert all(len(tokenizer.encode(row["prefix"] + row["body"][part["start"]:part["end"]]).ids) <= 1024
                   for part in parts)


async def test_real_onnx_build_install_and_cross_dimension_query(tmp_path, monkeypatch):
    """Exercise two sequential native sessions; installed vectors are never rebuilt."""
    root = os.environ["NEKO_REAL_EMBEDDING_ROOT"]
    quantization = os.environ.get("NEKO_REAL_EMBEDDING_QUANTIZATION", "auto")
    owner = EmbeddingService(model_dir=root, embedding_dim_setting=256, quantization_setting=quantization)
    if owner.is_disabled():
        pytest.skip(f"native hardware policy disabled this configuration: {owner._disable_reason}")
    active = {"owner": owner}
    monkeypatch.setattr(compute, "_encoder", lambda: active["owner"])
    compute._fingerprints.clear()
    app = FastAPI()
    app.include_router(compute.router)
    report = {"quantization_setting": quantization, "peak_sampled_rss_bytes": 0}
    sampling = True

    async def sample_memory():
        process = psutil.Process()
        while sampling:
            report["peak_sampled_rss_bytes"] = max(report["peak_sampled_rss_bytes"], process.memory_info().rss)
            await asyncio.sleep(0.02)

    sampler = asyncio.create_task(sample_memory())
    author = consumer = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://memory.test") as client:
            monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: client)
            adapter = HttpEmbeddingAdapter()
            first_spec = await adapter.specification()
            started = time.perf_counter()
            assert await owner.request_load()
            report["load_256_seconds"] = time.perf_counter() - started
            report["baseline_rss_bytes"] = psutil.Process().memory_info().rss
            author = LibraryService(tmp_path / "author", embedder=adapter)
            await author.start()
            library = await author.create_library("Synthetic native references")
            started = time.perf_counter()
            for filename, text in (
                ("lighthouse.txt", "The violet lighthouse stands on a rocky coast."),
                ("garden.txt", "Orange trees grow in the warm garden.\n\n花园里的橘子树需要阳光。"),
            ):
                job = await author.preview_document(library["library_id"], filename, text.encode())
                await author.jobs.queue.join()
                parsed = await author.get_job(job["job_id"])
                assert parsed["state"] == "completed", parsed
                preview = await author.get_preview(parsed["result"]["preview_id"])
                job = await author.confirm_preview(preview["preview_id"], preview["sha256"])
                await author.jobs.queue.join()
                indexed = await author.get_job(job["job_id"])
                assert indexed["state"] == "completed", indexed
                assert indexed["result"]["semantic_state"] == "ready"
            report["import_two_documents_seconds"] = time.perf_counter() - started
            snapshot = await author.freeze_library(library["library_id"], version="native-1")
            started = time.perf_counter()
            artifact = await build_package(snapshot, tmp_path / "author/artifacts/native.zip", adapter,
                                           expected_spec=first_spec)
            report["build_seconds"] = time.perf_counter() - started
            await author.close()
            author = None
            await owner.close()

            # A fresh lower-dimensional consumer has the same weights and
            # processing. Its ONNX session must remain unloaded during install.
            owner = EmbeddingService(model_dir=root, embedding_dim_setting=128, quantization_setting=quantization)
            active["owner"] = owner
            adapter = HttpEmbeddingAdapter()
            next_spec = await adapter.specification()
            started = time.perf_counter()
            prepared = await install_package(Path(artifact["artifact_path"]), tmp_path / "consumer",
                                             expected_spec=next_spec)
            assert prepared["semantic_available"]
            consumer = LibraryService(tmp_path / "consumer", embedder=adapter)
            await consumer.start()
            installed = await consumer.install_snapshot(prepared["snapshot"], installation=prepared["installation"])
            report["install_seconds"] = time.perf_counter() - started
            assert not owner.is_available(), "install unexpectedly loaded the query model"
            query = prepared["snapshot"]["chunks"][0]["embedding_text"]
            started = time.perf_counter()
            result = await consumer.search(installed["library_id"], query)
            report["cold_query_seconds"] = time.perf_counter() - started
            assert result["semantic_state"] == "ready" and result["hits"]
            for question, filename in (
                ("Where is the navigation beacon located?", "lighthouse.txt"),
                ("什么地方适合种植果树？", "garden.txt"),
            ):
                found = await consumer.search(installed["library_id"], question)
                assert found["hits"] and found["hits"][0]["filename"] == filename, [
                    (hit["filename"], hit["score"], hit["match_type"]) for hit in found["hits"]
                ]
                assert found["hits"][0]["match_type"] in {"semantic", "hybrid"}, found
            report["synthetic_semantic_top1_checks"] = 2
            query_vector = (await adapter.embed_batch([query], expected_spec=next_spec))[0]
            stored = prepared["snapshot"]["chunks"][0]["vector"]
            prefix = project_vector(stored, 128)
            report["same_cpu_prefix_max_abs_error"] = max(abs(a - b) for a, b in zip(prefix, query_vector))
            # This checks processing equivalence on one CPU, not a recall or
            # cross-ISA compatibility threshold for arbitrary corpora.
            assert report["same_cpu_prefix_max_abs_error"] < 1e-5
            assert hashlib.sha256(Path(artifact["artifact_path"]).read_bytes()).hexdigest() == artifact["sha256"]
            report.update(author_dimension=256, consumer_dimension=128,
                          chunk_count=len(prepared["snapshot"]["chunks"]),
                          artifact_bytes=artifact["size"], model_id=next_spec["model_id"])
    finally:
        if author is not None:
            await author.close()
        if consumer is not None:
            await consumer.close()
        await owner.close()
        sampling = False
        await sampler
    output = Path(os.environ.get("NEKO_REAL_KB_REPORT", str(tmp_path / "resources.json")))
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


@pytest.mark.skipif(not os.environ.get("NEKO_REAL_KB_BENCHMARK"), reason="requires explicit typical-package benchmark opt-in")
async def test_typical_package_native_resource_budget(tmp_path, monkeypatch):
    """Measure the documented 50-document fixture without touching user data."""
    owner = EmbeddingService(model_dir=os.environ["NEKO_REAL_EMBEDDING_ROOT"], embedding_dim_setting=256)
    if owner.is_disabled():
        pytest.skip(f"native hardware policy disabled this configuration: {owner._disable_reason}")
    monkeypatch.setattr(compute, "_encoder", lambda: owner)
    app = FastAPI()
    app.include_router(compute.router)
    process = psutil.Process()
    cpu_started = process.cpu_times()
    report = {"documents": 50, "peak_sampled_rss_bytes": 0, "peak_sampled_library_disk_bytes": 0,
              "peak_sampled_cpu_percent": 0, "os": platform.system(), "architecture": platform.machine(),
              "total_ram_bytes": psutil.virtual_memory().total, "logical_cpus": psutil.cpu_count()}
    observing, probing = True, False
    foreground = []
    author = consumer = None

    async def sample_resources():
        process = psutil.Process()
        while observing:
            report["peak_sampled_rss_bytes"] = max(report["peak_sampled_rss_bytes"], process.memory_info().rss)
            # psutil reports a process's cumulative percentage across cores;
            # this value can exceed 100 and is not a whole-machine percentage.
            report["peak_sampled_cpu_percent"] = max(report["peak_sampled_cpu_percent"], process.cpu_percent())
            size = 0
            for item in tmp_path.rglob("*"):
                try:
                    if item.is_file():
                        size += item.stat().st_size
                except FileNotFoundError:
                    pass  # An atomic staging file can disappear during sampling.
            report["peak_sampled_library_disk_bytes"] = max(report["peak_sampled_library_disk_bytes"], size)
            await asyncio.sleep(0.1)

    async def probe_memory_queries():
        while probing:
            started = time.perf_counter()
            assert await owner.embed("Where is the coastal lighthouse?", sticky_failure=False) is not None
            foreground.append(time.perf_counter() - started)
            await asyncio.sleep(0.05)

    sampler = asyncio.create_task(sample_resources())
    probe = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://memory.test") as client:
            monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: client)
            adapter = HttpEmbeddingAdapter()
            specification = await adapter.specification()
            started = time.perf_counter()
            assert await owner.request_load()
            report["load_seconds"] = time.perf_counter() - started
            report["loaded_baseline_rss_bytes"] = psutil.Process().memory_info().rss
            baseline = []
            for _ in range(10):
                started = time.perf_counter()
                assert await owner.embed("Where is the coastal lighthouse?", sticky_failure=False) is not None
                baseline.append(time.perf_counter() - started)
            report["idle_query_p95_seconds"] = sorted(baseline)[int((len(baseline) - 1) * 0.95)]
            author = LibraryService(tmp_path / "author", embedder=adapter)
            await author.start()
            library = await author.create_library("Synthetic typical package")
            started = time.perf_counter()
            for index in range(50):
                paragraphs = []
                for paragraph in range(40):
                    if index % 2:
                        seed = f"资料{index + 1}段落{paragraph + 1}。海岸灯塔用于航海定位，花园中的橘子树需要阳光，知识库保存来源。"
                    else:
                        seed = f"Document {index + 1}, paragraph {paragraph + 1}. The coastal lighthouse guides navigation and orange trees grow in the sunny garden. "
                    paragraphs.append((seed * (500 // len(seed) + 1))[:500].strip())
                job = await author.preview_document(library["library_id"], f"reference-{index + 1}.txt", "\n\n".join(paragraphs).encode())
                await author.jobs.queue.join()
                parsed = await author.get_job(job["job_id"])
                assert parsed["state"] == "completed", parsed
                preview = await author.get_preview(parsed["result"]["preview_id"])
                job = await author.confirm_preview(preview["preview_id"], preview["sha256"])
                await author.jobs.queue.join()
                indexed = await author.get_job(job["job_id"])
                assert indexed["state"] == "completed" and indexed["result"]["semantic_state"] == "ready", indexed
                if (index + 1) % 10 == 0:
                    print(json.dumps({"imported_documents": index + 1}), flush=True)
            report["import_seconds"] = time.perf_counter() - started
            snapshot = await author.freeze_library(library["library_id"], version="benchmark-1")
            report["characters"] = sum(len(document["content"]) for document in snapshot["documents"])
            report["chunks"] = len(snapshot["chunks"])
            probing = True
            probe = asyncio.create_task(probe_memory_queries())
            started = time.perf_counter()
            artifact = await build_package(snapshot, tmp_path / "author/artifacts/build.zip", adapter,
                                           expected_spec=specification)
            report["build_seconds"] = time.perf_counter() - started
            probing = False
            await probe
            foreground.sort()
            report["building_query_samples"] = len(foreground)
            report["building_query_p95_seconds"] = foreground[int((len(foreground) - 1) * 0.95)]
            report["building_query_max_seconds"] = foreground[-1]
            started = time.perf_counter()
            prepared = await install_package(Path(artifact["artifact_path"]), tmp_path / "consumer",
                                             expected_spec=specification)
            consumer = LibraryService(tmp_path / "consumer")
            await consumer.start()
            installed = await consumer.install_snapshot(prepared["snapshot"], installation=prepared["installation"])
            report["install_seconds"] = time.perf_counter() - started
            manifest = consumer.store.manifest(installed["library_id"])
            consumer.store._index_path(manifest).unlink()
            started = time.perf_counter()
            assert (await consumer.search(installed["library_id"], "lighthouse"))["hits"]
            report["index_restore_seconds"] = time.perf_counter() - started
            report.update(artifact_bytes=artifact["size"], model_id=specification["model_id"])
    finally:
        probing = False
        if probe is not None:
            try:
                await probe
            except (Exception, asyncio.CancelledError):
                pass  # Preserve the primary failure while still draining all owners.
        if author is not None:
            await author.close()
        if consumer is not None:
            await consumer.close()
        await owner.close()
        observing = False
        await sampler
        cpu_finished = process.cpu_times()
        report["process_cpu_seconds"] = cpu_finished.user + cpu_finished.system - cpu_started.user - cpu_started.system
    output = Path(os.environ.get("NEKO_REAL_KB_BENCHMARK_REPORT", str(tmp_path / "typical-resources.json")))
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)
