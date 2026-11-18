"""Opt-in real artifact capacity checks with explicitly synthetic vectors.

Run serially with NEKO_KB_CAPACITY_OUTPUT_ROOT pointing to an empty temporary
directory. These measure file validation, persistence and retrieval resources;
they do not measure ONNX encoding time or the quality of a learned embedding.
"""

from contextlib import contextmanager
from copy import deepcopy
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import subprocess
import tempfile
import threading
import time

import psutil
import pytest

from services.knowledge_library.indexing import make_chunks
from services.knowledge_library.jobs import run_sync
from services.knowledge_library.models import LibraryError
from services.knowledge_library.package_build import build_package
from services.knowledge_library.package_install import install_package
from services.knowledge_library.package_validate import CHUNKING_VERSION, PROCESSING_VERSION, validate_package
from services.knowledge_library import package_validate
from services.knowledge_library.persistence import MAX_METADATA_BYTES
from services.knowledge_library.service import LibraryService


pytestmark = [pytest.mark.integration_serial, pytest.mark.skipif(
    not os.environ.get("NEKO_KB_CAPACITY_OUTPUT_ROOT"),
    reason="requires an explicit temporary output root and a serial resource run",
)]


def _logical_bytes(root):
    """Count generated file lengths without following symlinks outside the case."""
    total = 0
    for directory, _children, names in os.walk(root):
        for name in names:
            path = Path(directory) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                pass  # Atomic staging files can disappear between list and stat.
    return total


class _Resources:
    """Sample this one process and its temporary files, including staging peaks."""

    def __init__(self, root):
        self.root = root
        self.process = psutil.Process()
        self.stop = threading.Event()
        self.phases = {}
        self.current = None
        self.peak_rss = self.process.memory_info().rss
        self.peak_disk = 0
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self):
        while not self.stop.is_set():
            rss, disk = self.process.memory_info().rss, _logical_bytes(self.root)
            self.peak_rss, self.peak_disk = max(self.peak_rss, rss), max(self.peak_disk, disk)
            phase = self.current
            if phase is not None:
                phase["peak_process_rss_bytes"] = max(phase["peak_process_rss_bytes"], rss)
                phase["peak_logical_disk_bytes"] = max(phase["peak_logical_disk_bytes"], disk)
            self.stop.wait(0.05)

    @contextmanager
    def phase(self, name):
        entry = {"peak_process_rss_bytes": self.process.memory_info().rss,
                 "peak_logical_disk_bytes": _logical_bytes(self.root)}
        self.phases[name] = self.current = entry
        started = time.perf_counter()
        try:
            yield
        finally:
            entry["seconds"] = time.perf_counter() - started
            entry["end_process_rss_bytes"] = self.process.memory_info().rss
            entry["end_logical_disk_bytes"] = _logical_bytes(self.root)
            entry["peak_process_rss_bytes"] = max(entry["peak_process_rss_bytes"], entry["end_process_rss_bytes"])
            entry["peak_logical_disk_bytes"] = max(entry["peak_logical_disk_bytes"], entry["end_logical_disk_bytes"])
            self.current = None


@contextmanager
def _case(name):
    """Keep resource artifacts and reports only under an explicit temporary root."""
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.skip("capacity probes require one pytest process, without xdist")
    base = Path(os.environ["NEKO_KB_CAPACITY_OUTPUT_ROOT"]).resolve()
    allowed = [Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()]
    assert any(base.is_relative_to(root) for root in allowed), "capacity output must be a temporary directory"
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=name + "-", dir=base))
    cpu = platform.processor() or platform.machine()
    if platform.system() == "Darwin":
        cpu = subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True, encoding="utf-8").strip()
    resources = _Resources(root)
    report = {"case": name, "cpu": cpu, "platform": platform.platform(),
              "total_ram_bytes": psutil.virtual_memory().total,
              "baseline_process_rss_bytes": resources.peak_rss, "root": str(root),
              "vectors": "synthetic normalized fixtures; no native model or retrieval-quality measurement",
              "restart_mode": "same-process domain service restart, not process-kill or power-loss acceptance",
              "validator_source_sha256": hashlib.sha256(Path(package_validate.__file__).read_bytes()).hexdigest(),
              "sampling_interval_seconds": 0.05, "phases": resources.phases}
    resources.thread.start()
    try:
        yield root, resources, report
        report["status"] = "passed"
    except BaseException as error:
        report.update(status="failed", error=type(error).__name__, reason=getattr(error, "code", None))
        raise
    finally:
        resources.stop.set()
        resources.thread.join(3)
        assert not resources.thread.is_alive(), "capacity sampler did not stop"
        report.update(peak_process_rss_bytes=max(resources.peak_rss, resources.process.memory_info().rss),
                      peak_logical_disk_bytes=max(resources.peak_disk, _logical_bytes(root)))
        path = root / "resource-report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("CAPACITY_RESOURCE_REPORT=" + str(path))


