from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.pet_care_gateway as gateway

ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
CLIENT_PROOF = "proof-secret-with-at-least-32-characters"


@pytest.fixture(autouse=True)
def _clear_local_capabilities(monkeypatch):
    monkeypatch.delenv(gateway._DEV_UPSTREAM_ENV, raising=False)
    with gateway._local_capabilities_lock:
        gateway._local_capabilities.clear()
    yield
    with gateway._local_capabilities_lock:
        gateway._local_capabilities.clear()


class _ResponseStream:
    def __init__(self, response: httpx.Response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *_args):
        await self.response.aclose()


def _client(monkeypatch, *, logged_in: bool = True):
    app = FastAPI()
    app.include_router(gateway.router)
    monkeypatch.setattr(gateway.C, "_local_request_source_allowed", lambda _request: True)
    monkeypatch.setattr(gateway.C, "_local_ui_request_source_allowed", lambda _request: True)

    snapshot = {
        "access_token": "oauth-secret",
        "auth_source": "oauth",
        "base_url": "https://community.example",
        "client_id": "neko-desktop",
        "local_user_id": ACCOUNT_ID,
        "session_id": "session-a",
    }

    @contextmanager
    def session_locks(_paths):
        yield

    async def account_identity(_request):
        return "local"

    async def unexpected_remote_status_lookup():
        pytest.fail("the PetCare gateway must not call /api/users/me")

    monkeypatch.setattr(gateway.O, "account_request_identity", account_identity)
    monkeypatch.setattr(
        gateway.O,
        "resolve_saved_oauth_status",
        unexpected_remote_status_lookup,
    )
    monkeypatch.setattr(gateway.C, "_social_session_paths", lambda: [])
    monkeypatch.setattr(gateway.C, "_social_session_locks", session_locks)
    monkeypatch.setattr(
        gateway.C,
        "_desktop_session_snapshot",
        lambda: dict(snapshot) if logged_in else None,
    )
    monkeypatch.setattr(
        gateway.C,
        "_get_client_credentials",
        lambda: ("client-1", CLIENT_PROOF),
    )
    monkeypatch.setattr(gateway.C, "_social_base_url", lambda: "https://community.example")
    return TestClient(app)


def _json_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _local_capability(client: TestClient, path: str, body: bytes) -> str:
    response = client.get(
        "/api/pet-care/local-capability",
        params={
            "method": "POST",
            "path": path,
            "body_sha256": hashlib.sha256(body).hexdigest(),
        },
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200, response.text
    return response.json()["capability"]


def _post_json(
    client: TestClient,
    path: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
):
    body = _json_body(payload)
    capability = _local_capability(client, path, body)
    request_headers = {
        "Content-Type": "application/json",
        gateway._LOCAL_CAPABILITY_HEADER: capability,
        **(headers or {}),
    }
    return client.post(path, content=body, headers=request_headers)


class _JsonUpstream:
    def __init__(
        self,
        *,
        status_code: int = 200,
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.status_code = status_code
        self.payload = payload or {"ok": True}
        self.headers = headers or {}
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, method, url, *, content, headers):
        self.calls.append(
            {"method": method, "url": url, "content": content, "headers": headers}
        )
        return _ResponseStream(
            httpx.Response(
                self.status_code,
                json=self.payload,
                headers=self.headers,
                request=httpx.Request(method, url),
            )
        )


def test_gateway_forwards_only_server_credentials_and_fixed_query(monkeypatch):
    captured = {}

    class Upstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, *, content, headers):
            captured.update(method=method, url=url, content=content, headers=headers)
            return _ResponseStream(
                httpx.Response(
                    200,
                    json={"binding_status": "none"},
                    headers={
                        "X-Correlation-Id": "22222222-2222-4222-8222-222222222222"
                    },
                    request=httpx.Request(method, url),
                )
            )

    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: Upstream())
    client = _client(monkeypatch)
    uid = "a" * 32
    response = client.get(
        f"/api/pet-care/context?character_uid={uid}",
        headers={"Authorization": "Bearer browser-token"},
    )

    assert response.status_code == 200
    assert captured["url"] == (
        f"https://community.example/api/pet-care/context?character_uid={uid}"
    )
    assert captured["headers"]["Authorization"] == "Bearer oauth-secret"
    assert captured["headers"]["X-Client-Id"] == "client-1"
    assert captured["headers"]["X-Client-Proof"] == CLIENT_PROOF
    assert "browser-token" not in repr(captured["headers"])
    assert response.headers["cache-control"] == "no-store"
    assert (
        response.headers["x-correlation-id"]
        == "22222222-2222-4222-8222-222222222222"
    )


