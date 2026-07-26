"""Runtime compatibility API for Bilibili auth actions."""

from __future__ import annotations

from typing import Any

from . import runtime_bili_auth, runtime_douyin_auth, runtime_twitch_auth


class RuntimeAuthApiMixin:
    async def reload_credential(self) -> None:
        """Reload cached Bilibili credential from encrypted local storage."""
        await runtime_bili_auth.reload_credential(self)

    async def bili_login(self) -> dict[str, Any]:
        """Create a QR-code login session, or report an existing login."""
        return await runtime_bili_auth.bili_login(self)

    async def bili_login_check(self) -> dict[str, Any]:
        """Poll QR-code login and reload encrypted credentials on success."""
        return await runtime_bili_auth.bili_login_check(self)

    async def bili_login_status(self) -> dict[str, Any]:
        """Return local Bilibili login status without exposing credentials."""
        return await runtime_bili_auth.bili_login_status(self)

    async def bili_logout(self) -> dict[str, Any]:
        """Delete local encrypted Bilibili credentials and clear the cache."""
        return await runtime_bili_auth.bili_logout(self)

    async def reload_douyin_credential(self) -> None:
        """Reload cached Douyin cookie from encrypted local storage."""
        await runtime_douyin_auth.reload_credential(self)

    async def douyin_cookie_import(self, cookie: Any, uid: Any = "", nickname: Any = "") -> dict[str, Any]:
        """Save a manually provided Douyin cookie without exposing its value."""
        return await runtime_douyin_auth.import_cookie(self, cookie, uid=uid, nickname=nickname)

    async def douyin_cookie_status(self) -> dict[str, Any]:
        """Return local Douyin cookie status without exposing credentials."""
        return await runtime_douyin_auth.credential_status(self)

    async def douyin_cookie_validate(self, room_ref: Any = "") -> dict[str, Any]:
        """Manually validate the cached Douyin cookie against a room page."""
        return await runtime_douyin_auth.validate_cookie(self, room_ref=room_ref)

    async def douyin_cookie_delete(self) -> dict[str, Any]:
        """Delete local encrypted Douyin cookie and clear the cache."""
        return await runtime_douyin_auth.delete_cookie(self)

    async def reload_twitch_credential(self) -> None:
        """Reload cached Twitch OAuth tokens from encrypted local storage."""
        await runtime_twitch_auth.reload_credential(self)

    async def twitch_device_authorization_start(self) -> dict[str, Any]:
        """Start Twitch Device Code Flow and return only the public user code."""
        return await runtime_twitch_auth.start_device_authorization(self)

    async def twitch_device_authorization_check(self) -> dict[str, Any]:
        """Perform one scheduled Device Code Flow token check."""
        return await runtime_twitch_auth.check_device_authorization(self)

    async def twitch_device_authorization_cancel(self) -> dict[str, Any]:
        """Cancel the active Twitch Device Code Flow session."""
        return await runtime_twitch_auth.cancel_device_authorization(self)

    async def twitch_login_status(self) -> dict[str, Any]:
        """Return credential presence; expose account metadata only after validation."""
        return await runtime_twitch_auth.credential_status(self)

    async def twitch_credential_validate(self) -> dict[str, Any]:
        """Validate and, when necessary, refresh the cached Twitch token."""
        return await runtime_twitch_auth.validate_credential(self)

    async def twitch_logout(self) -> dict[str, Any]:
        """Delete encrypted Twitch credentials and clear the cache."""
        return await runtime_twitch_auth.logout(self)
