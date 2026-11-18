"""Preserve committed directory-sync warnings through both real install APIs."""

import json
from pathlib import Path

import httpx
import pytest

from main_routers import knowledge_library_market_router as market
from services.knowledge_library import persistence
from tests.unit.knowledge_library.test_market_routes import complete, scenario as scenario, transport
from tests.unit.knowledge_library.test_packages import _built
from tests.unit.knowledge_library.test_routes import HEADERS, client as client, wait_job


pytestmark = pytest.mark.unit_fast


def _fail_sync_after_installed_manifest(monkeypatch, root):
    """Fail only after an installed manifest has visibly replaced its pointer."""
    sync = persistence.sync_directory
    failures = []

    def flush(directory):
        manifest_path = directory / "manifest.json"
        if directory.parent == root / "libraries" and manifest_path.is_file():
            record = json.loads(manifest_path.read_bytes())
            if record.get("kind") == "installed" and record["revision"] > 0:
                failures.append(manifest_path)
                raise OSError("private storage diagnostic must not replace the committed outcome")
        return sync(directory)

    monkeypatch.setattr(persistence, "sync_directory", flush)
    return failures


def _assert_active_without_persisted_warning(root, result, failures):
    """The response warning is transient; the saved installation is authoritative."""
    assert result["warnings"] == ["storage_sync_unconfirmed"]
    manifest_path = root / "libraries" / result["library_id"] / "manifest.json"
    assert failures == [manifest_path]
    saved = json.loads(manifest_path.read_bytes())
    assert saved["kind"] == "installed" and saved["revision"] == 1
    assert saved["documents"] and "warnings" not in saved
    assert saved["installation"]["artifact_id"] == result["artifact_id"]


async def test_offline_install_task_preserves_post_commit_sync_warning(client, monkeypatch):
    """Exercise upload, retained package, activation and the local thin runner."""
    http, app, root = client
    artifact = await _built(root / "inputs")
    path = Path(artifact["artifact_path"])
    failures = _fail_sync_after_installed_manifest(monkeypatch, root / "knowledge_library")
    response = await http.post(
        "/api/knowledge-library/packages/install", headers=HEADERS,
        files={"file": (path.name, path.read_bytes(), "application/zip")},
    )
    assert response.status_code == 200, response.text
    result = await wait_job(http, response.json()["job"])
    _assert_active_without_persisted_warning(root / "knowledge_library", result, failures)
    detail = await http.get("/api/knowledge-library/libraries/" + result["library_id"])
    assert detail.status_code == 200 and detail.json()["documents"]
    assert "warnings" not in detail.json()["library"]
    assert app.state.knowledge_library_host.embedder.calls == 0


async def test_market_install_task_preserves_post_commit_sync_warning(scenario, monkeypatch):
    """Exercise the fixed local bridge download and Main's market install runner."""
    http, service, host, _built_package, artifact, share, actual = scenario

    async def detail(*_args, **_kwargs):
        return {"ok": True, "share": share}

    monkeypatch.setattr(market, "_bridge_json", detail)
    transport(monkeypatch, actual, lambda _request: httpx.Response(200, content=artifact.read_bytes()))
    failures = _fail_sync_after_installed_manifest(monkeypatch, host.root)
    response = await http.post("/api/knowledge-library/market/install", json={
        "share_id": share["id"], "revision": share["revision"], "file_sha256": share["file_sha256"],
    })
    job = await complete(service, response)
    assert job["state"] == "completed", job
    _assert_active_without_persisted_warning(host.root, job["result"], failures)
    library = await service.get_library(job["result"]["library_id"])
    assert library["documents"] and "warnings" not in library["library"]
    assert service.embedder.calls == []
