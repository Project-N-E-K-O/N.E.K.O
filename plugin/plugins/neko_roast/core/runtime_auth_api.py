"""Runtime compatibility API for Bilibili auth actions."""

from __future__ import annotations

from typing import Any

from . import runtime_bili_auth, runtime_douyin_auth


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
