"""Verify binary downloads and private-name transport through both thin proxies."""

import httpx
import pytest
from fastapi import FastAPI

from main_routers import public_knowledge_router as main
from main_routers.system_router import _shared
from plugin.server.routes import knowledge_bridge as bridge

pytestmark = pytest.mark.unit_fast


@pytest.mark.parametrize("hop", ["main", "plugin"])
@pytest.mark.parametrize("path,field", [("entries", "query"), ("entry", "title")])
async def test_private_management_read_headers_never_become_proxy_url_fields(monkeypatch, hop, path, field):
    """Forward only each read endpoint's explicit private field header."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        if hop == "main":
            monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: upstream)
            module, prefix = main, "/api/public-knowledge"
        else:
            monkeypatch.setattr(bridge, "_client", lambda: upstream)
            monkeypatch.setattr(bridge, "_verify_token", lambda _token: None)
            monkeypatch.setattr(bridge, "_require_local_bridge_token_access", lambda _request: None)
            module, prefix = bridge, "/market/knowledge"
        app = FastAPI()
        app.include_router(module.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(prefix + "/" + path,
                params={"pack_id": "imp.demo", field: "must-not-propagate", "token": "local"},
                headers={f"X-Knowledge-{field}": "%E7%A7%81%E5%AF%86", "X-Untrusted-Header": "must-not-pass"})
    assert response.status_code == 200
    assert requests[0].headers[f"x-knowledge-{field}"] == "%E7%A7%81%E5%AF%86"
    assert "x-untrusted-header" not in requests[0].headers
    assert field not in requests[0].url.params
    assert "must-not-propagate" not in str(requests[0].url)


@pytest.mark.parametrize("hop", ["main", "plugin"])
async def test_artifact_bytes_and_filename_survive_the_proxy_without_json_parsing(monkeypatch, hop):
    """Only explicit artifact reads stream binary and its safe disposition."""
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=b"PK\x03\x04test-artifact", headers={
            "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="sample.neko-knowledge.zip"',
            "Set-Cookie": "do-not-relay=1",
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        if hop == "main":
            monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: upstream)
            module, prefix = main, "/api/public-knowledge"
        else:
            monkeypatch.setattr(bridge, "_client", lambda: upstream)
            monkeypatch.setattr(bridge, "_verify_token", lambda _token: None)
            monkeypatch.setattr(bridge, "_require_local_bridge_token_access", lambda _request: None)
            module, prefix = bridge, "/market/knowledge"
        app = FastAPI()
        app.include_router(module.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(prefix + "/packs/artifacts", params={"artifact_id": "a" * 64, "token": "local"})
        assert response.content == b"PK\x03\x04test-artifact"
        assert response.headers["content-type"] == "application/zip"
        assert "sample.neko-knowledge.zip" in response.headers["content-disposition"]
        assert "set-cookie" not in response.headers
        assert requests[0].url.params["artifact_id"] == "a" * 64
        assert "token" not in requests[0].url.params


@pytest.mark.parametrize("hop", ["main", "plugin"])
async def test_preview_name_headers_are_allowlisted_and_stay_out_of_urls(monkeypatch, hop):
    """Opaque pack IDs may be query parameters; source display names may not."""
    requests = []

    async def handler(request):
        await request.aread()
        requests.append(request)
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        if hop == "main":
            monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: upstream)
            monkeypatch.setattr(_shared, "AUTOSTART_CSRF_TOKEN", "csrf-test")
            module, prefix = main, "/api/public-knowledge"
        else:
            monkeypatch.setattr(bridge, "_client", lambda: upstream)
            monkeypatch.setattr(bridge, "_verify_token", lambda _token: None)
            monkeypatch.setattr(bridge, "_require_local_bridge_token_access", lambda _request: None)
            module, prefix = bridge, "/market/knowledge"
        app = FastAPI()
        app.include_router(module.router)
        headers = {"Origin": "http://testserver", "X-CSRF-Token": "csrf-test",
                   "X-Knowledge-Filename": "private.txt", "X-Knowledge-Name": "private-group",
                   "X-Untrusted-Header": "must-not-pass", "Content-Type": "application/octet-stream"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.post(prefix + "/sources/preview", params={"pack_id": "imp.demo", "token": "local", "filename": "legacy-name.txt", "name": "legacy-private-group"},
                                         content=b"synthetic text", headers=headers)
        assert response.status_code == 200
        forwarded = requests[0]
        assert forwarded.headers["x-knowledge-filename"] == "private.txt"
        assert forwarded.headers["x-knowledge-name"] == "private-group"
        assert "x-untrusted-header" not in forwarded.headers
        assert "private" not in str(forwarded.url)
        assert "filename" not in forwarded.url.params and "name" not in forwarded.url.params
