from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from plugin.plugins.neko_live.adapters.twitch_auth_service import (
    TwitchAuthService,
    _request_json,
    _verification_uri,
)
from plugin.plugins.neko_live.core import runtime_twitch_auth


class _Store:
    def __init__(self, credential: dict[str, Any] | None = None, *, save_ok: bool = True) -> None:
        self.credential = credential
        self.save_ok = save_ok
        self.saved: list[dict[str, Any]] = []

    async def load(self) -> dict[str, Any] | None:
        return dict(self.credential) if self.credential else None

    async def save(self, payload: dict[str, Any]) -> bool:
        self.saved.append(dict(payload))
        if self.save_ok:
            self.credential = dict(payload)
        return self.save_ok


class _Http:
    def __init__(self, responses: list[tuple[int, dict[str, Any]]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "data": data or {}})
        return self.responses.pop(0)


class _Logger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(f"INFO {message}")

    def warning(self, message: str) -> None:
        self.lines.append(f"WARNING {message}")

    def error(self, message: str) -> None:
        self.lines.append(f"ERROR {message}")


def test_verification_uri_rejects_malformed_twitch_port() -> None:
    assert _verification_uri("https://www.twitch.tv:not-a-port/activate") == ""


def _service(store: _Store, http: _Http) -> TwitchAuthService:
    async def reload() -> None:
        return None

    return TwitchAuthService(
        credential_provider=store.load,
        credential_saver=store.save,
        credential_reloader=reload,
        request_json=http,
        clock=lambda: 1_700_000_000.0,
    )


@pytest.mark.asyncio
async def test_external_twitch_http_requests_trust_environment_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiohttp

    captured: dict[str, Any] = {}

    class _Response:
        status = 400

        async def __aenter__(self) -> _Response:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def json(self, *, content_type: Any = None) -> dict[str, Any]:
            return {"message": "invalid client id"}

    class _Session:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        def request(self, *_args: Any, **_kwargs: Any) -> _Response:
            return _Response()

    monkeypatch.setattr(aiohttp, "ClientSession", _Session)

    status, _payload = await _request_json(
        "POST",
        "https://id.twitch.tv/oauth2/device",
        data={"client_id": "aaaaaaaa", "scopes": "user:read:chat"},
    )

    assert status == 400
    assert captured["trust_env"] is True


@pytest.mark.asyncio
async def test_device_authorization_logs_only_device_authorization_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    store = _Store()
    logger = _Logger()
    http = _Http(
        [
            (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate?public=true&device-code=ABCD-EFGH",
                    "expires_in": 900,
                    "interval": 5,
                },
            )
        ]
    )

    async def reload() -> None:
        return None

    service = TwitchAuthService(
        logger=logger,
        credential_provider=store.load,
        credential_saver=store.save,
        credential_reloader=reload,
        request_json=http,
        clock=lambda: 1_700_000_000.0,
    )

    result = await service.start_device_authorization("clientid123")

    lines = "\n".join(logger.lines)
    assert result["started"] is True
    assert "stage=request_start" in lines
    assert "trust_env=True proxy_env_present=True" in lines
    assert "stage=response status=200" in lines
    assert "stage=ready user_code_present=True verification_uri_present=True expires_in=900 interval=5" in lines
    assert "secret-device-code" not in lines
    assert "ABCD-EFGH" not in lines
    assert "device-code=ABCD-EFGH" not in lines


@pytest.mark.asyncio
async def test_local_twitch_credential_is_unverified_until_validated() -> None:
    runtime = SimpleNamespace(
        twitch_credential={"access_token": "access", "refresh_token": "refresh", "login": "account_login"},
        twitch_credential_store=SimpleNamespace(has_credential=lambda: True),
    )

    status = await runtime_twitch_auth.credential_status(runtime)

    assert status == {
        "platform": "twitch",
        "logged_in": False,
        "authorization_state": "unverified",
        "login": "",
        "user_id": "",
        "scopes": [],
    }


@pytest.mark.asyncio
async def test_device_authorization_start_serializes_with_cancel() -> None:
    request_started = asyncio.Event()
    release_request = asyncio.Event()

    async def request_json(*_args: Any, **_kwargs: Any) -> tuple[int, dict[str, Any]]:
        request_started.set()
        await release_request.wait()
        return (
            200,
            {
                "device_code": "secret-device-code",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://www.twitch.tv/activate",
                "expires_in": 900,
                "interval": 5,
            },
        )

    store = _Store()

    async def reload() -> None:
        return None

    service = TwitchAuthService(
        credential_provider=store.load,
        credential_saver=store.save,
        credential_reloader=reload,
        request_json=request_json,
        clock=lambda: 1_700_000_000.0,
    )

    start_task = asyncio.create_task(service.start_device_authorization("clientid123"))
    await asyncio.wait_for(request_started.wait(), timeout=1)
    cancel_task = asyncio.create_task(service.cancel_device_authorization("clientid123"))
    await asyncio.sleep(0)

    assert cancel_task.done() is False

    release_request.set()
    started, cancelled = await asyncio.gather(start_task, cancel_task)

    assert started["started"] is True
    assert cancelled["cancelled"] is True
    assert service.device_authorization_status("clientid123") is None