def test_gateway_drops_a_noncanonical_upstream_correlation_id(monkeypatch):
    upstream = _JsonUpstream(
        payload={"binding_status": "none"},
        headers={"X-Correlation-Id": "not-a-canonical-uuid"},
    )
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: upstream)
    client = _client(monkeypatch)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 200
    assert "x-correlation-id" not in response.headers


def test_get_reads_saved_transport_context_inside_one_session_fence(monkeypatch):
    upstream = _JsonUpstream(payload={"binding_status": "none"})
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: upstream)
    client = _client(monkeypatch)
    depth = 0

    @contextmanager
    def session_locks(_paths):
        nonlocal depth
        depth += 1
        try:
            yield
        finally:
            depth -= 1

    snapshot = {
        "access_token": "oauth-secret",
        "auth_source": "oauth",
        "base_url": "https://community.example",
        "client_id": "neko-desktop",
        "local_user_id": ACCOUNT_ID,
        "session_id": "session-a",
    }

    def read_snapshot():
        assert depth == 1
        return dict(snapshot)

    def read_credentials():
        assert depth == 1
        return "client-1", CLIENT_PROOF

    monkeypatch.setattr(gateway.C, "_social_session_locks", session_locks)
    monkeypatch.setattr(gateway.C, "_desktop_session_snapshot", read_snapshot)
    monkeypatch.setattr(gateway.C, "_get_client_credentials", read_credentials)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 200
    assert depth == 0
    assert len(upstream.calls) == 1


@pytest.mark.parametrize(
    "saved_token",
    [None, "", "oauth token", "oauth\nsecret", "x" * (16 * 1024 + 1)],
)
def test_missing_or_obviously_invalid_saved_token_fails_before_transport(
    monkeypatch, saved_token
):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            pytest.fail("an invalid saved bearer must fail before upstream I/O")

    client = _client(monkeypatch)
    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedClient)
    monkeypatch.setattr(
        gateway.C,
        "_desktop_session_snapshot",
        lambda: {
            "access_token": saved_token,
            "auth_source": "oauth",
            "base_url": "https://community.example",
            "client_id": "neko-desktop",
            "local_user_id": ACCOUNT_ID,
            "session_id": "session-a",
        },
    )
    monkeypatch.setattr(
        gateway.C,
        "_get_client_credentials",
        lambda: pytest.fail("invalid sessions must not load installation proof"),
    )
    body = _json_body({"idempotency_key": "resume-1"})

    get_response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )
    capability_response = client.get(
        "/api/pet-care/local-capability",
        params={
            "method": "POST",
            "path": "/api/pet-care/session/resume",
            "body_sha256": hashlib.sha256(body).hexdigest(),
        },
    )
    post_response = client.post(
        "/api/pet-care/role-aliases/sync",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Neko-Expected-Account": ACCOUNT_ID,
        },
    )

    for response in (get_response, capability_response, post_response):
        assert response.status_code == 401
        assert response.json()["detail"] == "community_login_required"


@pytest.mark.parametrize(
    "credentials",
    [None, ("short", CLIENT_PROOF), ("client-1", "short-proof")],
)
def test_missing_or_invalid_installation_credentials_fail_locally(
    monkeypatch, credentials
):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            pytest.fail("invalid installation credentials must fail before upstream I/O")

    client = _client(monkeypatch)
    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedClient)
    monkeypatch.setattr(gateway.C, "_get_client_credentials", lambda: credentials)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "client_binding_required"


