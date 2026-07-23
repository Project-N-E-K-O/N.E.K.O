"""Twitch read-only live ingest provider."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from twitchio import eventsub

from ...core.contracts import LiveRoomStatus, ViewerEvent
from ...core.runtime_live_listener import handle_unexpected_live_listener_stop
from .._base import BaseModule
from .helix import lookup_channel_status
from .projection import project_chat_message, project_chat_notification
from .room_ref import parse_twitch_room_ref
from .twitch_client import create_twitch_client


_TWITCH_SUPPORT_EVIDENCE = "twitch_eventsub_typed_event"
_TWITCH_SUPPORT_EVENT_TYPES = {
    "TWITCH_CHEER",
    "TWITCH_SUB",
    "TWITCH_RESUB",
    "TWITCH_SUB_GIFT",
    "TWITCH_COMMUNITY_SUB_GIFT",
}


class TwitchLiveIngestModule(BaseModule):
    id = "twitch_live_ingest"
    title = "Twitch live ingest"
    domain = "live"

    def __init__(self, *, client_factory: Any = None) -> None:
        super().__init__()
        self._listening = False
        self._room_ref = ""
        self._client: Any = None
        self._client_factory = client_factory or create_twitch_client
        self._client_task: asyncio.Task[Any] | None = None
        self._client_supervisor_task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._generation = 0
        self._stop_requested = True
        self._state = "disconnected"
        self._last_error = ""
        self._last_event_at = 0.0

    async def teardown(self) -> None:
        await self.stop_listening()
        await super().teardown()

    def is_listening(self) -> bool:
        return self._listening is True and self._state in {"connected", "receiving"}

    async def start_listening(self, room_ref: Any) -> bool:
        async with self._lifecycle_lock:
            await self._stop_locked(clear_error=True)
            parsed = parse_twitch_room_ref(room_ref)
            if not parsed.ok:
                self._last_error = parsed.message
                return False
            credential = self._credential()
            client_id = _safe_client_id(credential.get("client_id"))
            access_token = _safe_secret(credential.get("access_token"))
            refresh_token = _safe_secret(credential.get("refresh_token"))
            account_user_id = _safe_numeric_id(credential.get("user_id"))
            if not all((client_id, access_token, refresh_token, account_user_id)):
                self._state = "auth_required"
                self._last_error = "twitch authorization is required"
                return False
            self._generation += 1
            generation = self._generation
            self._stop_requested = False
            self._room_ref = parsed.room_ref
            self._state = "connecting"
            try:
                client = self._client_factory(
                    client_id=client_id,
                    on_message=lambda message: self._on_message(message, generation),
                    on_chat_notification=lambda notification: self._on_chat_notification(notification, generation),
                    on_token_refreshed=lambda payload: self._on_token_refreshed(payload, generation),
                )
                self._client = client
                runner = asyncio.create_task(
                    client.start(
                        token=access_token,
                        with_adapter=False,
                        load_tokens=False,
                        save_tokens=False,
                    )
                )
                self._client_task = runner
                await self._wait_for_ready(client, runner)
                await client.add_token(access_token, refresh_token)
                status = await lookup_channel_status(client, parsed.room_ref, token_for=account_user_id)
                if not status.ok or status.room_id <= 0:
                    raise RuntimeError(status.message or "twitch channel was not found")
                subscription = eventsub.ChatMessageSubscription(
                    broadcaster_user_id=str(status.room_id),
                    user_id=account_user_id,
                )
                await client.subscribe_websocket(
                    subscription,
                    as_bot=False,
                    token_for=account_user_id,
                )
                notification_subscription = eventsub.ChatNotificationSubscription(
                    broadcaster_user_id=str(status.room_id),
                    user_id=account_user_id,
                )
                await client.subscribe_websocket(
                    notification_subscription,
                    as_bot=False,
                    token_for=account_user_id,
                )
            except Exception as exc:
                self._last_error = _safe_error(exc)
                await self._stop_locked(clear_error=False)
                return False
            self._listening = True
            self._state = "connected"
            self._last_error = ""
            self._client_supervisor_task = asyncio.create_task(
                self._supervise_client_task(generation, client, runner)
            )
            return True

    async def stop_listening(self) -> None:
        async with self._lifecycle_lock:
            await self._stop_locked(clear_error=True)

    async def _stop_locked(self, *, clear_error: bool) -> None:
        self._stop_requested = True
        self._generation += 1
        self._listening = False
        client, task = self._client, self._client_task
        supervisor = self._client_supervisor_task
        self._client = None
        self._client_task = None
        self._client_supervisor_task = None
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
        if task is not None and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.CancelledError, TimeoutError, Exception):
                if not task.done():
                    task.cancel()
        if supervisor is not None and supervisor is not asyncio.current_task():
            supervisor.cancel()
            try:
                await supervisor
            except asyncio.CancelledError:
                pass
        self._state = "disconnected"
        if clear_error:
            self._last_error = ""

    async def _supervise_client_task(
        self,
        generation: int,
        client: Any,
        runner: asyncio.Task[Any],
    ) -> None:
        failure = "twitch client stopped unexpectedly"
        try:
            await asyncio.shield(runner)
        except asyncio.CancelledError:
            if generation != self._generation or self._stop_requested:
                return
            failure = "twitch listener failed: CancelledError"
        except Exception as exc:
            failure = _safe_error(exc)
        async with self._lifecycle_lock:
            if generation != self._generation or self._stop_requested or self._client_task is not runner:
                if self._client_task is runner and runner.done():
                    self._client_task = None
                if self._client_supervisor_task is asyncio.current_task():
                    self._client_supervisor_task = None
                return
            self._stop_requested = True
            self._generation += 1
            self._listening = False
            self._client = None
            self._client_task = None
            self._client_supervisor_task = None
            self._state = "disconnected"
            self._last_error = failure
            try:
                await client.close()
            except Exception:
                pass
            await self._sync_unexpected_disconnect(failure)

    async def _sync_unexpected_disconnect(
        self,
        failure: str,
        *,
        connection_state: str = "disconnected",
    ) -> None:
        runtime = self.ctx
        if runtime is None:
            return
        await handle_unexpected_live_listener_stop(runtime, connection_state=connection_state)
        audit = getattr(runtime, "audit", None)
        record = getattr(audit, "record", None)
        if callable(record):
            record(
                "twitch_listener_stopped",
                "Twitch listener stopped unexpectedly",
                level="warning",
                detail={"error": failure},
            )

    @staticmethod
    async def _wait_for_ready(client: Any, runner: asyncio.Task[Any]) -> None:
        ready = asyncio.create_task(client.wait_until_ready())
        done, _pending = await asyncio.wait(
            {ready, runner},
            timeout=15,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if ready in done:
            await ready
            return
        ready.cancel()
        if runner in done:
            await runner
            raise RuntimeError("twitch client stopped before becoming ready")
        raise TimeoutError("twitch client ready timeout")

    async def lookup_room_status(self, room_ref: Any) -> LiveRoomStatus:
        parsed = parse_twitch_room_ref(room_ref)
        if not parsed.ok:
            return LiveRoomStatus(room_id=0, ok=False, message=parsed.message)
        credential = self._credential()
        token_for = credential.get("user_id") if isinstance(credential, dict) else ""
        if self._client is not None:
            return await lookup_channel_status(self._client, parsed.room_ref, token_for=token_for)
        client_id = _safe_client_id(credential.get("client_id"))
        access_token = _safe_secret(credential.get("access_token"))
        refresh_token = _safe_secret(credential.get("refresh_token"))
        if not all((client_id, access_token, refresh_token, _safe_numeric_id(token_for))):
            return LiveRoomStatus(room_id=0, ok=False, message="twitch authorization is required")
        client = self._client_factory(
            client_id=client_id,
            on_message=_ignore_event,
            on_chat_notification=_ignore_event,
            on_token_refreshed=self._on_temporary_token_refreshed,
        )
        try:
            await client.login(token=access_token, load_tokens=False, save_tokens=False)
            await client.add_token(access_token, refresh_token)
            return await lookup_channel_status(client, parsed.room_ref, token_for=token_for)
        except Exception as exc:
            return LiveRoomStatus(
                room_id=0,
                ok=False,
                live_status="unknown",
                message=_safe_error(exc),
            )
        finally:
            try:
                await client.close()
            except Exception:
                pass

    async def _on_message(self, message: Any, generation: int) -> None:
        if not self._owns_target(generation):
            return
        event = project_chat_message(message, room_ref=self._room_ref)
        self._publish_event(event)

    async def _on_chat_notification(self, notification: Any, generation: int) -> None:
        if not self._owns_target(generation):
            return
        event = project_chat_notification(notification, room_ref=self._room_ref)
        self._publish_event(event)

    def _publish_event(self, event: Any) -> None:
        if event is None:
            return
        event.session_generation = _safe_generation(getattr(self.ctx, "_live_session_generation", 0))
        bus = getattr(self.ctx, "event_bus", None)
        if bus is None:
            return
        bus.publish(event.type, event)
        self._last_event_at = event.ts
        self._state = "receiving"

    async def _on_token_refreshed(self, payload: Any, generation: int) -> None:
        if generation != self._generation or self._stop_requested:
            return
        await self._save_refreshed_token(payload, stop_on_failure=True)

    async def _on_temporary_token_refreshed(self, payload: Any) -> None:
        await self._save_refreshed_token(payload, stop_on_failure=False)

    async def _save_refreshed_token(self, payload: Any, *, stop_on_failure: bool) -> None:
        current = self._credential()
        user_id = _safe_numeric_id(getattr(payload, "user_id", None))
        access_token = _safe_secret(getattr(payload, "token", None))
        refresh_token = _safe_secret(getattr(payload, "refresh_token", None))
        if not user_id or user_id != _safe_numeric_id(current.get("user_id")) or not access_token or not refresh_token:
            return
        scopes = _public_scopes(getattr(payload, "scopes", None))
        expires_in = _safe_positive_int(getattr(payload, "expires_in", None), maximum=31_536_000)
        updated = {
            **current,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "scopes": " ".join(scopes),
            "expires_at": str(int(time.time() + expires_in)) if expires_in else "",
        }
        store = getattr(self.ctx, "twitch_credential_store", None)
        saver = getattr(store, "save", None)
        ok = await saver(updated) if callable(saver) else False
        if ok:
            self.ctx.twitch_credential = updated
            reloader = getattr(self.ctx, "reload_twitch_credential", None)
            if callable(reloader):
                await reloader()
            return
        self._last_error = "twitch refreshed credential could not be saved"
        if stop_on_failure:
            self._stop_requested = True
            self._generation += 1
            self._listening = False
            self._state = "auth_required"
            client = self._client
            self._client = None
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
            await self._sync_unexpected_disconnect(
                self._last_error,
                connection_state="auth_required",
            )

    def _credential(self) -> dict[str, Any]:
        candidate = getattr(self.ctx, "twitch_credential", None) if self.ctx is not None else None
        return dict(candidate) if isinstance(candidate, dict) else {}

    def _owns_target(self, generation: int) -> bool:
        if generation != self._generation or self._stop_requested or not self.is_listening() or self.ctx is None:
            return False
        router = getattr(self.ctx, "live_provider", None)
        if getattr(router, "platform", "") != "twitch":
            return False
        provider_for = getattr(router, "provider_for", None)
        if callable(provider_for) and provider_for("twitch") is not self:
            return False
        configured = getattr(router, "configured_room_ref", None)
        return not callable(configured) or configured() == self._room_ref

    def normalize(self, payload: Any) -> ViewerEvent:
        safe = payload if isinstance(payload, dict) else {}
        uid = _safe_text(safe.get("uid"), 48)
        if uid and not uid.startswith("twitch:"):
            uid = f"twitch:{uid}"
        raw = {
            "event_type": _safe_text(safe.get("event_type"), 32),
            "chatter_login": _safe_login(safe.get("chatter_login")),
        }
        parsed_room = parse_twitch_room_ref(safe.get("room_ref"))
        if parsed_room.ok:
            raw["room_ref"] = parsed_room.room_ref
        if raw["event_type"] == "gift":
            gift_name = _safe_text(safe.get("gift_name"), 80)
            gift_count = _safe_positive_int(safe.get("gift_count"), maximum=10_000_000)
            gift_value = _safe_positive_int(safe.get("gift_value"), maximum=10_000_000)
            provider_event_id = _safe_public_token(safe.get("provider_event_id"), 80)
            provider_event_type = _safe_text(safe.get("provider_event_type"), 48)
            verified = (
                safe.get("support_verified") is True
                and safe.get("support_evidence") == _TWITCH_SUPPORT_EVIDENCE
                and provider_event_id
                and provider_event_type in _TWITCH_SUPPORT_EVENT_TYPES
            )
            if gift_name:
                raw["gift_name"] = gift_name
            if gift_count:
                raw["gift_count"] = gift_count
            if gift_value:
                raw["gift_value"] = gift_value
            if verified:
                raw.update(
                    {
                        "coin_type": "gold",
                        "support_verified": True,
                        "support_evidence": _TWITCH_SUPPORT_EVIDENCE,
                        "provider_event_id": provider_event_id,
                        "provider_event_type": provider_event_type,
                    }
                )
        return ViewerEvent(
            uid=uid,
            nickname=_safe_text(safe.get("nickname"), 80),
            danmaku_text=_safe_text(safe.get("danmaku_text") or safe.get("text"), 500),
            source="live_danmaku",
            live_mode=self.ctx.config.live_mode if self.ctx else "co_stream",
            raw=raw,
        )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled is True,
            "platform": "twitch",
            "room_ref": self._room_ref,
            "room_id": 0,
            "listening": self._listening,
            "state": self._state if self._state in {"disconnected", "connecting", "connected", "receiving", "auth_required"} else "disconnected",
            "last_error": _safe_text(self._last_error, 160),
            "last_event_at": self._last_event_at if isinstance(self._last_event_at, float) else 0.0,
        }

    def listener_state(self) -> dict[str, Any]:
        return {
            "state": self.status()["state"],
            "room_ref": self._room_ref,
            "room_id": 0,
            "viewer_count": 0,
            "last_error": _safe_text(self._last_error, 160),
        }


def _safe_text(value: Any, limit: int) -> str:
    return " ".join(value.split()).strip()[:limit] if isinstance(value, str) else ""


def _safe_login(value: Any) -> str:
    text = _safe_text(value, 25).lower()
    return text if text.isascii() and text.replace("_", "").isalnum() else ""


def _safe_public_token(value: Any, limit: int) -> str:
    text = value.strip()[:limit] if isinstance(value, str) else ""
    if not text or not text.isascii():
        return ""
    return text if all(char.isalnum() or char in {"-", "_", ":", "."} for char in text) else ""


def _safe_client_id(value: Any) -> str:
    text = _safe_text(value, 80)
    return text if len(text) >= 8 and text.isascii() and text.isalnum() else ""


def _safe_secret(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_numeric_id(value: Any) -> str:
    text = _safe_text(value, 32)
    return text if text.isascii() and text.isdigit() else ""


def _safe_generation(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _safe_positive_int(value: Any, *, maximum: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= maximum else 0


def _public_scopes(value: Any) -> list[str]:
    to_list = getattr(value, "to_list", None)
    selected = getattr(value, "selected", None)
    candidate = to_list() if callable(to_list) else selected if isinstance(selected, list) else value
    if not isinstance(candidate, (list, tuple, set)):
        return []
    return sorted({item for item in candidate if item == "user:read:chat"})


def _safe_error(exc: Exception) -> str:
    message = _safe_text(str(exc), 160)
    lowered = message.lower()
    if any(marker in lowered for marker in ("token=", "authorization", "bearer ", "access_token", "refresh_token")):
        message = ""
    return message or f"twitch listener failed: {type(exc).__name__}"


async def _ignore_event(_payload: Any) -> None:
    return None
