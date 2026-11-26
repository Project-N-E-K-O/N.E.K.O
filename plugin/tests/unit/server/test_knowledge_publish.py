"""Publish only explicitly confirmed, identity-checked unified artifacts."""

import hashlib
import asyncio
import json
from email import policy
from email.parser import BytesParser
import threading
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
import httpcore
import httpx
import pytest

from plugin.server.routes import knowledge_publish as publish
from tests.unit.test_knowledge_archive_v2 import make_archive


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    """Keep real streaming transports and package checks with synthetic account data."""
    raw = make_archive(tmp_path / "source.zip").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    events = []
    reply = {"status": 201, "content": None}
    original_client = httpx.AsyncClient

    async def market_token():
        events.append("account")
        return {"access_token": "isolated-test-bearer"}

    async def upstream(request):
        if request.url.host == "127.0.0.1":
            assert "authorization" not in request.headers
            assert request.url.params["artifact_id"] == digest
            events.append("artifact")
            return httpx.Response(200, content=raw)
        assert request.url.host == "market.test"
        assert request.method == "POST" and request.url.path == "/api/v1/shares"
        assert request.headers["authorization"] == "Bearer isolated-test-bearer"
        body = await request.aread()
        assert b".neko-knowledge.zip" in body and raw in body
        message = BytesParser(policy=policy.default).parsebytes(
            b'Content-Type: ' + request.headers["content-type"].encode() + b'\r\n\r\n' + body)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                  for part in message.iter_parts()}
        assert fields["client_request_id"].decode() == data["client_request_id"]
        events.append("publication")
        if reply["content"] is not None:
            return httpx.Response(reply["status"], content=reply["content"])
        return httpx.Response(reply["status"], json={"id": 7, "kind": "knowledge", "status": "pending_review",
                                         "file_sha256": digest, "revision": 1})

    app = FastAPI()
    app.include_router(publish.router)
    client = original_client(transport=httpx.ASGITransport(app=app), base_url="http://local.test")
    monkeypatch.setattr(publish.account, "_require_local_bridge_token_access", lambda _request: None)
    monkeypatch.setattr(publish.account, "_verify_token", lambda _token: None)
    monkeypatch.setattr(publish.account, "_authenticated_market_token", market_token)
    monkeypatch.setattr(publish.account, "_main_server_port", lambda: 48911)
    monkeypatch.setattr(publish, "get_market_api_url", lambda: "https://market.test")
    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **kwargs: original_client(transport=httpx.MockTransport(upstream), **kwargs))
    data = {"pack_id": "imp.example", "artifact_id": digest, "publish_confirmed": True,
            "client_request_id": str(uuid4()), "title": "Shared sample", "description": "Synthetic only"}
    try:
        async with client:
            yield client, data, events, reply
    finally:
        owners = tuple(getattr(publish, "_publication_owners", ()))
        for owner in owners:
            owner.cancel()
        await asyncio.gather(*owners, return_exceptions=True)


async def test_publication_streams_the_confirmed_artifact_to_existing_shares(setup):
    client, data, events, _reply = setup
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 200, response.text
    assert response.json()["share"]["id"] == 7
    assert events == ["account", "artifact", "publication"]
    assert "bearer" not in response.text


async def test_publication_signs_only_the_local_artifact_hop(setup, monkeypatch):
    """Proxy deployments authenticate Main without disclosing proof to Market."""
    from utils import deployment, instance_access
    from utils.http import internal_client
    from starlette.requests import Request

    client, data, _events, _reply = setup
    monkeypatch.setattr(deployment, "is_behind_proxy", lambda: True)
    monkeypatch.setattr(instance_access, "instance_key", lambda: "isolated-signing-key")
    monkeypatch.setattr(internal_client, "_trusted_internal_service_ports", lambda: {48911})
    factory = publish.httpx.AsyncClient
    requests = []

    async def observe(request):
        requests.append(request)

    def observed_client(**kwargs):
        hooks = kwargs.setdefault("event_hooks", {})
        hooks["request"] = [*hooks.get("request", []), observe]
        return factory(**kwargs)

    monkeypatch.setattr(publish.httpx, "AsyncClient", observed_client)
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 200, response.text
    local, market = requests
    header = instance_access.INTERNAL_SERVICE_HEADER
    assert local.url.host == "127.0.0.1" and local.headers[header]
    incoming = Request({"type": "http", "method": "GET", "path": local.url.path,
                        "headers": local.headers.raw, "client": ("127.0.0.1", 50123),
                        "server": ("127.0.0.1", 48911), "scheme": "http", "query_string": b""})
    assert instance_access._internal_service_identity(incoming, "isolated-signing-key") == "internal"
    assert market.url.host == "market.test" and header not in market.headers
    assert "authorization" not in local.headers
    assert market.headers["authorization"] == "Bearer isolated-test-bearer"


