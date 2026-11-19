"""Opt in to an isolated native-encoder import, index and archive round trip."""

import asyncio
import os
import sqlite3
from pathlib import Path

import pytest

from app.memory_server.knowledge_routes import _SharedEmbedder
from knowledge.service import KnowledgeService
from memory.embeddings import EmbeddingService
from utils.knowledge_package import validate_package

pytestmark = pytest.mark.integration_serial


async def _terminal(service, result):
    """Observe the durable import runner rather than assuming a sleep finished it."""
    async with asyncio.timeout(30):
        while True:
            job = next(row for row in service.list_jobs() if row["job_id"] == result["job_id"])
            if job["state"] not in {"queued", "building"}:
                assert job["state"] == "active", job
                return job
            await asyncio.sleep(0.01)


async def _manual_backfill(service):
    """Keep real imports while making the native index round observable."""
    service._tasks[1].cancel()
    await asyncio.gather(service._tasks[1], return_exceptions=True)


async def test_real_encoder_cold_import_backfill_and_vector_reuse(tmp_path, monkeypatch):
    model_root = os.environ.get("NEKO_REAL_EMBEDDING_ROOT")
    if not model_root:
        pytest.skip("set NEKO_REAL_EMBEDDING_ROOT to existing read-only native assets")
    assert Path(model_root).is_dir()
    encoder = EmbeddingService(model_dir=model_root, enabled=True, ram_gb=8,
                               embedding_dim_setting=128, quantization_setting="int8",
                               has_vnni=False, has_avx2=False)
    library = KnowledgeService(tmp_path / "author", embedder=_SharedEmbedder(encoder))
    installed = None
    calls = []
    original = encoder._infer_blocking

    def count_native(texts):
        calls.append(len(texts))
        return original(texts)

    monkeypatch.setattr(encoder, "_infer_blocking", count_native)
    try:
        # Resource fingerprints and full token counts must leave ONNX cold.
        spec = await asyncio.to_thread(encoder.spec)
        assert spec and spec["dimension"] == 128
        assert (await asyncio.to_thread(encoder.count_tokens, ["lighthouse " * 1500]))[0] > 1024
        assert not encoder.is_available() and encoder._session is None
        assert (await encoder.embed_ex(["cold query"])).status == "unavailable"
        await library.start()
        await _manual_backfill(library)
        preview = await library.preview_source("synthetic-coast.txt", (
            "Lighthouses guide ships along the coast. A violet beacon marks the harbour. " * 30
        ).encode(), name="Synthetic coastal reference")
        source = preview["preview"]
        await _terminal(library, await library.commit_source(source["preview_id"], source["sha256"]))
        pack = (await library.list_packs())[0]
        assert pack["enabled"] is False and not pack["auto_context"]
        assert await library._index_round() == 0
        with sqlite3.connect(library._store.database_path) as database:
            assert database.execute("SELECT MAX(attempts) FROM chunks").fetchone()[0] == 0
        assert not calls and encoder._session is None

        # Only this explicit test warmup loads the owner's model, as memory's
        # startup worker would. Neither knowledge import nor query loads it.
        assert await encoder.request_load()
        await library._index_round()
        rows = library._store.vectors_for_pack(pack["pack_id"], encoder.model_id())
        assert rows and len(rows) == pack["chunks_total"]
        assert all(size <= 1024 for size in await asyncio.to_thread(
            encoder.count_tokens, [row["embedding_text"] for row in rows]))
        before_export = len(calls)
        exported = await _terminal(library, await library.export_pack(pack["pack_id"], format_version=2))
        archive = library.artifact_path(exported["result"]["artifact_id"])
        checked = await asyncio.to_thread(validate_package, archive, expected_spec=spec)
        assert checked.semantic_available and len(checked.chunks) == len(rows)
        assert len(calls) == before_export

        installed = KnowledgeService(tmp_path / "reader", embedder=_SharedEmbedder(encoder))
        await installed.start()
        await _manual_backfill(installed)
        await _terminal(installed, await installed.import_pack(archive))
        assert len(installed._store.vectors_for_pack(pack["pack_id"], encoder.model_id())) == len(rows)
        assert await installed._index_round() == 0
        assert len(calls) == before_export  # No re-encoding during either operation.
        await installed.set_pack_enabled(pack["pack_id"], True)
        result = await installed.query(query="lighthouses", budget_ms=5000)
        assert result["result"] == "matched"
        assert encoder.status_counts().get("ok", 0) > 0
    finally:
        if installed is not None:
            await installed.stop()
        await library.stop()
        await encoder.close()
