from __future__ import annotations

import pytest

from main_logic import client_registration as R

pytestmark = pytest.mark.unit

CLIENT_ID = "4fc4d20c9f5f456287bc6c40dce2039b"
CLIENT_PROOF = "p" * 43
CLOUD = "https://community.example"


@pytest.fixture(autouse=True)
def _clear_cache():
    R.reset_registration_cache()
    yield
    R.reset_registration_cache()


class _Response:
    def __init__(self, status_code: int = 200, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _fake_client(posts: list, response: _Response | Exception = None):
    outcome = response or _Response()

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            posts.append((url, kwargs["json"]))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    return FakeAsyncClient


def test_social_base_url_falls_back_to_production(monkeypatch):
    monkeypatch.delenv("NEKO_SOCIAL_BASE_URL", raising=False)
    assert R.social_base_url() == R.DEFAULT_SOCIAL_BASE_URL
    # The opt-in callers must still be able to tell "unset" from "defaulted",
    # otherwise facts sync and drop hints would start sending cloud traffic
    # nobody configured.
    assert R.configured_social_base_url() is None


def test_social_base_url_prefers_configured_value(monkeypatch):
    monkeypatch.setenv("NEKO_SOCIAL_BASE_URL", "https://staging.example/")
    assert R.social_base_url() == "https://staging.example"
    assert R.configured_social_base_url() == "https://staging.example"


@pytest.mark.parametrize(
    # Not named `base_url`: pytest-base-url ships a session-scoped fixture of
    # that name and would shadow the parametrization.
    "cloud_url,allowed",
    [
        ("https://community.example", True),
        ("http://127.0.0.1:8080", True),
        ("http://localhost:8080", True),
        ("http://community.example", False),
        ("ftp://community.example", False),
        ("not-a-url", False),
    ],
)
def test_proof_transport_allowed_blocks_plaintext_off_loopback(cloud_url, allowed):
    assert R.proof_transport_allowed(cloud_url) is allowed


@pytest.mark.parametrize(
    "status_code,detail,expected",
    [
        (403, "invalid_client_proof", True),
        (403, "client_not_registered", True),
        (404, "client_not_found", True),
        (401, "client_not_registered", True),
        (403, "client_already_bound_to_other_user", False),
        (409, "client_not_registered", False),
        (200, "", False),
    ],
)
def test_looks_unregistered(status_code, detail, expected):
    assert R.looks_unregistered(status_code, detail) is expected


@pytest.mark.asyncio
async def test_register_posts_supplied_credentials(monkeypatch):
    posts: list = []
    monkeypatch.setattr(R.httpx, "AsyncClient", _fake_client(posts))

    assert await R.ensure_client_registered(CLOUD, CLIENT_ID, CLIENT_PROOF)
    assert posts == [
        (
            f"{CLOUD}/api/clients/register",
            {"client_id": CLIENT_ID, "client_proof": CLIENT_PROOF},
        )
    ]


@pytest.mark.asyncio
async def test_register_loads_persisted_credentials_when_omitted(monkeypatch):
    posts: list = []
    monkeypatch.setattr(R.httpx, "AsyncClient", _fake_client(posts))
    monkeypatch.setattr(R, "_load_credentials", lambda: (CLIENT_ID, CLIENT_PROOF))

    assert await R.ensure_client_registered(CLOUD)
    assert posts[0][1] == {"client_id": CLIENT_ID, "client_proof": CLIENT_PROOF}


@pytest.mark.asyncio
async def test_register_is_cached_per_base_url_and_client(monkeypatch):
    posts: list = []
    monkeypatch.setattr(R.httpx, "AsyncClient", _fake_client(posts))

    assert await R.ensure_client_registered(CLOUD, CLIENT_ID, CLIENT_PROOF)
    assert await R.ensure_client_registered(CLOUD, CLIENT_ID, CLIENT_PROOF)
    assert len(posts) == 1

    # A different cloud is a different registration.
    assert await R.ensure_client_registered(
        "https://other.example", CLIENT_ID, CLIENT_PROOF
    )
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_force_reregisters_after_cloud_forgets_the_row(monkeypatch):
    posts: list = []
    monkeypatch.setattr(R.httpx, "AsyncClient", _fake_client(posts))

    assert await R.ensure_client_registered(CLOUD, CLIENT_ID, CLIENT_PROOF)
    # The cached "registered" flag can outlive the cloud row it describes, so a
    # 403 on a proof-bearing call must be able to punch through the cache.
    assert await R.ensure_client_registered(
        CLOUD, CLIENT_ID, CLIENT_PROOF, force=True
    )
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_register_refuses_to_leak_proof_over_plaintext_http(monkeypatch):
    posts: list = []
    monkeypatch.setattr(R.httpx, "AsyncClient", _fake_client(posts))

    assert not await R.ensure_client_registered(
        "http://community.example", CLIENT_ID, CLIENT_PROOF
    )
    assert posts == []


@pytest.mark.asyncio
async def test_failed_registration_is_not_cached(monkeypatch):
    posts: list = []
    monkeypatch.setattr(
        R.httpx, "AsyncClient", _fake_client(posts, _Response(500, "boom"))
    )

    assert not await R.ensure_client_registered(CLOUD, CLIENT_ID, CLIENT_PROOF)
    assert not await R.ensure_client_registered(CLOUD, CLIENT_ID, CLIENT_PROOF)
    assert len(posts) == 2


@pytest.mark.asyncio
async def test_register_survives_transport_failure(monkeypatch):
    posts: list = []
    monkeypatch.setattr(
        R.httpx,
        "AsyncClient",
        _fake_client(posts, R.httpx.ConnectError("no route")),
    )

    assert not await R.ensure_client_registered(CLOUD, CLIENT_ID, CLIENT_PROOF)


@pytest.mark.asyncio
async def test_missing_credentials_skip_the_call(monkeypatch):
    posts: list = []
    monkeypatch.setattr(R.httpx, "AsyncClient", _fake_client(posts))
    monkeypatch.setattr(R, "_load_credentials", lambda: None)

    assert not await R.ensure_client_registered(CLOUD)
    assert posts == []