def test_gateway_passes_through_edge_401_without_local_revalidation(monkeypatch):
    upstream = _JsonUpstream(
        status_code=401,
        payload={"detail": "invalid_token", "code": "invalid_token"},
    )
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: upstream)
    client = _client(monkeypatch)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid_token", "code": "invalid_token"}
    assert response.headers["cache-control"] == "no-store"
    assert len(upstream.calls) == 1


@pytest.mark.parametrize(
    ("override", "expected_origin"),
    [
        ("http://localhost:18080", "http://localhost:18080"),
        ("http://127.0.0.2:18080/", "http://127.0.0.2:18080"),
        ("http://[::1]:18080", "http://[::1]:18080"),
    ],
)
def test_gateway_loopback_override_is_scoped_to_pet_care(
    monkeypatch, override, expected_origin
):
    upstream = _JsonUpstream(payload={"binding_status": "none"})
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: upstream)
    client = _client(monkeypatch)
    monkeypatch.setenv(gateway._DEV_UPSTREAM_ENV, override)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 200
    assert upstream.calls[0]["url"].startswith(
        f"{expected_origin}/api/pet-care/context?"
    )
    assert gateway.C._social_base_url() == "https://community.example"


def test_packaged_gateway_rejects_loopback_development_override(monkeypatch):
    class UnexpectedUpstream:
        def __init__(self, **_kwargs):
            pytest.fail("packaged PetCare must reject a local authority before I/O")

    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedUpstream)
    monkeypatch.setattr(gateway.sys, "frozen", True, raising=False)
    monkeypatch.setenv(gateway._DEV_UPSTREAM_ENV, "http://127.0.0.1:18080")
    client = _client(monkeypatch)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "pet_care_dev_upstream_invalid"


def test_packaged_gateway_without_override_uses_community_authority(monkeypatch):
    upstream = _JsonUpstream(payload={"binding_status": "none"})
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: upstream)
    monkeypatch.setattr(gateway.sys, "frozen", True, raising=False)
    client = _client(monkeypatch)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 200
    assert upstream.calls[0]["url"].startswith(
        "https://community.example/api/pet-care/context?"
    )
    assert upstream.calls[0]["headers"]["Authorization"] == "Bearer oauth-secret"
    assert upstream.calls[0]["headers"]["X-Client-Id"] == "client-1"


def test_gateway_rejects_a_saved_session_from_another_community(monkeypatch):
    class UnexpectedUpstream:
        def __init__(self, **_kwargs):
            pytest.fail("cross-environment credentials must fail before upstream I/O")

    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedUpstream)
    client = _client(monkeypatch)
    monkeypatch.setattr(
        gateway.C,
        "_desktop_session_snapshot",
        lambda: {
            "access_token": "oauth-secret",
            "auth_source": "oauth",
            "base_url": "https://other-community.example",
            "client_id": "neko-desktop",
            "local_user_id": ACCOUNT_ID,
            "session_id": "session-a",
        },
    )

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "pet_care_session_environment_mismatch",
        "code": "pet_care_session_environment_mismatch",
    }


def test_loopback_development_override_accepts_a_remote_saved_session(monkeypatch):
    upstream = _JsonUpstream(payload={"binding_status": "none"})
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: upstream)
    client = _client(monkeypatch)
    monkeypatch.setattr(
        gateway.C,
        "_desktop_session_snapshot",
        lambda: {
            "access_token": "oauth-secret",
            "auth_source": "oauth",
            "base_url": "https://community.example",
            "client_id": "neko-desktop",
            "local_user_id": ACCOUNT_ID,
            "session_id": "session-a",
        },
    )
    monkeypatch.setenv(gateway._DEV_UPSTREAM_ENV, "http://127.0.0.1:18080")

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 200
    assert upstream.calls[0]["url"].startswith(
        "http://127.0.0.1:18080/api/pet-care/context?"
    )


