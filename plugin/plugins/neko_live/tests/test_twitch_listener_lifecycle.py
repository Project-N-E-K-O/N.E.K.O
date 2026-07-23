from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from twitchio.eventsub import websockets as twitchio_websockets
from twitchio.authentication import Scopes
from twitchio.exceptions import WebsocketConnectionException

from plugin.plugins.neko_live.modules.twitch_live_ingest import TwitchLiveIngestModule, _public_scopes
from plugin.plugins.neko_live.core.runtime_live_controls import _resolve_connection_auth_mode
from plugin.plugins.neko_live.modules.twitch_live_ingest.twitch_client import (
    NekoTwitchClient,
    create_twitch_client,
)


async def _ignore_callback(_payload: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_neko_twitch_client_http_session_trusts_environment() -> None:
    client = create_twitch_client(
        client_id="client123",
        on_message=_ignore_callback,
        on_chat_notification=_ignore_callback,
        on_token_refreshed=_ignore_callback,
    )
    assert isinstance(client, NekoTwitchClient)
    try:
        assert client._neko_http_session.trust_env is True
    finally:
        await client.close(save_tokens=False)


@pytest.mark.asyncio
async def test_neko_twitch_client_closes_injected_http_session() -> None:
    client = create_twitch_client(
        client_id="client123",
        on_message=_ignore_callback,
        on_chat_notification=_ignore_callback,
        on_token_refreshed=_ignore_callback,
    )
    session = client._neko_http_session

    await client.close(save_tokens=False)
    await client.close(save_tokens=False)

    assert session.closed is True


@pytest.mark.asyncio
async def test_eventsub_websocket_connection_failure_uses_environment_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_kwargs: dict[str, Any] = {}

    class _FailingSession:
        async def __aenter__(self) -> "_FailingSession":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def ws_connect(self, *_args: Any, **_kwargs: Any) -> None:
            raise OSError("simulated websocket connection failure")

    class _FakeAiohttp:
        @staticmethod
        def ClientSession(**kwargs: Any) -> _FailingSession:
            session_kwargs.update(kwargs)
            return _FailingSession()

    monkeypatch.setattr(twitchio_websockets, "aiohttp", _FakeAiohttp())
    client = create_twitch_client(
        client_id="client123",
        on_message=_ignore_callback,
        on_chat_notification=_ignore_callback,
        on_token_refreshed=_ignore_callback,
    )
    socket = twitchio_websockets.Websocket(
        client=None,
        token_for="123",
        http=SimpleNamespace(),
    )
    try:
        with pytest.raises(WebsocketConnectionException):
            await socket.connect(fail_once=True)
        assert session_kwargs.get("trust_env") is True
    finally:
        await client.close(save_tokens=False)


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def publish(self, event_type: str, event: Any) -> None:
        self.events.append((event_type, event))


class _Resettable:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset(self) -> None:
        self.reset_count += 1


class _AudienceSession:
    def __init__(self) -> None:
        self.finish_count = 0

    def finish_session(self) -> None:
        self.finish_count += 1


class _Store:
    def __init__(self, save_ok: bool = True) -> None:
        self.save_ok = save_ok
        self.saved: list[dict[str, Any]] = []

    async def save(self, payload: dict[str, Any]) -> bool:
        self.saved.append(dict(payload))
        return self.save_ok


class _AsyncItems:
    def __init__(self, items: list[Any]) -> None:
        self.items = items

    def __aiter__(self):
        async def iterate():
            for item in self.items:
                yield item

        return iterate()


class _Client:
    def __init__(self, **callbacks: Any) -> None:
        self.callbacks = callbacks
        self.ready = asyncio.Event()
        self.closed = asyncio.Event()
        self.start_kwargs: dict[str, Any] = {}
        self.tokens: list[tuple[str, str]] = []
        self.subscriptions: list[tuple[Any, dict[str, Any]]] = []

    async def start(self, token: str, **kwargs: Any) -> None:
        self.start_kwargs = {"token": token, **kwargs}
        self.ready.set()
        await self.closed.wait()

    async def wait_until_ready(self) -> None:
        await self.ready.wait()

    async def add_token(self, access_token: str, refresh_token: str) -> None:
        self.tokens.append((access_token, refresh_token))

    async def fetch_users(self, **_kwargs: Any) -> list[Any]:
        return [SimpleNamespace(id="100", name="target_channel", display_name="Target Channel")]

    def fetch_streams(self, **_kwargs: Any) -> _AsyncItems:
        return _AsyncItems([])

    async def subscribe_websocket(self, payload: Any, **kwargs: Any) -> None:
        self.subscriptions.append((payload, kwargs))

    async def close(self, **_kwargs: Any) -> None:
        self.closed.set()


def _context(module: TwitchLiveIngestModule, store: _Store) -> SimpleNamespace:
    credential = {
        "access_token": "secret-access",
        "refresh_token": "secret-refresh",
        "client_id": "clientid123",
        "user_id": "42",
        "login": "account_login",
        "display_name": "Account Login",
        "scopes": "user:read:chat",
        "expires_at": "1700014400",
    }
    router = SimpleNamespace(
        platform="twitch",
        provider_for=lambda platform: module if platform == "twitch" else None,
        configured_room_ref=lambda: "target_channel",
    )

    async def reload_credential() -> None:
        return None

    safety_updates: list[bool] = []
    context = SimpleNamespace(
        config=SimpleNamespace(
            live_platform="twitch",
            live_room_ref="target_channel",
            live_mode="co_stream",
            live_enabled=True,
        ),
        twitch_credential=credential,
        twitch_credential_store=store,
        reload_twitch_credential=reload_credential,
        live_provider=router,
        event_bus=_Bus(),
        audit=SimpleNamespace(record=lambda *_args, **_kwargs: None),
        safety_guard=SimpleNamespace(set_connected=safety_updates.append),
        safety_updates=safety_updates,
        live_connection_state="connected",
        live_connection_auth_mode="authenticated",
        _accepting_live_events=True,
        _live_session_generation=7,
        _live_listener_started_at=123.0,
        live_room_context={"platform": "twitch", "room_ref": "target_channel", "live_status": "offline"},
        live_audience_session=_AudienceSession(),
        live_events=_Resettable(),
        live_support_events=_Resettable(),
    )
    restore_calls: list[bool] = []

    async def restore_instructions(*, force: bool = False) -> str:
        restore_calls.append(force)
        return "restored"

    context.restore_instructions = restore_instructions
    context.restore_calls = restore_calls
    return context


@pytest.mark.asyncio
async def test_listener_starts_without_twitchio_token_files_and_subscribes_target_chat() -> None:
    clients: list[_Client] = []

    def factory(**callbacks: Any) -> _Client:
        client = _Client(**callbacks)
        clients.append(client)
        return client

    module = TwitchLiveIngestModule(client_factory=factory)
    module.ctx = _context(module, _Store())

    assert await module.start_listening("TARGET_CHANNEL") is True

    client = clients[0]
    assert client.start_kwargs == {
        "token": "secret-access",
        "with_adapter": False,
        "load_tokens": False,
        "save_tokens": False,
    }
    assert client.tokens == [("secret-access", "secret-refresh")]
    subscription, kwargs = client.subscriptions[0]
    assert subscription.condition == {"broadcaster_user_id": "100", "user_id": "42"}
    assert kwargs == {"as_bot": False, "token_for": "42"}
    notification_subscription, notification_kwargs = client.subscriptions[1]
    assert notification_subscription.type == "channel.chat.notification"
    assert notification_subscription.condition == {"broadcaster_user_id": "100", "user_id": "42"}
    assert notification_kwargs == {"as_bot": False, "token_for": "42"}
    assert module.listener_state()["state"] == "connected"
    assert module.listener_state()["room_ref"] == "target_channel"

    await module.stop_listening()

    assert client.closed.is_set()
    assert module.is_listening() is False
    assert module.listener_state()["state"] == "disconnected"
    assert module.listener_state()["last_error"] == ""
    assert module._client_supervisor_task is None


@pytest.mark.asyncio
async def test_listener_consumes_runner_failure_and_clears_connected_state() -> None:
    class _FailingClient(_Client):
        def __init__(self, **callbacks: Any) -> None:
            super().__init__(**callbacks)
            self.fail = asyncio.Event()

        async def start(self, token: str, **kwargs: Any) -> None:
            self.start_kwargs = {"token": token, **kwargs}
            self.ready.set()
            await self.fail.wait()
            raise RuntimeError("socket token=secret-access disconnected")

    clients: list[_FailingClient] = []

    def factory(**callbacks: Any) -> _FailingClient:
        client = _FailingClient(**callbacks)
        clients.append(client)
        return client

    audit_records: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    module = TwitchLiveIngestModule(client_factory=factory)
    ctx = _context(module, _Store())
    ctx.audit = SimpleNamespace(record=lambda *args, **kwargs: audit_records.append((args, kwargs)))
    module.ctx = ctx
    assert await module.start_listening("target_channel") is True
    supervisor = module._client_supervisor_task
    assert supervisor is not None

    clients[0].fail.set()
    await asyncio.wait_for(supervisor, timeout=1)

    assert module.is_listening() is False
    assert module.listener_state()["state"] == "disconnected"
    assert module.listener_state()["last_error"] == "twitch listener failed: RuntimeError"
    assert module._client is None
    assert module._client_task is None
    assert module._client_supervisor_task is None
    assert clients[0].closed.is_set()
    assert ctx._accepting_live_events is False
    assert ctx.live_connection_state == "disconnected"
    assert ctx.live_connection_auth_mode == "unknown"
    assert ctx.config.live_enabled is False
    assert ctx._live_listener_started_at == 0.0
    assert ctx.live_audience_session.finish_count == 1
    assert ctx.live_events.reset_count == 1
    assert ctx.live_support_events.reset_count == 1
    assert ctx.live_room_context == {"live_status": "unknown"}
    assert ctx.restore_calls == [True]
    assert ctx.safety_updates == [False]
    assert audit_records == [
        (
            ("twitch_listener_stopped", "Twitch listener stopped unexpectedly"),
            {"level": "warning", "detail": {"error": "twitch listener failed: RuntimeError"}},
        )
    ]

    assert await module.start_listening("target_channel") is True
    assert len(clients) == 2
    assert module.is_listening() is True
    await module.stop_listening()


@pytest.mark.asyncio
async def test_listener_projects_messages_and_drops_stale_generation_after_stop() -> None:
    clients: list[_Client] = []

    def factory(**callbacks: Any) -> _Client:
        client = _Client(**callbacks)
        clients.append(client)
        return client

    module = TwitchLiveIngestModule(client_factory=factory)
    ctx = _context(module, _Store())
    module.ctx = ctx
    await module.start_listening("target_channel")
    emit = clients[0].callbacks["on_message"]
    message = SimpleNamespace(
        id="message-1",
        text="hello NEKO",
        chatter=SimpleNamespace(id="200", name="viewer_login", display_name="Viewer Name"),
    )

    await emit(message)

    assert len(ctx.event_bus.events) == 1
    event_type, event = ctx.event_bus.events[0]
    assert event_type == "danmaku"
    assert event.session_generation == 7
    assert event.raw is None
    assert module.listener_state()["state"] == "receiving"

    await module.stop_listening()
    await emit(message)

    assert len(ctx.event_bus.events) == 1


@pytest.mark.asyncio
async def test_listener_projects_chat_notifications_to_gift_bus_events() -> None:
    clients: list[_Client] = []

    def factory(**callbacks: Any) -> _Client:
        client = _Client(**callbacks)
        clients.append(client)
        return client

    module = TwitchLiveIngestModule(client_factory=factory)
    ctx = _context(module, _Store())
    module.ctx = ctx
    await module.start_listening("target_channel")
    emit = clients[0].callbacks["on_chat_notification"]
    notice = SimpleNamespace(
        id="notice-sub-1",
        notice_type="sub",
        anonymous=False,
        chatter=SimpleNamespace(id="201", name="subscriber", display_name="Subscriber"),
        text="",
        system_message="Subscriber subscribed at Tier 1!",
        sub=SimpleNamespace(tier="1000", months=1, prime=False),
    )

    await emit(notice)

    assert len(ctx.event_bus.events) == 1
    event_type, event = ctx.event_bus.events[0]
    assert event_type == "gift"
    assert event.payload["provider_event_type"] == "TWITCH_SUB"
    assert event.session_generation == 7
    assert module.listener_state()["state"] == "receiving"

    await module.stop_listening()
    await emit(notice)

    assert len(ctx.event_bus.events) == 1


@pytest.mark.asyncio
async def test_twitchio_refresh_callback_atomically_saves_rotated_tokens() -> None:
    clients: list[_Client] = []

    def factory(**callbacks: Any) -> _Client:
        client = _Client(**callbacks)
        clients.append(client)
        return client

    store = _Store()
    module = TwitchLiveIngestModule(client_factory=factory)
    module.ctx = _context(module, store)
    await module.start_listening("target_channel")
    refresh = clients[0].callbacks["on_token_refreshed"]

    await refresh(
        SimpleNamespace(
            user_id="42",
            token="fresh-access",
            refresh_token="fresh-refresh",
            scopes=SimpleNamespace(to_list=lambda: ["user:read:chat"]),
            expires_in=3600,
        )
    )

    assert store.saved[0]["access_token"] == "fresh-access"
    assert store.saved[0]["refresh_token"] == "fresh-refresh"
    assert store.saved[0]["user_id"] == "42"
    assert module.listener_state()["state"] in {"connected", "receiving"}


@pytest.mark.asyncio
async def test_failed_rotated_token_save_stops_listener() -> None:
    clients: list[_Client] = []

    def factory(**callbacks: Any) -> _Client:
        client = _Client(**callbacks)
        clients.append(client)
        return client

    module = TwitchLiveIngestModule(client_factory=factory)
    ctx = _context(module, _Store(save_ok=False))
    module.ctx = ctx
    await module.start_listening("target_channel")

    await clients[0].callbacks["on_token_refreshed"](
        SimpleNamespace(
            user_id="42",
            token="fresh-access",
            refresh_token="fresh-refresh",
            scopes=SimpleNamespace(to_list=lambda: ["user:read:chat"]),
            expires_in=3600,
        )
    )

    assert module.is_listening() is False
    assert module.listener_state()["state"] == "auth_required"
    assert module.listener_state()["last_error"] == "twitch refreshed credential could not be saved"
    assert ctx.config.live_enabled is False
    assert ctx.live_connection_state == "auth_required"
    assert ctx._live_listener_started_at == 0.0
    assert ctx.live_audience_session.finish_count == 1
    assert ctx.restore_calls == [True]


@pytest.mark.asyncio
async def test_twitch_connection_requires_validated_user_token() -> None:
    async def valid() -> dict[str, Any]:
        return {"logged_in": True, "login": "account_login"}

    runtime = SimpleNamespace(
        twitch_credential_validate=valid,
        audit=SimpleNamespace(record=lambda *_args, **_kwargs: None),
    )

    mode = await _resolve_connection_auth_mode(runtime, platform="twitch", allow_accountless=False)

    assert mode == "authenticated"


@pytest.mark.asyncio
async def test_twitch_connection_rejects_missing_or_expired_authorization() -> None:
    async def invalid() -> dict[str, Any]:
        return {"logged_in": False, "message": "twitch authorization expired"}

    runtime = SimpleNamespace(
        twitch_credential_validate=invalid,
        audit=SimpleNamespace(record=lambda *_args, **_kwargs: None),
        live_provider=SimpleNamespace(is_listening=lambda: False),
        config=SimpleNamespace(live_enabled=True),
        live_connection_state="disconnected",
        live_connection_auth_mode="unknown",
        safety_guard=SimpleNamespace(set_connected=lambda _value: None),
    )

    with pytest.raises(ValueError, match="Twitch authorization is required"):
        await _resolve_connection_auth_mode(runtime, platform="twitch", allow_accountless=False)

    assert runtime.config.live_enabled is False
    assert runtime.live_connection_state == "auth_required"


def test_scope_projection_accepts_real_twitchio_scopes() -> None:
    assert _public_scopes(Scopes(["user:read:chat"])) == ["user:read:chat"]