@pytest.mark.parametrize("market_base,no_proxy,proxied", [
    ("https://market.test", "", True),
    ("https://market.test", "market.test", False),
    ("http://127.0.0.1:8000", "", False),
    ("http://localhost:8000", "", False),
    ("http://[::1]:8000", "", False),
    ("https://localhost:8443", "", False),
    ("https://192.168.1.20", "", False),
    ("https://[fe80::1]:8443", "", False),
])
async def test_publication_uses_real_proxy_routing_only_for_remote_market(
    setup, tmp_path, monkeypatch, market_base, no_proxy, proxied,
):
    client, data, _events, _reply = setup
    raw = (tmp_path / "source.zip").read_bytes()
    connections, requests, tls_names = [], [], []

    class Stream(httpcore.AsyncNetworkStream):
        """Serve HTTP bytes below real HTTPX proxy selection and HTTP CONNECT."""
        def __init__(self, host):
            self.host, self.written = host, bytearray()

        async def write(self, buffer, timeout=None):
            self.written.extend(buffer)

        async def read(self, max_bytes, timeout=None):
            request = bytes(self.written)
            self.written.clear()
            if not request:
                return b""
            line = request.split(b"\r\n", 1)[0].decode()
            requests.append((self.host, line, request))
            if line.startswith("CONNECT "):
                return b"HTTP/1.1 200 Connection established\r\n\r\n"
            if line.startswith("GET "):
                assert self.host == "127.0.0.1" and "authorization:" not in request.decode(errors="replace").lower()
                content = raw
            else:
                assert line == "POST /api/v1/shares HTTP/1.1"
                assert b"Authorization: Bearer isolated-test-bearer" in request and raw in request
                content = json.dumps({"id": 7, "kind": "knowledge", "file_sha256": data["artifact_id"]}).encode()
            return b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(content)).encode() + b"\r\n\r\n" + content

        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            tls_names.append(server_hostname)
            return self

        async def aclose(self):
            pass

        def get_extra_info(self, info):
            return None

    class Network(httpcore.AsyncNetworkBackend):
        async def connect_tcp(self, host, port, **kwargs):
            connections.append((host, port))
            if host == "market.test" and proxied:
                raise httpcore.ConnectError("synthetic market requires its configured proxy")
            return Stream(host)

    # Keep genuine client/env-mount and httpcore protocol behavior; replace
    # only the socket backend so no test network or external server is needed.
    real_client = type(client)
    created = []
    def make_client(**kwargs):
        created.append(kwargs)
        outbound = real_client(**kwargs)
        for transport in (outbound._transport, *outbound._mounts.values()):
            if transport is not None:
                transport._pool._network_backend = Network()
        return outbound

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(key, raising=False)
        monkeypatch.delenv(key.lower(), raising=False)
    monkeypatch.setenv("HTTP_PROXY", "http://env-proxy.test:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.test:3128")
    monkeypatch.setenv("NO_PROXY", no_proxy)
    monkeypatch.setattr(publish.httpx, "AsyncClient", make_client)
    monkeypatch.setattr(publish, "get_market_api_url", lambda: market_base)
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 200, response.text
    assert connections[0] == ("127.0.0.1", 48911)
    assert requests[0][1].startswith("GET /api/public-knowledge/packs/artifacts?")
    if proxied:
        assert connections[1] == ("env-proxy.test", 3128)
        assert requests[1][1] == "CONNECT market.test:443 HTTP/1.1"
        assert b"isolated-test-bearer" not in requests[1][2]
        assert tls_names == ["market.test"]
    else:
        assert all(host != "env-proxy.test" for host, _port in connections)
        if market_base.startswith("https://") and market_base != "https://market.test":
            # A direct (non-public) market client keeps the user's CA configuration.
            assert created[-1]["trust_env"] is False and "verify" in created[-1]
    assert response.json()["share"]["id"] == 7