class _SyntheticEncoder:
    """Generate bounded normalized data, explicitly not a language encoder."""

    def __init__(self, dimension, *, uniform=False):
        self.dimension, self.uniform, self.encoded_rows = dimension, uniform, 0
        self.spec = {"model_id": f"local-text-retrieval-v1-{dimension}d-int8-mlen1024",
                     "profile": "local-text-retrieval-v1", "dimension": dimension,
                     "quantization": "int8", "max_length": 1024, "processing_version": PROCESSING_VERSION,
                     "weights_sha256": {role: hashlib.sha256(("synthetic-capacity-" + role).encode()).hexdigest()
                                        for role in ("model", "external_data", "tokenizer")}}

    async def specification(self):
        return deepcopy(self.spec)

    async def plan_inputs(self, inputs, *, expected_spec=None):
        assert expected_spec == self.spec
        return [[{"start": 0, "end": len(row["body"])}] for row in inputs]

    async def embed_background(self, texts, *, expected_spec=None):
        assert expected_spec == self.spec
        self.encoded_rows += len(texts)
        result = []
        for text in texts:
            if self.uniform:
                row = [1 / math.sqrt(self.dimension)] * self.dimension
            else:
                rng = random.Random(int.from_bytes(hashlib.sha256(text.encode()).digest()[:8]))
                row = [rng.uniform(-1, 1) for _ in range(self.dimension)]
                norm = math.sqrt(sum(value * value for value in row))
                row = [value / norm for value in row]
            result.append(row)
        return result


class _NoInference:
    """Expose unavailable queries and make any actual encoding observable."""

    def __init__(self):
        self.spec_calls, self.embed_calls = 0, 0

    async def specification(self):
        self.spec_calls += 1
        raise LibraryError("embedding_unavailable")

    async def embed_batch(self, *_args, **_kwargs):
        self.embed_calls += 1
        raise AssertionError("capacity install/recovery must never encode documents")

    async def embed_background(self, *_args, **_kwargs):
        self.embed_calls += 1
        raise AssertionError("capacity install/recovery must never encode documents")