@pytest.mark.parametrize(
    "override",
    [
        "https://community.example",
        "http://127.0.0.1:18080/api",
        "http://user@127.0.0.1:18080",
        "http://127.0.0.1:18080?target=other",
        "http://127.0.0.1:18080#fragment",
        "http://localhost.evil.example:18080",
    ],
)
def test_gateway_rejects_invalid_explicit_dev_upstream(monkeypatch, override):
    class UnexpectedUpstream:
        def __init__(self, **_kwargs):
            pytest.fail("invalid PetCare development upstream must fail before I/O")

    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedUpstream)
    client = _client(monkeypatch)
    monkeypatch.setenv(gateway._DEV_UPSTREAM_ENV, override)

    response = client.get(
        f"/api/pet-care/context?character_uid={'a' * 32}",
    )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "pet_care_dev_upstream_invalid",
        "code": "pet_care_dev_upstream_invalid",
    }


def test_gateway_preserves_write_identity_but_not_arbitrary_headers(monkeypatch):
    captured = {}

    class Upstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, *, content, headers):
            captured.update(method=method, url=url, content=content, headers=headers)
            return _ResponseStream(
                httpx.Response(
                    200,
                    json={"ok": True},
                    request=httpx.Request(method, url),
                )
            )

    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: Upstream())
    client = _client(monkeypatch)
    response = _post_json(
        client,
        "/api/pet-care/session/resume",
        {"idempotency_key": "resume-1"},
        headers={
            "Idempotency-Key": "resume-1",
            "X-Internal-Assertion": "forged",
            "Cookie": "forged=1",
        },
    )

    assert response.status_code == 200
    assert captured["headers"]["Idempotency-Key"] == "resume-1"
    assert "X-Internal-Assertion" not in captured["headers"]
    assert "Cookie" not in captured["headers"]
    assert gateway._LOCAL_CAPABILITY_HEADER not in captured["headers"]


