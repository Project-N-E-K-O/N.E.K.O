"""CSRF/Origin canary tests for previously-unguarded local mutation endpoints.

These endpoints used to be `@router.post` with no `_validate_local_mutation_request`
gate. The security follow-up (issue #1479) brought them under the unified guard.
This file guarantees the guard stays wired up: each endpoint must reject a
request with no Origin and no CSRF token with 403 + ``csrf_validation_failed``.

We intentionally do NOT exercise the happy path here — that would require
mocking Steamworks / session_manager / LLM / translation backends, which is
out of scope. The negative path alone is enough to detect anyone accidentally
removing the guard.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers import system_router as system_router_module


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override repo-level autouse fixture; router-only tests don't need it."""
    yield


@pytest.fixture
def unauthenticated_client():
    """Client that sends NO Origin and NO X-CSRF-Token — the guard's null
    inputs. Use this to verify each protected endpoint rejects with 403.
    """
    app = FastAPI()
    app.include_router(system_router_module.router)
    with TestClient(app) as client:
        yield client


# Each entry: (endpoint path, method-specific kwargs).
# Use POSTs only — these are mutation endpoints.
UNCOVERED_ENDPOINTS: list[tuple[str, dict]] = [
    ("/api/pending-notices/ack", {"json": {"cursor": 0}}),
    ("/api/emotion/analysis", {"json": {"text": "hello"}}),
    ("/api/steam/set-achievement-status/PLAY_GAME", {}),
    ("/api/steam/update-playtime", {"json": {"seconds": 10}}),
    ("/api/proactive_chat", {"json": {"lanlan_name": "Yui"}}),
    ("/api/proactive/music_played_through", {"json": {"lanlan_name": "Yui"}}),
    ("/api/translate", {"json": {"text": "hello", "target_lang": "zh"}}),
]


@pytest.mark.unit
@pytest.mark.parametrize("endpoint,kwargs", UNCOVERED_ENDPOINTS)
def test_endpoint_rejects_request_without_csrf_and_origin(
    unauthenticated_client, endpoint: str, kwargs: dict
):
    """No Origin, no CSRF token → 403 ``csrf_validation_failed``.

    Canary against future regressions: anyone removing the guard from one of
    these endpoints will see this test fail with a clear endpoint path.
    """
    response = unauthenticated_client.post(endpoint, **kwargs)
    assert response.status_code == 403, (
        f"{endpoint} should reject unauthenticated POST with 403, "
        f"got {response.status_code}: {response.text[:200]}"
    )
    body = response.json()
    assert body.get("error_code") == "csrf_validation_failed", (
        f"{endpoint} should return csrf_validation_failed error code, "
        f"got: {body}"
    )


@pytest.mark.unit
@pytest.mark.parametrize("endpoint,kwargs", UNCOVERED_ENDPOINTS)
def test_endpoint_rejects_request_with_wrong_csrf_token(
    unauthenticated_client, endpoint: str, kwargs: dict
):
    """Same-origin browser request but wrong CSRF token → 403.

    Covers the realistic attacker scenario: a malicious page running in the
    same origin context (e.g., a compromised localhost dev server on the same
    port range) that can read Origin but not the per-instance CSRF token.
    """
    response = unauthenticated_client.post(
        endpoint,
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": "wrong-token-not-the-real-one",
        },
        **kwargs,
    )
    assert response.status_code == 403, (
        f"{endpoint} should reject wrong-CSRF POST with 403, "
        f"got {response.status_code}: {response.text[:200]}"
    )
    assert response.json().get("error_code") == "csrf_validation_failed"
