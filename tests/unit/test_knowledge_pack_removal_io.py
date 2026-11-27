"""A failed visibility update cannot report successful whole-pack removal."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.memory_server import knowledge_routes
from services.knowledge.registry import load_registry
from services.knowledge.service import KnowledgeService
from tests.knowledge_fixtures import _finished, _import, _pack


pytestmark = pytest.mark.unit_fast


@pytest.mark.parametrize("error_type", [OSError, PermissionError])
async def test_unreadable_retention_manifest_keeps_the_pack_until_a_successful_removal(tmp_path, monkeypatch, error_type):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        preview = (await service.preview_source("notes.txt", b"Lighthouse reference."))["preview"]
        initial = await service.commit_source(preview["preview_id"], preview["sha256"])
        assert (await _finished(service, initial))["state"] == "active"
        pack_id = initial["pack_id"]
        assert (await service.remove_source(pack_id, preview["doc_id"]))["state"] == "active"
        manifest = tmp_path / "sources" / pack_id / "manifest.json"
        assert json.loads(manifest.read_text())["retained"] is True
        assert (await _finished(service, await service.restore_source(pack_id, preview["doc_id"], 1)))["state"] == "active"
        # Restoration preserves historical visibility metadata. The subsequent
        # whole-pack removal must durably turn that retained marker off.
        assert json.loads(manifest.read_text())["retained"] is True
        before = service._registry.packs[pack_id]
        retained = {path: path.read_bytes() for path in manifest.parent.rglob("*") if path.is_file()}
        open_file = Path.open

        def unavailable(path, *args, **kwargs):
            if path == manifest:
                raise error_type("injected temporary read failure")
            return open_file(path, *args, **kwargs)

        monkeypatch.setattr(knowledge_routes, "_service", service)
        app = FastAPI()
        app.include_router(knowledge_routes.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            with monkeypatch.context() as patch:
                patch.setattr(Path, "open", unavailable)
                failed = await client.post("/internal/knowledge/packs/remove", json={"pack_id": pack_id})
            assert failed.status_code == 500 and failed.json()["reason"] == "knowledge_error"
            assert service._registry.packs[pack_id] == before
            assert load_registry(tmp_path).packs[pack_id] == before
            assert all(path.read_bytes() == raw for path, raw in retained.items())
            assert any(row["pack_id"] == pack_id and not row.get("retained") for row in await service.list_packs())
            assert (await service.source_versions(pack_id, preview["doc_id"]))["versions"][0]["current"] is True
            removed = await client.post("/internal/knowledge/packs/remove", json={"pack_id": pack_id})
            assert removed.status_code == 200 and removed.json()["ok"]
        assert json.loads(manifest.read_text())["retained"] is False
        assert await service.list_packs() == []
        assert all(path.read_bytes() == raw for path, raw in retained.items() if path != manifest)
        await service.stop()
        service = KnowledgeService(tmp_path)
        await service.start()
        assert await service.list_packs() == []
        assert all(path.read_bytes() == raw for path, raw in retained.items() if path != manifest)
    finally:
        await service.stop()


@pytest.mark.parametrize("sidecar", [None, "{broken-json", "[]"])
async def test_ordinary_packs_can_be_removed_without_usable_source_sidecars(tmp_path, sidecar):
    service = KnowledgeService(tmp_path)
    await service.start()
    try:
        pack_id = "retention.reference"
        await _import(service, _pack(pack_id))
        manifest = tmp_path / "sources" / pack_id / "manifest.json"
        if sidecar is not None:
            manifest.parent.mkdir(parents=True)
            manifest.write_text(sidecar)
        assert (await service.remove_pack(pack_id))["pack_id"] == pack_id
        assert pack_id not in load_registry(tmp_path).packs
        assert await service.list_packs() == []
        if sidecar is not None:
            assert manifest.read_text() == sidecar
    finally:
        await service.stop()