@pytest.mark.parametrize(
    ("path", "extra_headers"),
    [
        ("/api/pet-care/session/resume", {}),
        (
            "/api/pet-care/role-aliases/sync",
            {"X-Neko-Expected-Account": ACCOUNT_ID},
        ),
        ("/api/pet-care/bind", {}),
        (f"/api/pet-care/pets/{'a' * 32}/care-actions", {}),
        (f"/api/pet-care/pets/{'a' * 32}/explorations", {}),
        (f"/api/pet-care/pets/{'a' * 32}/status-recovery", {}),
        (f"/api/pet-care/pets/{'a' * 32}/play-leases", {}),
        (f"/api/pet-care/pets/{'a' * 32}/explorations/trip-1/complete", {}),
        (f"/api/pet-care/pets/{'a' * 32}/explorations/trip-1/recall", {}),
        (f"/api/pet-care/pets/{'a' * 32}/tasks/task-1/claim", {}),
        (f"/api/pet-care/pets/{'a' * 32}/tasks/task-1/complete", {}),
        (
            f"/api/pet-care/pets/{'a' * 32}/play-leases/lease.v1~opaque/complete",
            {},
        ),
    ],
)
def test_every_allowed_write_route_requires_the_same_local_capability(
    monkeypatch, path, extra_headers
):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            raise AssertionError("a missing capability must fail before upstream I/O")

    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedClient)
    client = _client(monkeypatch)
    response = client.post(
        path,
        content=_json_body({"idempotency_key": "op-1"}),
        headers={"Content-Type": "application/json", **extra_headers},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "pet_care_local_capability_invalid"


def test_local_capability_requires_the_local_ui_source_contract(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        gateway.C, "_local_ui_request_source_allowed", lambda _request: False
    )
    body = _json_body({"idempotency_key": "resume-1"})

    response = client.get(
        "/api/pet-care/local-capability",
        params={
            "method": "POST",
            "path": "/api/pet-care/session/resume",
            "body_sha256": hashlib.sha256(body).hexdigest(),
        },
        headers={"Sec-Fetch-Site": "same-origin"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "instance_authorization_required"


def test_local_capability_rejects_noncanonical_or_unapproved_paths(monkeypatch):
    client = _client(monkeypatch)
    body_sha256 = hashlib.sha256(b"{}").hexdigest()

    for path in (
        "/api/pet-care//session/resume",
        "/api/pet-care/session/resume?extra=1",
        "/api/pet-care/pets/not-a-character/care-actions",
        f"/api/pet-care/pets/{'a' * 32}/care-actions/",
    ):
        response = client.get(
            "/api/pet-care/local-capability",
            params={"method": "POST", "path": path, "body_sha256": body_sha256},
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        assert response.status_code == 422
        assert (
            response.json()["detail"]
            == "pet_care_local_capability_request_invalid"
        )


def test_local_capability_is_exact_body_and_path_bound_and_spent_on_mismatch(
    monkeypatch,
):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            raise AssertionError("a mismatched capability must fail before upstream I/O")

    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedClient)
    client = _client(monkeypatch)
    original = _json_body({"idempotency_key": "resume-1", "value": 1})
    altered = _json_body({"idempotency_key": "resume-1", "value": 2})
    capability = _local_capability(
        client, "/api/pet-care/session/resume", original
    )

    mismatch = client.post(
        "/api/pet-care/session/resume",
        content=altered,
        headers={
            "Content-Type": "application/json",
            gateway._LOCAL_CAPABILITY_HEADER: capability,
        },
    )
    replay_after_mismatch = client.post(
        "/api/pet-care/session/resume",
        content=original,
        headers={
            "Content-Type": "application/json",
            gateway._LOCAL_CAPABILITY_HEADER: capability,
        },
    )

    assert mismatch.status_code == 403
    assert replay_after_mismatch.status_code == 403

    path_capability = _local_capability(client, "/api/pet-care/bind", original)
    wrong_path = client.post(
        "/api/pet-care/session/resume",
        content=original,
        headers={
            "Content-Type": "application/json",
            gateway._LOCAL_CAPABILITY_HEADER: path_capability,
        },
    )
    replay_after_wrong_path = client.post(
        "/api/pet-care/bind",
        content=original,
        headers={
            "Content-Type": "application/json",
            gateway._LOCAL_CAPABILITY_HEADER: path_capability,
        },
    )

    assert wrong_path.status_code == 403
    assert replay_after_wrong_path.status_code == 403


def test_local_capability_is_single_use_even_after_success(monkeypatch):
    upstream = _JsonUpstream()
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: upstream)
    client = _client(monkeypatch)
    path = "/api/pet-care/session/resume"
    body = _json_body({"idempotency_key": "resume-1"})
    capability = _local_capability(client, path, body)
    headers = {
        "Content-Type": "application/json",
        gateway._LOCAL_CAPABILITY_HEADER: capability,
        "Idempotency-Key": "resume-1",
    }

    first = client.post(path, content=body, headers=headers)
    replay = client.post(path, content=body, headers=headers)

    assert first.status_code == 200
    assert replay.status_code == 403
    assert len(upstream.calls) == 1


def test_login_session_change_invalidates_an_issued_capability(monkeypatch):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            raise AssertionError("an old-session capability must not reach upstream")

    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedClient)
    client = _client(monkeypatch)
    path = "/api/pet-care/session/resume"
    body = _json_body({"idempotency_key": "resume-1"})
    capability = _local_capability(client, path, body)
    monkeypatch.setattr(
        gateway.C,
        "_desktop_session_snapshot",
        lambda: {
            "access_token": "oauth-secret-2",
            "auth_source": "oauth",
            "base_url": "https://community.example",
            "client_id": "neko-desktop",
            "local_user_id": ACCOUNT_ID,
            "session_id": "session-b",
        },
    )

    response = client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            gateway._LOCAL_CAPABILITY_HEADER: capability,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "pet_care_local_capability_invalid"
    with gateway._local_capabilities_lock:
        assert not gateway._local_capabilities


def test_busy_session_fence_spends_the_presented_capability(monkeypatch):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            raise AssertionError("a busy session fence must not reach upstream")

    client = _client(monkeypatch)
    path = "/api/pet-care/session/resume"
    body = _json_body({"idempotency_key": "resume-1"})
    capability = _local_capability(client, path, body)

    @contextmanager
    def busy_session_locks(_paths):
        raise TimeoutError("busy")
        yield

    monkeypatch.setattr(gateway.C, "_social_session_locks", busy_session_locks)
    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedClient)
    response = client.post(
        path,
        content=body,
        headers={
            "Content-Type": "application/json",
            gateway._LOCAL_CAPABILITY_HEADER: capability,
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "pet_care_local_session_busy"
    with gateway._local_capabilities_lock:
        assert not gateway._local_capabilities


def test_local_capability_expiry_and_pool_are_bounded_and_thread_safe(monkeypatch):
    fingerprint = "f" * 64
    path = "/api/pet-care/session/resume"
    body_sha256 = hashlib.sha256(b"{}").hexdigest()
    wrong_method = gateway._issue_local_capability(
        session_fingerprint=fingerprint,
        method="POST",
        gateway_path=path,
        body_sha256=body_sha256,
        now=1.0,
    )
    assert not gateway._consume_local_capability(
        wrong_method,
        session_fingerprint=fingerprint,
        method="GET",
        gateway_path=path,
        body_sha256=body_sha256,
        now=1.0,
    )
    assert not gateway._consume_local_capability(
        wrong_method,
        session_fingerprint=fingerprint,
        method="POST",
        gateway_path=path,
        body_sha256=body_sha256,
        now=1.0,
    )
    expired = gateway._issue_local_capability(
        session_fingerprint=fingerprint,
        method="POST",
        gateway_path=path,
        body_sha256=body_sha256,
        now=10.0,
    )
    assert not gateway._consume_local_capability(
        expired,
        session_fingerprint=fingerprint,
        method="POST",
        gateway_path=path,
        body_sha256=body_sha256,
        now=10.0 + gateway._LOCAL_CAPABILITY_TTL_SEC,
    )

    monkeypatch.setattr(gateway, "_LOCAL_CAPABILITY_MAX_ACTIVE", 8)
    with ThreadPoolExecutor(max_workers=16) as pool:
        issued = list(
            pool.map(
                lambda _index: gateway._issue_local_capability(
                    session_fingerprint=fingerprint,
                    method="POST",
                    gateway_path=path,
                    body_sha256=body_sha256,
                ),
                range(64),
            )
        )

    assert len(set(issued)) == 64
    with gateway._local_capabilities_lock:
        assert len(gateway._local_capabilities) == 8


def test_gateway_forwards_only_exact_account_fenced_alias_retirement(monkeypatch):
    captured = {}

    class Upstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, *, content, headers):
            captured.update(method=method, url=url, content=content, headers=headers)
            return _ResponseStream(
                httpx.Response(
                    200,
                    json={
                        "character_uid": "a" * 32,
                        "source_revision": f"delete:{'a' * 32}",
                        "status": "retired",
                    },
                    request=httpx.Request(method, url),
                )
            )

    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: Upstream())
    client = _client(monkeypatch)
    payload = {
        "character_uid": "a" * 32,
        "source_revision": f"delete:{'a' * 32}",
        "status": "retired",
    }
    response = _post_json(
        client,
        "/api/pet-care/role-aliases/sync",
        payload,
        headers={"X-Neko-Expected-Account": ACCOUNT_ID},
    )

    assert response.status_code == 200
    assert captured["url"] == "https://community.example/api/pet-care/role-aliases/sync"
    assert captured["content"] == response.request.content
    assert captured["headers"]["X-Neko-Expected-Account"] == ACCOUNT_ID
    assert not gateway._route_allowed("POST", "/role-aliases/sync/extra")


