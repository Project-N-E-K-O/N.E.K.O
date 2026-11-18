"""Keep export/publication review bound to validated immutable package bytes."""

import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from main_routers import knowledge_library_router as routes
from services.knowledge_library.models import LibraryError
from tests.unit.knowledge_library.test_packages import _built


pytestmark = [pytest.mark.unit, pytest.mark.unit_fast]


async def test_scope_preview_comes_from_artifact_and_excludes_private_runtime_data(tmp_path):
    """Return only selected source metadata, never whole documents or vectors."""
    from services.knowledge_library.package_install import package_scope

    artifact = await _built(tmp_path)
    result = package_scope(tmp_path, artifact["artifact_id"])
    assert result["artifact_id"] == artifact["artifact_id"] == result["sha256"]
    assert result["source"] == {"author": "Author", "license": "CC0-1.0", "url": ""}
    assert [row["name"] for row in result["documents"]] == ["note-0.txt", "note-1.txt"]
    assert all(set(row) == {"document_id", "name", "version", "characters"} for row in result["documents"])
    assert all(row["characters"] > 0 for row in result["documents"])
    rendered = str(result)
    assert "blue lighthouse" not in rendered
    assert "vector" not in rendered and str(tmp_path) not in rendered


async def test_scope_preview_rechecks_digest_and_cannot_use_a_missing_artifact(tmp_path):
    """The review must fail closed when the bytes no longer match their identity."""
    from services.knowledge_library.package_install import package_scope

    artifact = await _built(tmp_path)
    from pathlib import Path
    Path(artifact["artifact_path"]).write_bytes(b"replaced bytes")
    with pytest.raises(LibraryError, match="package_artifact_hash_mismatch"):
        package_scope(tmp_path, artifact["artifact_id"])
    with pytest.raises(LibraryError, match="package_not_found"):
        package_scope(tmp_path, "f" * 64)


async def test_scope_http_is_private_metadata_and_does_not_depend_on_current_library(tmp_path, monkeypatch):
    """A library switch cannot change the content range reviewed for this digest."""
    artifact = await _built(tmp_path)

    async def get_host(_request):
        return SimpleNamespace(root=tmp_path, upload_slots=asyncio.Semaphore(2))

    monkeypatch.setattr(routes, "get_host", get_host)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        result = await http.get(f"/api/knowledge-library/packages/{artifact['artifact_id']}/preview")
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["package"]["artifact_id"] == artifact["artifact_id"]
    assert result.json()["package"]["counts"] == {"documents": 2, "chunks": 2}


async def test_scope_admission_rejects_parallel_work_and_drains_cancelled_readers(tmp_path, monkeypatch):
    """A cancelled browser cannot release a large validation's slot early."""
    host = SimpleNamespace(root=tmp_path, upload_slots=asyncio.Semaphore(1))
    started, release = threading.Event(), threading.Event()
    calls = []

    async def get_host(_request):
        return host

    def scope(_root, artifact_id):
        calls.append(artifact_id)
        started.set()
        assert release.wait(5), "validation worker was not released"
        return {"artifact_id": artifact_id}

    monkeypatch.setattr(routes, "get_host", get_host)
    monkeypatch.setattr(routes, "package_scope", scope)
    app = FastAPI()
    app.include_router(routes.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        request = asyncio.create_task(http.get("/api/knowledge-library/packages/one/preview"))
        try:
            assert await asyncio.to_thread(started.wait, 2)
            refused = await http.get("/api/knowledge-library/packages/two/preview")
            assert refused.status_code == 429 and refused.json()["reason"] == "jobs_busy"
            request.cancel()
            await asyncio.sleep(0)
            assert host.upload_slots.locked()
            refused = await http.get("/api/knowledge-library/packages/three/preview")
            assert refused.status_code == 429 and calls == ["one"]
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await request
        assert not host.upload_slots.locked()
        assert (await http.get("/api/knowledge-library/packages/four/preview")).status_code == 200
        assert calls == ["one", "four"]
