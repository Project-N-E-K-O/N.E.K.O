# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""Twitch Device Code authentication backed by the local encrypted store."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from utils.cookies_login import load_cookies_from_file, save_cookies_to_file
from utils.external_http_client import get_external_http_client


_DEVICE_URL = "https://id.twitch.tv/oauth2/device"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
_SCOPES = ("user:read:follows",)
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9]{8,80}$")


@dataclass(slots=True)
class _DeviceSession:
    client_id: str
    device_code: str
    expires_at: float
    interval: int


class TwitchAuthService:
    """Own the short-lived device code and persist only validated credentials."""

    def __init__(self) -> None:
        self._device_session: _DeviceSession | None = None

    async def start(self, client_id: Any) -> dict[str, Any]:
        client_id = _client_id(client_id)
        if not client_id:
            self._device_session = None
            return _error("invalid_client_id")
        status, data = await _request("POST", _DEVICE_URL, data={
            "client_id": client_id,
            "scopes": " ".join(_SCOPES),
        })
        device_code = _text(data.get("device_code"), 512)
        user_code = _text(data.get("user_code"), 64)
        verification_uri = _verification_uri(data.get("verification_uri"))
        expires_in = _positive_int(data.get("expires_in"), 0, 3600)
        interval = _positive_int(data.get("interval"), 5, 60)
        if status != 200 or not all((device_code, user_code, verification_uri, expires_in)):
            self._device_session = None
            return _error("device_authorization_failed")
        self._device_session = _DeviceSession(
            client_id=client_id,
            device_code=device_code,
            expires_at=time.time() + expires_in,
            interval=interval,
        )
        return {
            "success": True,
            "platform": "twitch",
            "pending": True,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "expires_in": expires_in,
            "interval": interval,
        }

    async def check_device_code(self, client_id: Any) -> dict[str, Any]:
        session = self._device_session
        if session is None or _client_id(client_id) != session.client_id:
            return _error("device_authorization_not_active")
        if time.time() >= session.expires_at:
            self._device_session = None
            return _error("device_authorization_expired")
        status, data = await _request("POST", _TOKEN_URL, data={
            "client_id": session.client_id,
            "scopes": " ".join(_SCOPES),
            "device_code": session.device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if status != 200:
            message = _oauth_error(data)
            if message in {"authorization_pending", "slow_down"}:
                return {"success": True, "platform": "twitch", "pending": True, "interval": session.interval}
            if message == "expired_token":
                self._device_session = None
            return _error("device_authorization_failed")
        credential = await _validated_credential(session.client_id, data)
        if credential is None or not await _save(credential):
            self._device_session = None
            return _error("credential_save_failed")
        self._device_session = None
        return _public_status(credential, refreshed=False)

    async def status(self) -> dict[str, Any]:
        credential = await _load()
        if not _credential_present(credential) or not set(_SCOPES).issubset(_scopes(credential.get("scopes") if credential else "")):
            return {
                "success": True, "platform": "twitch", "logged_in": False, "has_cookies": False,
                "requires_reauthorization": bool(credential),
            }
        return _public_status(credential, refreshed=False)

    async def access_token(self, *, force_refresh: bool = False) -> tuple[str, str]:
        """Return a valid ``(client_id, access_token)`` pair, refreshing when needed."""
        credential = await _load()
        client_id = _client_id(credential.get("client_id") if credential else "")
        access_token = _secret(credential, "access_token")
        if not client_id or not access_token:
            return "", ""
        expires_at = _positive_int(credential.get("expires_at"), 0, 2_147_483_647)
        if not force_refresh and expires_at > int(time.time()) + 90:
            return client_id, access_token
        refresh_token = _secret(credential, "refresh_token")
        if not refresh_token:
            return "", ""
        status, data = await _request("POST", _TOKEN_URL, data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
        if status != 200:
            return "", ""
        refreshed = await _validated_credential(client_id, data)
        if refreshed is None or not await _save(refreshed):
            return "", ""
        return client_id, _secret(refreshed, "access_token")

    async def followed_stream_access(self, *, force_refresh: bool = False) -> tuple[str, str, str]:
        """Return the credential context required by ``/streams/followed``."""
        credential = await _load()
        if not set(_SCOPES).issubset(_scopes(credential.get("scopes") if credential else "")):
            return "", "", ""
        user_id = _text(credential.get("user_id") if credential else "", 64)
        client_id, access_token = await self.access_token(force_refresh=force_refresh)
        return (client_id, access_token, user_id) if client_id and access_token and user_id else ("", "", "")


async def _request(method: str, url: str, *, headers: dict[str, str] | None = None, data: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    try:
        response = await get_external_http_client().request(method, url, headers=headers, data=data, timeout=15.0)
        try:
            payload = response.json()
        except Exception:
            payload = {}
        return response.status_code, payload if isinstance(payload, dict) else {}
    except Exception:
        return 0, {}


async def _validated_credential(client_id: str, token_data: dict[str, Any]) -> dict[str, str] | None:
    access_token = _secret(token_data, "access_token")
    refresh_token = _secret(token_data, "refresh_token")
    if not access_token or not refresh_token:
        return None
    status, data = await _request("GET", _VALIDATE_URL, headers={"Authorization": f"OAuth {access_token}"})
    login = _login(data.get("login"))
    user_id = _text(data.get("user_id"), 64)
    scopes = _scopes(data.get("scopes"))
    if status != 200 or _client_id(data.get("client_id")) != client_id or not login or not user_id or not set(_SCOPES).issubset(scopes):
        return None
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "user_id": user_id,
        "login": login,
        "scopes": " ".join(sorted(scopes)),
        "expires_at": str(int(time.time()) + _positive_int(data.get("expires_in"), 0, 31_536_000)),
    }


async def _load() -> dict[str, str]:
    return await asyncio.to_thread(load_cookies_from_file, "twitch")


async def _save(credential: dict[str, str]) -> bool:
    return await asyncio.to_thread(save_cookies_to_file, "twitch", credential, True)


def _public_status(credential: dict[str, str], *, refreshed: bool) -> dict[str, Any]:
    return {
        "success": True, "platform": "twitch", "logged_in": True, "has_cookies": True,
        "login": _login(credential.get("login")), "user_id": _text(credential.get("user_id"), 64),
        "expires_at": _text(credential.get("expires_at"), 24), "refreshed": refreshed,
    }


def _credential_present(data: Any) -> bool:
    """Return whether the encrypted store contains a complete OAuth credential."""
    return (
        isinstance(data, dict)
        and bool(_client_id(data.get("client_id")))
        and bool(_secret(data, "access_token"))
        and bool(_secret(data, "refresh_token"))
        and bool(_text(data.get("user_id"), 64))
    )


def _error(code: str) -> dict[str, Any]:
    return {"success": False, "platform": "twitch", "pending": False, "code": code}


def _client_id(value: Any) -> str:
    value = value.strip() if isinstance(value, str) else ""
    return value if _CLIENT_ID_RE.fullmatch(value) else ""


def _secret(data: Any, key: str) -> str:
    value = data.get(key) if isinstance(data, dict) else ""
    return value.strip()[:4096] if isinstance(value, str) else ""


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split()).strip()[:limit] if isinstance(value, str) else ""


def _login(value: Any) -> str:
    value = _text(value, 25).lower()
    return value if re.fullmatch(r"[a-z0-9_]{1,25}", value) else ""


def _scopes(value: Any) -> set[str]:
    items = value if isinstance(value, list) else value.split() if isinstance(value, str) else []
    return {item.strip() for item in items if isinstance(item, str) and re.fullmatch(r"[a-z]+(?::[a-z]+)+", item.strip())}


def _positive_int(value: Any, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if 0 < number <= maximum else default


def _verification_uri(value: Any) -> str:
    value = _text(value, 200)
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme != "https" or parsed.hostname not in {"twitch.tv", "www.twitch.tv"} or parsed.path != "/activate" or parsed.username or parsed.password or parsed.fragment:
        return ""
    return value


def _oauth_error(data: Any) -> str:
    value = _text(data.get("message"), 80).lower() if isinstance(data, dict) else ""
    return value if value in {"authorization_pending", "slow_down", "expired_token"} else ""