def test_gateway_alias_retirement_fails_closed_when_account_changes(monkeypatch):
    client = _client(monkeypatch)
    payload = {
        "character_uid": "a" * 32,
        "source_revision": f"delete:{'a' * 32}",
        "status": "retired",
    }

    missing = client.post("/api/pet-care/role-aliases/sync", json=payload)
    changed = client.post(
        "/api/pet-care/role-aliases/sync",
        json=payload,
        headers={
            "X-Neko-Expected-Account": "22222222-2222-4222-8222-222222222222"
        },
    )

    assert missing.status_code == 422
    assert missing.json()["detail"] == "pet_care_expected_account_required"
    assert changed.status_code == 409
    assert changed.json()["detail"] == "pet_care_account_changed"


def test_gateway_rejects_routes_queries_and_non_json_before_forwarding(monkeypatch):
    client = _client(monkeypatch)
    uid = "b" * 32

    assert client.post(f"/api/pet-care/pets/{uid}/debug/fill-status", json={}).status_code == 404
    assert client.post(f"/api/pet-care/pets/{uid}/daily-check-in", json={}).status_code == 404
    assert client.get(f"/api/pet-care/pets/{uid}/explorations").status_code == 404
    assert client.get(f"/api/pet-care/pets/{uid}?extra=1").status_code == 422
    assert client.get("/api/pet-care/context?character_uid=bad").status_code == 422
    assert client.post(
        "/api/pet-care/session/resume",
        content="x",
        headers={"Content-Type": "text/plain"},
    ).status_code == 415