@pytest.mark.asyncio
async def test_device_authorization_retries_one_transient_proxy_connection_failure() -> None:
    store = _Store()
    logger = _Logger()

    class _TransientHttp:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, *_args: Any, **_kwargs: Any) -> tuple[int, dict[str, Any]]:
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("proxy reset")
            return (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 900,
                    "interval": 5,
                },
            )

    http = _TransientHttp()

    async def reload() -> None:
        return None

    service = TwitchAuthService(
        logger=logger,
        credential_provider=store.load,
        credential_saver=store.save,
        credential_reloader=reload,
        request_json=http,
        clock=lambda: 1_700_000_000.0,
    )

    result = await service.start_device_authorization("clientid123")

    lines = "\n".join(logger.lines)
    assert result["started"] is True
    assert http.calls == 2
    assert "stage=request_retry endpoint=device attempt=1 error_type=ConnectionError" in lines
    assert "stage=request_attempt endpoint=device attempt=2" in lines


@pytest.mark.asyncio
async def test_device_authorization_stays_in_memory_and_pending_check_is_public() -> None:
    store = _Store()
    http = _Http(
        [
            (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 900,
                    "interval": 5,
                },
            ),
            (400, {"message": "authorization_pending"}),
        ]
    )
    service = _service(store, http)

    started = await service.start_device_authorization("clientid123")
    pending = await service.check_device_authorization("clientid123")

    assert started == {
        "platform": "twitch",
        "started": True,
        "logged_in": False,
        "pending": True,
        "authorization_state": "pending",
        "user_code": "ABCD-EFGH",
        "verification_uri": "https://www.twitch.tv/activate",
        "expires_in": 900,
        "interval": 5,
    }
    assert "device_code" not in started
    assert pending["pending"] is True
    assert pending["logged_in"] is False
    assert "secret-device-code" not in str(pending)
    assert store.saved == []
    assert http.calls[1]["data"]["scopes"] == "user:read:chat"
    assert http.calls[1]["data"]["device_code"] == "secret-device-code"


@pytest.mark.asyncio
async def test_device_authorization_slow_down_increases_all_following_intervals() -> None:
    store = _Store()
    http = _Http(
        [
            (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 900,
                    "interval": 5,
                },
            ),
            (400, {"message": "slow_down"}),
            (400, {"message": "authorization_pending"}),
        ]
    )
    service = _service(store, http)

    await service.start_device_authorization("clientid123")
    slowed = await service.check_device_authorization("clientid123")
    pending = await service.check_device_authorization("clientid123")

    assert slowed["pending"] is True
    assert slowed["message"] == "slow_down"
    assert slowed["interval"] == 10
    assert pending["message"] == "authorization_pending"
    assert pending["interval"] == 10


@pytest.mark.asyncio
async def test_device_authorization_status_can_resume_and_cancel_public_session() -> None:
    store = _Store()
    http = _Http(
        [
            (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 900,
                    "interval": 5,
                },
            )
        ]
    )
    service = _service(store, http)

    await service.start_device_authorization("clientid123")
    resumed = service.device_authorization_status("clientid123")
    cancelled = await service.cancel_device_authorization("clientid123")

    assert resumed is not None
    assert resumed["authorization_state"] == "pending"
    assert resumed["user_code"] == "ABCD-EFGH"
    assert "device_code" not in resumed
    assert cancelled["cancelled"] is True
    assert cancelled["authorization_state"] == "unauthorized"
    assert service.device_authorization_status("clientid123") is None


@pytest.mark.asyncio
async def test_cancel_waits_for_blocked_save_and_never_reports_false_success() -> None:
    save_started = asyncio.Event()
    release_save = asyncio.Event()

    class _BlockingStore(_Store):
        async def save(self, payload: dict[str, Any]) -> bool:
            self.saved.append(dict(payload))
            save_started.set()
            await release_save.wait()
            self.credential = dict(payload)
            return True

    store = _BlockingStore()
    http = _Http(
        [
            (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 900,
                    "interval": 5,
                },
            ),
            (200, {"access_token": "new-access", "refresh_token": "new-refresh"}),
            (
                200,
                {
                    "client_id": "clientid123",
                    "login": "account_login",
                    "user_id": "42",
                    "scopes": ["user:read:chat"],
                    "expires_in": 14400,
                },
            ),
        ]
    )
    service = _service(store, http)

    await service.start_device_authorization("clientid123")
    check_task = asyncio.create_task(service.check_device_authorization("clientid123"))
    await asyncio.wait_for(save_started.wait(), timeout=1)
    cancel_task = asyncio.create_task(service.cancel_device_authorization("clientid123"))
    await asyncio.sleep(0)

    assert cancel_task.done() is False

    release_save.set()
    checked, cancelled = await asyncio.gather(check_task, cancel_task)

    assert checked["logged_in"] is True
    assert cancelled["cancelled"] is False
    assert cancelled["logged_in"] is True
    assert len(store.saved) == 1


