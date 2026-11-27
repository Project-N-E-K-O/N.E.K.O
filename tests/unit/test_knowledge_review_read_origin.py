"""Exercise private knowledge reads through Main's production browser guards."""

import httpx
import pytest

from main_logic import public_knowledge
from utils import instance_access

pytestmark = pytest.mark.unit

PRIVATE_READS = ("sources", "sources/versions", "entries", "entry", "packs/artifacts")


@pytest.fixture
async def main_reads(monkeypatch, tmp_path):
    from app import main_server

    calls = []

    def memory(request):
        calls.append(request)
        if request.url.path.endswith("/packs/artifacts"):
            return httpx.Response(200, content=b"PK-private-package", headers={"Content-Type": "application/zip"})
        return httpx.Response(200, json={"ok": True, "content": "private-document-canary"})

    monkeypatch.setattr(main_server, "_runtime_startup_init_completed", True)
    monkeypatch.setattr(main_server, "_main_runtime_limited_mode_enabled", False)
    monkeypatch.setattr(instance_access, "is_behind_proxy", lambda: False)
    monkeypatch.setattr(instance_access, "is_remote_backend_deployment", lambda: False)
    monkeypatch.setattr("utils.host_origin_guard.saved_trusted_hosts", lambda: "")
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path))
    monkeypatch.setenv("NEKO_INSTANCE_ACCESS_KEY", "read-review-" + "x" * 40)
    public_knowledge.reset_for_tests()
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(memory)) as upstream:
            monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: upstream)
            yield main_server.app, calls
    finally:
        public_knowledge.reset_for_tests()


@pytest.mark.parametrize("path", PRIVATE_READS)
async def test_local_reads_do_not_grant_cross_origin_browser_access(main_reads, path):
    """Local native reads stay compatible; no CORS permission exposes the body."""
    app, calls = main_reads
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48911") as client:
        for headers in ({}, {"Origin": "http://127.0.0.1:48911"}, {"Origin": "https://evil.example"}):
            response = await client.get("/api/public-knowledge/" + path, headers=headers)
            assert response.status_code == 200
            # An HTTP client can see this response; an external web page cannot
            # read it because Main does not grant CORS to that browser origin.
            assert "access-control-allow-origin" not in response.headers
            assert "access-control-allow-credentials" not in response.headers
    assert len(calls) == 3


@pytest.mark.parametrize("path", PRIVATE_READS)
async def test_dns_rebinding_host_is_rejected_before_private_reads(main_reads, path):
    app, calls = main_reads
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://untrusted-review.example") as client:
        response = await client.get("/api/public-knowledge/" + path)
    assert response.status_code == 400
    assert not calls


@pytest.mark.parametrize("path", PRIVATE_READS)
async def test_authenticated_remote_cross_origin_reads_are_rejected(main_reads, path):
    app, calls = main_reads
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://192.0.2.10") as client:
        headers = {"Authorization": "Bearer " + "read-review-" + "x" * 40, "Origin": "https://evil.example"}
        response = await client.get("/api/public-knowledge/" + path, headers=headers)
        assert response.status_code == 403
        assert not calls
        headers["Origin"] = "https://192.0.2.10"
        allowed = await client.get("/api/public-knowledge/" + path, headers=headers)
        assert allowed.status_code == 200
    assert len(calls) == 1