@pytest.mark.parametrize("url,proxied", [
    ("https://market.example", True),
    ("https://8.8.8.8/api", True),
    ("https://localhost:8443", False),
    ("https://dev.localhost", False),
    ("https://10.0.0.5", False),
    ("https://172.16.4.1", False),
    ("https://[fd00::1]", False),
    ("https://100.100.1.2", False),
    ("https://localhost.:8443", False),
    ("http://127.0.0.1:8000", False),
    ("http://market.example", False),
])
def test_publication_and_download_share_one_market_proxy_rule(url, proxied):
    from plugin.server.routes import knowledge_market, market_bridge

    assert market_bridge.market_uses_environment_proxy(url) is proxied
    # Subscription downloads select their transport with the same function.
    assert knowledge_market.market_uses_environment_proxy is market_bridge.market_uses_environment_proxy


def _self_signed_ca(path):
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic LAN Market CA")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now)
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_direct_market_hops_keep_the_users_certificate_configuration(tmp_path, monkeypatch, variable):
    """Bypassing proxies must not also drop a self-hosted market's configured CA."""
    from plugin.server.routes import market_bridge

    ca = tmp_path / "ca" / "market.pem"
    ca.parent.mkdir()
    _self_signed_ca(ca)
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv(variable, str(ca if variable == "SSL_CERT_FILE" else ca.parent))
    assert market_bridge.market_client_options("https://market.example") == {"trust_env": True}
    # Plain loopback HTTP never loads (or fails on) a CA configuration.
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))
    assert market_bridge.market_client_options("http://127.0.0.1:8000") == {"trust_env": False}
    monkeypatch.setenv(variable, str(ca if variable == "SSL_CERT_FILE" else ca.parent))
    if variable == "SSL_CERT_DIR":
        monkeypatch.delenv("SSL_CERT_FILE")
    options = market_bridge.market_client_options("https://192.168.1.20")
    assert options["trust_env"] is False
    if variable == "SSL_CERT_FILE":
        subjects = [dict(item[0] for item in row["subject"]) for row in options["verify"].get_ca_certs()]
        assert {"commonName": "Synthetic LAN Market CA"} in subjects
    else:
        import httpx

        default = httpx.create_ssl_context(trust_env=False)
        # A capath context starts empty instead of loading the certifi bundle.
        assert len(options["verify"].get_ca_certs()) < len(default.get_ca_certs())


async def test_publication_confirmation_precedes_account_or_artifact_reads(setup):
    client, data, events, _reply = setup
    response = await client.post("/market/knowledge/publish?token=test", json={**data, "publish_confirmed": False})
    assert response.status_code == 400
    assert response.json()["reason"] == "market_publication_not_confirmed"
    assert events == []


async def test_a_different_pack_artifact_is_rejected_before_publication(setup):
    client, data, events, _reply = setup
    response = await client.post("/market/knowledge/publish?token=test", json={**data, "pack_id": "imp.other"})
    assert response.status_code == 409
    assert response.json()["reason"] == "market_identity_mismatch"
    assert "publication" not in events


async def test_publication_refuses_cleartext_remote_market_before_reading_artifact(setup, monkeypatch):
    client, data, events, _reply = setup
    monkeypatch.setattr(publish, "get_market_api_url", lambda: "http://market.test")
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 503 and response.json()["reason"] == "market_not_connected"
    assert "artifact" not in events


async def test_publication_scratch_creation_failure_is_a_structured_error(setup, monkeypatch):
    client, data, _events, _reply = setup
    def full_disk(**_kwargs):
        raise OSError("synthetic full scratch volume")
    monkeypatch.setattr(publish.tempfile, "mkdtemp", full_disk)
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 503 and response.json()["reason"] == "market_publication_failed"


async def test_publication_deadline_covers_artifact_download(setup, monkeypatch):
    client, data, _events, _reply = setup
    async def held_download(*_args):
        await asyncio.sleep(30)
    monkeypatch.setattr(publish, "_download", held_download)
    monkeypatch.setattr(publish, "_PUBLICATION_TIMEOUT_SECONDS", 0.01)
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == 503 and response.json()["reason"] == "market_publication_failed"


async def test_publication_deadline_also_covers_account_refresh(setup, monkeypatch):
    client, data, _events, _reply = setup
    async def held_account():
        await asyncio.sleep(30)
    monkeypatch.setattr(publish.account, "_authenticated_market_token", held_account)
    monkeypatch.setattr(publish, "_PUBLICATION_TIMEOUT_SECONDS", 0.01)
    response = await asyncio.wait_for(client.post("/market/knowledge/publish?token=test", json=data), 0.5)
    assert response.status_code == 503 and response.json()["reason"] == "market_publication_failed"