@pytest.mark.asyncio
async def test_device_authorization_denial_ends_session() -> None:
    store = _Store()
    http = _Http(
        [
            (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 900,
                    "interval": 5,
                },
            ),
            (400, {"message": "access_denied"}),
        ]
    )
    service = _service(store, http)

    await service.start_device_authorization("clientid123")
    denied = await service.check_device_authorization("clientid123")

    assert denied["pending"] is False
    assert service.device_authorization_status("clientid123") is None


@pytest.mark.asyncio
async def test_device_authorization_success_validates_then_encrypts_tokens() -> None:
    store = _Store()
    http = _Http(
        [
            (
                200,
                {
                    "device_code": "secret-device-code",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 900,
                    "interval": 5,
                },
            ),
            (200, {"access_token": "new-access", "refresh_token": "new-refresh", "scope": ["user:read:chat"]}),
            (
                200,
                {
                    "client_id": "clientid123",
                    "login": "account_login",
                    "user_id": "42",
                    "scopes": ["user:read:chat"],
                    "expires_in": 14400,
                },
            ),
        ]
    )
    service = _service(store, http)

    await service.start_device_authorization("clientid123")
    result = await service.check_device_authorization("clientid123")

    assert result["logged_in"] is True
    assert result["login"] == "account_login"
    assert result["user_id"] == "42"
    assert result["scopes"] == ["user:read:chat"]
    assert "access_token" not in result
    assert store.saved[0]["access_token"] == "new-access"
    assert store.saved[0]["refresh_token"] == "new-refresh"
    assert store.saved[0]["expires_at"] == "1700014400"


@pytest.mark.asyncio
async def test_invalid_access_token_refreshes_and_replaces_one_time_refresh_token() -> None:
    old = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "client_id": "clientid123",
        "user_id": "42",
        "login": "account_login",
        "display_name": "Account Login",
        "scopes": "user:read:chat",
        "expires_at": "1",
    }
    store = _Store(old)
    http = _Http(
        [
            (401, {"status": 401, "message": "invalid access token"}),
            (200, {"access_token": "fresh-access", "refresh_token": "fresh-refresh", "scope": ["user:read:chat"]}),
            (
                200,
                {
                    "client_id": "clientid123",
                    "login": "account_login",
                    "user_id": "42",
                    "scopes": ["user:read:chat"],
                    "expires_in": 3600,
                },
            ),
        ]
    )
    service = _service(store, http)

    result = await service.check_credential("clientid123")

    assert result["logged_in"] is True
    assert result["refreshed"] is True
    assert store.credential is not None
    assert store.credential["access_token"] == "fresh-access"
    assert store.credential["refresh_token"] == "fresh-refresh"
    assert "old-refresh" not in str(result)


@pytest.mark.asyncio
async def test_failed_refresh_save_keeps_cached_old_credential_and_returns_no_secret() -> None:
    old = {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "client_id": "clientid123",
        "user_id": "42",
        "login": "account_login",
        "display_name": "Account Login",
        "scopes": "user:read:chat",
        "expires_at": "1",
    }
    store = _Store(old, save_ok=False)
    http = _Http(
        [
            (401, {"message": "invalid access token"}),
            (200, {"access_token": "fresh-access", "refresh_token": "fresh-refresh", "scope": ["user:read:chat"]}),
            (
                200,
                {
                    "client_id": "clientid123",
                    "login": "account_login",
                    "user_id": "42",
                    "scopes": ["user:read:chat"],
                    "expires_in": 3600,
                },
            ),
        ]
    )
    service = _service(store, http)

    result = await service.check_credential("clientid123")

    assert result["logged_in"] is False
    assert result["message"] == "twitch credential save failed"
    assert store.credential == old
    assert "fresh-access" not in str(result)
    assert "fresh-refresh" not in str(result)


@pytest.mark.asyncio
async def test_runtime_twitch_store_is_namespaced_encrypted_and_logout_clears_cache(tmp_path: Path) -> None:
    plugin = SimpleNamespace(data_path=lambda: str(tmp_path))
    audit = SimpleNamespace(record=lambda *_args, **_kwargs: None)
    store = runtime_twitch_auth.create_credential_store(plugin, audit)
    runtime = SimpleNamespace(
        twitch_credential_store=store,
        twitch_credential=None,
        audit=audit,
    )
    payload = {
        "access_token": "secret-access",
        "refresh_token": "secret-refresh",
        "client_id": "clientid123",
        "user_id": "42",
        "login": "account_login",
        "display_name": "Account Login",
        "scopes": "user:read:chat",
        "expires_at": "1700014400",
    }

    assert await store.save(payload) is True
    await runtime_twitch_auth.reload_credential(runtime)

    encrypted = (tmp_path / "twitch_credential.enc").read_bytes()
    assert b"secret-access" not in encrypted
    assert b"secret-refresh" not in encrypted
    assert runtime.twitch_credential == payload

    result = await runtime_twitch_auth.logout(runtime)

    assert result["logged_out"] is True
    assert runtime.twitch_credential is None
    assert not (tmp_path / "twitch_credential.enc").exists()
    assert not (tmp_path / "twitch_credential.key").exists()
