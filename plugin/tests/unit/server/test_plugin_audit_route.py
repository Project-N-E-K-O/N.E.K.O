from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from plugin import settings
from plugin.server.application.plugins import capability_gate
from plugin.server.application.plugins.call_audit import CallAudit
from plugin.server.routes.plugin_audit import router

pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def audit(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "observe")
    audit = CallAudit(path_resolver=lambda: tmp_path / "x.jsonl")
    monkeypatch.setattr(capability_gate, "audit", audit)
    audit.record({"kind": "forward", "target": "a", "caller": "b", "trace_id": "t1"})
    audit.record({"kind": "decision", "plugin": "a", "verdict": "would_deny", "trace_id": "t1"})
    audit.record({"kind": "forward", "target": "c", "caller": None, "trace_id": "t2"})
    yield audit
    audit.close(1.0)


def _client(client_host="127.0.0.1", base="http://127.0.0.1:48916"):
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))
    return httpx.AsyncClient(transport=transport, base_url=base)


async def test_local_native_client_reads_the_ring(audit):
    async with _client() as client:
        response = await client.get("/plugin-audit/call-chain")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "observe"
    assert [e["trace_id"] for e in body["entries"]] == ["t2", "t1", "t1"]
    assert body["dropped"] == 0


async def test_filters(audit):
    async with _client() as client:
        by_trace = (await client.get("/plugin-audit/call-chain", params={"trace_id": "t1"})).json()
        by_plugin = (await client.get("/plugin-audit/call-chain", params={"plugin_id": "b"})).json()
        by_verdict = (await client.get("/plugin-audit/call-chain", params={"verdict": "would_deny"})).json()
    assert len(by_trace["entries"]) == 2
    assert [e["target"] for e in by_plugin["entries"]] == ["a"]
    assert [e["kind"] for e in by_verdict["entries"]] == ["decision"]


@pytest.mark.parametrize(
    ("client_host", "base", "headers"),
    [
        ("192.168.1.5", "http://127.0.0.1:48916", {}),
        ("127.0.0.1", "http://nas.local:48916", {}),
        ("127.0.0.1", "http://127.0.0.1:48916", {"X-Forwarded-For": "10.0.0.2"}),
        ("127.0.0.1", "http://127.0.0.1:48916", {"Origin": "http://127.0.0.1:48911"}),
    ],
)
async def test_everything_but_a_local_native_client_is_refused(audit, client_host, base, headers):
    async with _client(client_host, base) as client:
        response = await client.get("/plugin-audit/call-chain", headers=headers)
    assert response.status_code == 403


async def test_limit_is_bounded(audit):
    async with _client() as client:
        assert (await client.get("/plugin-audit/call-chain", params={"limit": 5000})).status_code == 422
        assert len((await client.get("/plugin-audit/call-chain", params={"limit": 1})).json()["entries"]) == 1


async def test_off_mode_reports_off(audit, monkeypatch):
    monkeypatch.setattr(settings, "PLUGIN_CAPABILITY_POLICY", "off")
    async with _client() as client:
        assert (await client.get("/plugin-audit/call-chain")).json()["mode"] == "off"


async def test_route_is_served_by_the_plugin_server_app(audit):
    from plugin.server.http_app import build_plugin_server_app

    transport = httpx.ASGITransport(app=build_plugin_server_app(), client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:48916") as client:
        response = await client.get("/plugin-audit/call-chain")
    assert response.status_code == 200
    assert response.json()["mode"] == "observe"
