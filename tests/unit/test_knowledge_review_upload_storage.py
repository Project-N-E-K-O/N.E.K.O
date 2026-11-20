"""Distinguish local upload storage faults from malformed browser input."""

import asyncio
import errno
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from knowledge.service import KnowledgeService

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("upload", ["json", "multipart", "source"])
@pytest.mark.parametrize("operation", ["mkdir", "open", "write", "close"])
async def test_spool_storage_failures_are_server_errors(tmp_path, monkeypatch, caplog, upload, operation):
    """A failed scratch write neither admits work nor blames the uploaded file."""
    service = KnowledgeService(tmp_path / "library")
    await service.start()
    try:
        monkeypatch.setattr(knowledge_routes, "_service", service)
        monkeypatch.setattr(knowledge_routes, "_import_slots", asyncio.Semaphore(2))
        run = service._thread
        failures = []
        entered = []

        async def fail_spool(func, *args, **kwargs):
            target = getattr(func, "__self__", None)
            name = getattr(func, "__name__", "")
            path = target if isinstance(target, Path) else getattr(target, "name", "")
            scratch = ".staging" in Path(path).parts if isinstance(path, (str, Path)) else False
            if scratch and name == operation and not failures:
                failures.append(name)
                # Close the real descriptor before simulating a failed flush.
                if operation == "close":
                    await run(func, *args, **kwargs)
                raise OSError(errno.ENOSPC, "private filename and document contents")
            return await run(func, *args, **kwargs)

        async def forbidden(*_args, **_kwargs):
            entered.append(True)
            raise AssertionError("A failed upload must not reach parsing/admission")

        monkeypatch.setattr(service, "_thread", fail_spool)
        monkeypatch.setattr(service, "import_pack", forbidden)
        monkeypatch.setattr(service, "preview_source", forbidden)
        app = FastAPI()
        app.include_router(knowledge_routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
            if upload == "source":
                response = await client.post("/internal/knowledge/sources/preview", content=b"private input",
                                             headers={"Content-Type": "application/octet-stream", "X-Knowledge-Filename": "private.txt"})
            elif upload == "multipart":
                response = await client.post("/internal/knowledge/packs/import", files={"pack": ("private.json", b"{}", "application/json")})
            else:
                response = await client.post("/internal/knowledge/packs/import", content=b"{}", headers={"Content-Type": "application/json"})
        assert response.status_code == 500
        assert response.json() == {"ok": False, "reason": "knowledge_error"}
        assert failures == [operation] and not entered
        assert not list((service.root / ".staging").glob("*"))
        assert "private filename and document contents" not in caplog.text
    finally:
        await service.stop()