async def test_expired_publication_returns_before_its_tracked_validator_finishes(setup, monkeypatch, tmp_path):
    client, data, _events, _reply = setup
    started, release = threading.Event(), threading.Event()
    directory = tmp_path / "deadline-scratch"
    directory.mkdir()
    monkeypatch.setattr(publish.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr(publish, "_PUBLICATION_TIMEOUT_SECONDS", 0.2)
    original = publish.validate_package
    def held_validator(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(publish, "validate_package", held_validator)
    task = asyncio.create_task(client.post("/market/knowledge/publish?token=test", json=data))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        response = await asyncio.wait_for(asyncio.shield(task), 1)
        assert response.status_code == 503 and response.json()["reason"] == "market_publication_failed"
        assert directory.is_dir() and any(not owner.done() for owner in publish._publication_owners)
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(*tuple(getattr(publish, "_publication_owners", ())), return_exceptions=True)
    assert not directory.exists()


async def test_early_market_reply_closes_the_upload_generator_before_cleanup(setup, monkeypatch, tmp_path):
    client, data, _events, _reply = setup
    raw = (tmp_path / "source.zip").read_bytes()
    directory = tmp_path / "early-reply-scratch"
    directory.mkdir()
    handles, cleanup_closed = [], []
    original_open, original_rmtree = Path.open, publish.shutil.rmtree
    original_client = type(client)
    def record_open(path, *args, **kwargs):
        handle = original_open(path, *args, **kwargs)
        if path.name == "artifact.zip":
            handles.append(handle)
        return handle
    def record_cleanup(path):
        cleanup_closed.append(bool(handles) and all(handle.closed for handle in handles))
        original_rmtree(path)
    class EarlyReply(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            if request.url.host == "127.0.0.1":
                return httpx.Response(200, content=raw)
            stream = request.stream.__aiter__()
            assert await anext(stream)  # multipart metadata
            assert await anext(stream)  # the first file chunk leaves the file open
            return httpx.Response(401)
    monkeypatch.setattr(publish.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    monkeypatch.setattr(Path, "open", record_open)
    monkeypatch.setattr(publish.shutil, "rmtree", record_cleanup)
    monkeypatch.setattr(publish.httpx, "AsyncClient", lambda **kwargs: original_client(transport=EarlyReply(), **kwargs))
    try:
        response = await client.post("/market/knowledge/publish?token=test", json=data)
        assert response.status_code == 401
        assert cleanup_closed == [True]
    finally:
        for handle in handles:
            handle.close()
        original_rmtree(directory, ignore_errors=True)


@pytest.mark.parametrize("raw", [b"", b"<html>Auth gateway</html>"])
async def test_refresh_success_with_non_json_body_uses_the_existing_invalid_response_error(monkeypatch, raw):
    account = publish.account
    original_client = httpx.AsyncClient
    async def fake_httpx():
        return SimpleNamespace(AsyncClient=lambda **kwargs: original_client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=raw)), **kwargs),
            Timeout=httpx.Timeout, HTTPError=httpx.HTTPError, HTTPStatusError=httpx.HTTPStatusError)
    monkeypatch.setattr(account, "ensure_httpx", fake_httpx)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await account._refresh_oauth_token({"refresh_token": "isolated-refresh-token"})
    assert error.value.status_code == 502 and "响应无效" in str(error.value.detail)


@pytest.mark.parametrize("status,reason", [(401, "market_auth_required"), (502, "market_publication_failed")])
async def test_market_status_is_not_hidden_by_a_non_json_error_body(setup, status, reason):
    client, data, _events, reply = setup
    reply.update(status=status, content=b"<html>upstream gateway</html>")
    response = await client.post("/market/knowledge/publish?token=test", json=data)
    assert response.status_code == status and response.json()["reason"] == reason


async def test_cancelled_publication_drains_the_validator_before_scratch_cleanup(setup, monkeypatch, tmp_path):
    client, data, _events, _reply = setup
    started, release = threading.Event(), threading.Event()
    directory = tmp_path / "publication-scratch"
    directory.mkdir()
    monkeypatch.setattr(publish.tempfile, "mkdtemp", lambda **_kwargs: str(directory))
    original = publish.validate_package
    def held_validator(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(publish, "validate_package", held_validator)
    task = asyncio.create_task(client.post("/market/knowledge/publish?token=test", json=data))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert directory.exists() and not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not directory.exists()
