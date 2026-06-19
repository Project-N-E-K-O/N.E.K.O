"""CSRF/Origin canary tests for the card-assist LLM endpoints.

The four POST endpoints under ``/api/card-assist`` (clarify / generate / refine /
chat) each invoke the user's configured assist LLM and spend real API / free-tier
quota, so they must sit behind the unified local-mutation guard
(``_validate_local_mutation_request``) like every other side-effectful,
browser-facing endpoint. Otherwise a malicious page can fire an opaque
``no-cors`` POST with a ``text/plain`` body containing valid JSON and burn the
user's quota even though it cannot read the response (Codex review on PR #1419).

We only exercise the negative path (no Origin + no CSRF token -> 403
``csrf_validation_failed``). The happy path would require mocking the LLM backend
and is out of scope; this canary's job is to fail loudly if anyone removes the
guard from one of these endpoints.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.card_assist_router import router as card_assist_router


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """Override the repo-level autouse fixture; router-only tests don't need it."""
    yield


@pytest.fixture
def unauthenticated_client():
    """Client that sends NO Origin and NO X-CSRF-Token -- the guard's null
    inputs. Use this to verify each protected endpoint rejects with 403."""
    app = FastAPI()
    app.include_router(card_assist_router)
    with TestClient(app) as client:
        yield client


# Each body is a valid JSON *object* so it clears the upstream
# ``isinstance(body, dict)`` 400 guard and actually reaches the CSRF/Origin
# check we want to canary.
CARD_ASSIST_ENDPOINTS: list[tuple[str, dict]] = [
    ("/api/card-assist/clarify", {"json": {"description": "a cat girl"}}),
    ("/api/card-assist/generate", {"json": {"description": "a cat girl"}}),
    ("/api/card-assist/refine", {"json": {"field_key": "性别"}}),
    ("/api/card-assist/chat", {"json": {"messages": [{"role": "user", "content": "hi"}]}}),
]


@pytest.mark.unit
@pytest.mark.parametrize("endpoint,kwargs", CARD_ASSIST_ENDPOINTS)
def test_card_assist_endpoint_rejects_request_without_csrf_and_origin(
    unauthenticated_client, endpoint: str, kwargs: dict
):
    """No Origin, no CSRF token -> 403 ``csrf_validation_failed``.

    Canary against future regressions: anyone removing the guard from one of
    these endpoints (or letting an LLM call run before it) will see this test
    fail with a clear endpoint path.
    """
    response = unauthenticated_client.post(endpoint, **kwargs)
    assert response.status_code == 403, (
        f"{endpoint} should reject unauthenticated POST with 403, "
        f"got {response.status_code}: {response.text[:200]}"
    )
    body = response.json()
    assert body.get("error_code") == "csrf_validation_failed", (
        f"{endpoint} should return csrf_validation_failed error code, got: {body}"
    )