def _fixture(documents, chunks_per_document, version):
    """Create honest source spans within the unchanged production size limits."""
    rows, chunks = [], []
    for document in range(documents):
        identity, name = f"resource-document-{document:03}", f"resource-{document:03}.txt"
        # Ten thousand paragraphs must also fit the unchanged single-document
        # character limit in the large-vector rejection case.
        suffix = "" if chunks_per_document > 1000 else (
            " A deterministic source paragraph retained for installation and recovery resource checks."
        )
        sections = [{"text": f"capacitytoken{document * chunks_per_document + index:05}" + suffix,
                     "location": {"paragraph": index + 1}} for index in range(chunks_per_document)]
        content = "\n\n".join(section["text"] for section in sections)
        rows.append({"document_id": identity, "name": name, "version": "source-1",
                     "sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content})
        chunks.extend(make_chunks(identity, name, sections))
    return {"snapshot_id": "resource-snapshot", "library_id": "resource-author",
            "package_id": "synthetic-capacity-work", "name": "Synthetic capacity fixture", "version": version,
            "source": {"author": "Automated resource test", "license": "CC0-1.0", "url": ""},
            "chunking_version": CHUNKING_VERSION, "documents": rows, "chunks": chunks}


async def test_ten_thousand_256d_chunks_install_search_restart_and_restore_without_encoding():
    with _case("10000-chunks-256d") as (root, resources, report):
        encoder, unavailable = _SyntheticEncoder(256), _NoInference()
        snapshot = _fixture(100, 100, "capacity-256-1")
        report.update(documents=100, chunks=10000, dimension=256,
                      total_text_characters=sum(len(row["content"]) for row in snapshot["documents"]))
        with resources.phase("synthetic_vector_build"):
            artifact = await build_package(snapshot, root / "author/artifacts/build.zip", encoder,
                                           expected_spec=encoder.spec)
        assert encoder.encoded_rows == 10000
        report["artifact_bytes"] = artifact["size"]
        del snapshot
        gc.collect()
        with resources.phase("validate"):
            validated = await run_sync(validate_package, Path(artifact["artifact_path"]))
            assert len(validated.documents) == 100 and len(validated.chunks) == 10000
        del validated
        service = LibraryService(root / "consumer", embedder=unavailable)
        await service.start()
        try:
            with resources.phase("prepare_install"):
                prepared = await install_package(Path(artifact["artifact_path"]), root / "consumer", expected_spec=encoder.spec)
            with resources.phase("activate_install"):
                installed = await service.install_snapshot(prepared["snapshot"], installation=prepared["installation"])
            assert unavailable.spec_calls == unavailable.embed_calls == 0
            identifier = installed["library_id"]
            del prepared
            gc.collect()
            with resources.phase("keyword_search"):
                result = await service.search(identifier, "capacitytoken09999")
            assert result["hits"][0]["document_id"] == "resource-document-099"
            saved = await service.get_document(identifier, "resource-document-099")
            expected_vector = saved["chunks"][-1]["vector"][:]
            del saved
            with resources.phase("synthetic_query_vector_search"):
                result = await run_sync(service.store.search, identifier, "capacitytoken09999", expected_vector, encoder.spec, 5)
            assert result["hits"][0]["match_type"] == "hybrid"
            with resources.phase("restart"):
                await service.close()
                service = LibraryService(root / "consumer", embedder=unavailable)
                await service.start()
            manifest = service.store.manifest(identifier)
            index = service.store._index_path(manifest)
            assert index.is_file()
            index.unlink()  # Only this isolated test's disposable derived index is removed.
            with resources.phase("index_restore_and_keyword_search"):
                restored = await service.search(identifier, "capacitytoken09999")
            assert index.is_file() and restored["hits"][0]["document_id"] == "resource-document-099"
            saved = await service.get_document(identifier, "resource-document-099")
            assert saved["chunks"][-1]["vector"] == expected_vector
            assert unavailable.embed_calls == 0
            report.update(consumer_encoding_calls=unavailable.embed_calls, query_availability_checks=unavailable.spec_calls)
        finally:
            await service.close()


async def test_legal_768d_package_exceeding_json_capacity_preserves_previous_install():
    with _case("10000-chunks-768d-rejection") as (root, resources, report):
        unavailable = _NoInference()
        service = LibraryService(root / "consumer", embedder=unavailable)
        await service.start()
        try:
            old_encoder = _SyntheticEncoder(64)
            old = await build_package(_fixture(1, 2, "safe-before-large-update"), root / "author/artifacts/old.zip", old_encoder)
            prepared = await install_package(Path(old["artifact_path"]), root / "consumer", expected_spec=old_encoder.spec)
            installed = await service.install_snapshot(prepared["snapshot"], installation=prepared["installation"])
            identifier = installed["library_id"]
            manifest_path = service.store._path(identifier)
            previous_manifest = manifest_path.read_bytes()
            previous_document = await service.get_document(identifier, "resource-document-000")
            encoder = _SyntheticEncoder(768, uniform=True)
            with resources.phase("synthetic_large_vector_build"):
                artifact = await build_package(_fixture(1, 10000, "oversized-local-json"), root / "author/artifacts/large.zip", encoder)
            report.update(documents=1, chunks=10000, dimension=768, artifact_bytes=artifact["size"],
                          vector_bytes=10000 * 768 * 4, formal_json_read_limit_bytes=MAX_METADATA_BYTES)
            with resources.phase("validate_legal_large_package"):
                validated = await run_sync(validate_package, Path(artifact["artifact_path"]))
                assert len(validated.documents) == 1 and len(validated.chunks) == 10000
            del validated, prepared
            gc.collect()
            with resources.phase("prepare_large_install"):
                prepared = await install_package(Path(artifact["artifact_path"]), root / "consumer", expected_spec=encoder.spec)
            with resources.phase("reject_unreadable_candidate_before_activation"):
                with pytest.raises(LibraryError, match="metadata_too_large"):
                    await service.install_snapshot(prepared["snapshot"], installation=prepared["installation"], library_id=identifier)
            assert manifest_path.read_bytes() == previous_manifest
            assert await service.get_document(identifier, "resource-document-000") == previous_document
            del prepared
            gc.collect()
            with resources.phase("restart_after_rejected_update"):
                await service.close()
                service = LibraryService(root / "consumer", embedder=unavailable)
                await service.start()
                assert await service.get_document(identifier, "resource-document-000") == previous_document
            assert unavailable.spec_calls == unavailable.embed_calls == 0
            assert not list((root / "consumer").rglob(".record-*.tmp"))
            report.update(consumer_encoding_calls=unavailable.embed_calls, previous_manifest_preserved=True,
                          previous_text_and_vectors_preserved=True, rejection_reason="metadata_too_large")
        finally:
            await service.close()