def test_gateway_treats_play_lease_id_as_an_opaque_url_safe_segment():
    uid = "d" * 32
    assert gateway._route_allowed(
        "POST", f"/pets/{uid}/play-leases/eyJ.v1~opaque_token/complete"
    )
    for invalid_path in (
        f"/pets/{uid}/play-leases/not/one/segment/complete",
        f"/pets/{uid}/play-leases/./complete",
        f"/pets/{uid}/play-leases/../complete",
        f"/pets//{uid}/play-leases/lease-1/complete",
        f"/pets/{uid}/play-leases/lease-1/complete/",
    ):
        assert not gateway._route_allowed("POST", invalid_path)
        assert gateway._canonical_post_gateway_path(
            f"/api/pet-care{invalid_path}"
        ) == ""


def test_gateway_allows_only_task_complete_and_claim_mutations():
    uid = "e" * 32

    assert gateway._route_allowed(
        "POST", f"/pets/{uid}/tasks/daily-work/complete"
    )
    assert gateway._route_allowed(
        "POST", f"/pets/{uid}/tasks/daily-work/claim"
    )
    assert not gateway._route_allowed(
        "POST", f"/pets/{uid}/tasks/daily-work/reward"
    )


def test_gateway_requires_verified_local_request_and_login(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(gateway.C, "_local_request_source_allowed", lambda _request: False)
    denied = client.get(f"/api/pet-care/context?character_uid={'c' * 32}")
    assert denied.status_code == 403

    client = _client(monkeypatch, logged_in=False)
    logged_out = client.post(
        "/api/pet-care/session/resume", json={"idempotency_key": "resume-1"}
    )
    assert logged_out.status_code == 401


def test_gateway_never_sends_account_or_installation_credentials_over_remote_http(
    monkeypatch,
):
    class UnexpectedClient:
        def __init__(self, **_kwargs):
            raise AssertionError("insecure PetCare transport must fail before HTTP setup")

    client = _client(monkeypatch)
    monkeypatch.setattr(
        gateway.C,
        "_social_base_url",
        lambda: "http://community.example",
    )
    monkeypatch.setattr(gateway.httpx, "AsyncClient", UnexpectedClient)

    response = _post_json(
        client,
        "/api/pet-care/session/resume",
        {"idempotency_key": "resume-insecure"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "pet_care_transport_not_secure"


def test_gateway_rejects_non_json_and_oversized_upstream(monkeypatch):
    class Upstream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, *, content, headers):
            return _ResponseStream(
                httpx.Response(
                    200,
                    content=b"x" * (gateway._MAX_RESPONSE_BYTES + 1),
                    headers={"Content-Type": "application/json"},
                    request=httpx.Request(method, url),
                )
            )

    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **_kwargs: Upstream())
    client = _client(monkeypatch)
    response = _post_json(
        client,
        "/api/pet-care/session/resume",
        {"idempotency_key": "resume-1"},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "pet_care_upstream_response_too_large"


def test_gateway_rejects_oversized_request_before_forwarding(monkeypatch):
    client = _client(monkeypatch)
    response = client.post(
        "/api/pet-care/session/resume",
        content=b"x" * (gateway._MAX_REQUEST_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "pet_care_request_too_large"
