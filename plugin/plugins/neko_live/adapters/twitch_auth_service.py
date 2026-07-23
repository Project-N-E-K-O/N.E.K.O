"""Twitch OAuth Device Code Flow with encrypted credential callbacks.

Twitch device codes are deliberately held only in this service instance. Access
and refresh tokens are handed to the injected credential store only after the
access token has been validated against Twitch.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

from utils.http.aiohttp_proxy import aiohttp_session_kwargs_for_url


CredentialProvider = Callable[[], Awaitable[dict[str, Any] | None]]
CredentialSaver = Callable[[dict[str, Any]], Awaitable[bool]]
CredentialReloader = Callable[[], Awaitable[None]]
RequestJson = Callable[..., Awaitable[tuple[int, dict[str, Any]]]]

_DEVICE_URL = "https://id.twitch.tv/oauth2/device"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
_SCOPES = ("user:read:chat",)
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9]{8,80}$")


@dataclass(slots=True)
class _DeviceSession:
    client_id: str
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float
    expires_in: int
    interval: int


class TwitchAuthService:
    """Device Flow session management and token validation/refresh."""

    def __init__(
        self,
        *,
        logger: Any = None,
        credential_provider: CredentialProvider,
        credential_saver: CredentialSaver,
        credential_reloader: CredentialReloader,
        request_json: RequestJson | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.logger = logger
        self._credential_provider = credential_provider
        self._credential_saver = credential_saver
        self._credential_reloader = credential_reloader
        self._request_json = request_json or _request_json
        self._clock = clock
        self._device_session: _DeviceSession | None = None
        self._device_authorization_lock = asyncio.Lock()

    async def start_device_authorization(self, client_id: Any) -> dict[str, Any]:
        normalized_client_id = _client_id(client_id)
        if not normalized_client_id:
            self._device_session = None
            self._log("warning", "stage=invalid_client_id flow=device_authorization")
            return _error("invalid twitch client id")
        request_started = time.perf_counter()
        self._log(
            "info",
            "stage=request_start flow=device_authorization "
            f"client_id_len={len(normalized_client_id)} "
            f"trust_env={aiohttp_session_kwargs_for_url(_DEVICE_URL).get('trust_env') is True} "
            f"proxy_env_present={_proxy_env_present()}",
        )
        try:
            status, data = await self._request(
                "POST",
                _DEVICE_URL,
                data={"client_id": normalized_client_id, "scopes": " ".join(_SCOPES)},
            )
        except Exception as exc:
            self._log(
                "error",
                "stage=request_error flow=device_authorization "
                f"error_type={type(exc).__name__} elapsed_ms={_elapsed_ms(request_started)}",
            )
            raise
        self._log(
            "info",
            f"stage=response status={status} flow=device_authorization elapsed_ms={_elapsed_ms(request_started)}",
        )
        device_code = _text(data.get("device_code"), limit=512)
        user_code = _text(data.get("user_code"), limit=64)
        verification_uri = _verification_uri(data.get("verification_uri"))
        expires_in = _positive_int(data.get("expires_in"), default=0, maximum=3600)
        interval = _positive_int(data.get("interval"), default=5, maximum=60)
        if status != 200 or not all((device_code, user_code, verification_uri, expires_in)):
            self._device_session = None
            self._log(
                "warning",
                "stage=response_invalid flow=device_authorization "
                f"status={status} user_code_present={bool(user_code)} "
                f"verification_uri_present={bool(verification_uri)}",
            )
            return _error("twitch device authorization could not be started")
        self._device_session = _DeviceSession(
            client_id=normalized_client_id,
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            expires_at=self._clock() + expires_in,
            expires_in=expires_in,
            interval=interval,
        )
        self._log(
            "info",
            "stage=ready "
            f"user_code_present={bool(user_code)} verification_uri_present={bool(verification_uri)} "
            f"expires_in={expires_in} interval={interval}",
        )
        return _pending_status(self._device_session, now=self._clock(), started=True)

    def device_authorization_status(self, client_id: Any) -> dict[str, Any] | None:
        """Return a public view of the active Device Flow session, if any."""
        normalized_client_id = _client_id(client_id)
        session = self._device_session
        if session is None or normalized_client_id != session.client_id:
            return None
        if self._clock() >= session.expires_at:
            self._device_session = None
            return None
        return _pending_status(session, now=self._clock())

    async def cancel_device_authorization(self, client_id: Any) -> dict[str, Any]:
        """Cancel the active Device Flow session without exposing its secrets."""
        normalized_client_id = _client_id(client_id)
        requested_session = self._device_session
        if requested_session is None or normalized_client_id != requested_session.client_id:
            return _cancelled_status(cancelled=False)
        async with self._device_authorization_lock:
            if self._device_session is requested_session:
                self._device_session = None
                return _cancelled_status(cancelled=True)
            credential = await self._credential_provider()
            if _credential_matches_client(credential, normalized_client_id):
                result = _public_status(credential or {}, refreshed=False)
                result["cancelled"] = False
                return result
            return _cancelled_status(cancelled=False)

    def _log(self, level: str, message: str) -> None:
        logger = self.logger
        if logger is None:
            return
        writer = getattr(logger, level, None)
        if not callable(writer):
            return
        try:
            writer(f"[Twitch OAuth] {message}")
        except Exception:
            return

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        endpoint = _endpoint_name(url)
        for attempt in (1, 2):
            self._log(
                "info",
                f"stage=request_attempt endpoint={endpoint} attempt={attempt}",
            )
            try:
                return await self._request_json(
                    method,
                    url,
                    headers=headers,
                    data=data,
                )
            except Exception as exc:
                if attempt >= 2 or not _retryable_request_error(exc):
                    raise
                self._log(
                    "warning",
                    f"stage=request_retry endpoint={endpoint} attempt={attempt} "
                    f"error_type={type(exc).__name__}",
                )
                await asyncio.sleep(0.25)
        raise RuntimeError("unreachable twitch request retry state")

    async def check_device_authorization(self, client_id: Any) -> dict[str, Any]:
        async with self._device_authorization_lock:
            return await self._check_device_authorization_locked(client_id)

    async def _check_device_authorization_locked(self, client_id: Any) -> dict[str, Any]:
        normalized_client_id = _client_id(client_id)
        session = self._device_session
        if session is None or normalized_client_id != session.client_id:
            return _error("twitch device authorization is not active")
        if self._clock() >= session.expires_at:
            self._device_session = None
            return _error("twitch device authorization expired")
        status, data = await self._request(
            "POST",
            _TOKEN_URL,
            data={
                "client_id": session.client_id,
                "scopes": " ".join(_SCOPES),
                "device_code": session.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        if self._device_session is not session:
            return _error("twitch device authorization is not active")
        if status != 200:
            message = _oauth_error(data)
            if message == "slow_down":
                session.interval = min(60, session.interval + 5)
            if message in {"authorization_pending", "slow_down"}:
                result = _pending_status(session, now=self._clock())
                result["message"] = message
                return result
            if message in {"access_denied", "expired_token", "invalid device code"}:
                self._device_session = None
            return _error("twitch authorization failed")
        credential = await self._validated_credential(session.client_id, data)
        if self._device_session is not session:
            return _error("twitch device authorization is not active")
        if credential is None:
            self._device_session = None
            return _error("twitch access token validation failed")
        if not await self._credential_saver(credential):
            self._device_session = None
            return _error("twitch credential save failed")
        await self._credential_reloader()
        self._device_session = None
        return _public_status(credential, refreshed=False)

    async def check_credential(self, client_id: Any) -> dict[str, Any]:
        normalized_client_id = _client_id(client_id)
        if not normalized_client_id:
            return _error("invalid twitch client id")
        current = await self._credential_provider()
        access_token = _secret(current, "access_token")
        if not access_token:
            return _error("twitch authorization is required")
        validated = await self._validate_token(access_token, normalized_client_id)
        if validated is not None:
            merged = _merge_validated(current or {}, validated, clock=self._clock())
            return _public_status(merged, refreshed=False)
        refresh_token = _secret(current, "refresh_token")
        if not refresh_token:
            return _error("twitch authorization expired")
        status, token_data = await self._request(
            "POST",
            _TOKEN_URL,
            data={
                "client_id": normalized_client_id,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if status != 200:
            return _error("twitch token refresh failed")
        refreshed = await self._validated_credential(normalized_client_id, token_data)
        if refreshed is None:
            return _error("twitch refreshed token validation failed")
        if not await self._credential_saver(refreshed):
            return _error("twitch credential save failed")
        await self._credential_reloader()
        return _public_status(refreshed, refreshed=True)

    async def _validated_credential(self, client_id: str, token_data: dict[str, Any]) -> dict[str, Any] | None:
        access_token = _secret(token_data, "access_token")
        refresh_token = _secret(token_data, "refresh_token")
        if not access_token or not refresh_token:
            return None
        validated = await self._validate_token(access_token, client_id)
        if validated is None:
            return None
        return _merge_validated(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            validated,
            clock=self._clock(),
        )

    async def _validate_token(self, access_token: str, client_id: str) -> dict[str, Any] | None:
        status, data = await self._request(
            "GET",
            _VALIDATE_URL,
            headers={"Authorization": f"OAuth {access_token}"},
        )
        if status != 200 or _client_id(data.get("client_id")) != client_id:
            return None
        user_id = _text(data.get("user_id"), limit=64)
        login = _login(data.get("login"))
        scopes = _scopes(data.get("scopes"))
        if not user_id or not login or not set(_SCOPES).issubset(scopes):
            return None
        return {
            "client_id": client_id,
            "user_id": user_id,
            "login": login,
            "scopes": " ".join(sorted(scopes)),
            "expires_in": str(_positive_int(data.get("expires_in"), default=0, maximum=31_536_000)),
        }


async def _request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    session_kwargs = aiohttp_session_kwargs_for_url(url)
    async with aiohttp.ClientSession(timeout=timeout, **session_kwargs) as session:
        async with session.request(method, url, headers=headers, data=data) as response:
            try:
                payload = await response.json(content_type=None)
            except Exception:
                payload = {}
            return response.status, payload if isinstance(payload, dict) else {}


def _merge_validated(current: dict[str, Any], validated: dict[str, Any], *, clock: float) -> dict[str, Any]:
    expires_in = _positive_int(validated.get("expires_in"), default=0, maximum=31_536_000)
    return {
        "access_token": _secret(current, "access_token"),
        "refresh_token": _secret(current, "refresh_token"),
        "client_id": _client_id(validated.get("client_id")),
        "user_id": _text(validated.get("user_id"), limit=64),
        "login": _login(validated.get("login")),
        "display_name": _text(current.get("display_name"), limit=80),
        "scopes": " ".join(sorted(_scopes(validated.get("scopes")))),
        "expires_at": str(int(clock + expires_in)),
    }


def _public_status(credential: dict[str, Any], *, refreshed: bool) -> dict[str, Any]:
    return {
        "platform": "twitch",
        "logged_in": True,
        "pending": False,
        "authorization_state": "authorized",
        "user_id": _text(credential.get("user_id"), limit=64),
        "login": _login(credential.get("login")),
        "display_name": _text(credential.get("display_name"), limit=80),
        "scopes": sorted(_scopes(credential.get("scopes"))),
        "expires_at": _text(credential.get("expires_at"), limit=24),
        "refreshed": refreshed is True,
    }


def _error(message: str) -> dict[str, Any]:
    return {
        "platform": "twitch",
        "logged_in": False,
        "pending": False,
        "authorization_state": "unauthorized",
        "message": message,
    }


def _cancelled_status(*, cancelled: bool) -> dict[str, Any]:
    return {
        "platform": "twitch",
        "logged_in": False,
        "pending": False,
        "authorization_state": "unauthorized",
        "cancelled": cancelled,
    }


def _credential_matches_client(credential: Any, client_id: str) -> bool:
    return (
        isinstance(credential, dict)
        and _client_id(credential.get("client_id")) == client_id
        and bool(_secret(credential, "access_token"))
        and bool(_secret(credential, "refresh_token"))
    )


def _pending_status(session: _DeviceSession, *, now: float, started: bool = False) -> dict[str, Any]:
    return {
        "platform": "twitch",
        "started": started,
        "logged_in": False,
        "pending": True,
        "authorization_state": "pending",
        "user_code": session.user_code,
        "verification_uri": session.verification_uri,
        "expires_in": max(0, int(session.expires_at - now)),
        "interval": session.interval,
    }


def _client_id(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text if _CLIENT_ID_RE.fullmatch(text) else ""


def _secret(data: Any, key: str) -> str:
    if not isinstance(data, dict) or not isinstance(data.get(key), str):
        return ""
    return data[key].strip()


def _text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:limit]


def _login(value: Any) -> str:
    text = _text(value, limit=25).lower()
    return text if re.fullmatch(r"[a-z0-9_]{1,25}", text) else ""


def _scopes(value: Any) -> set[str]:
    if isinstance(value, str):
        items = value.split()
    elif isinstance(value, list):
        items = [item for item in value if isinstance(item, str)]
    else:
        items = []
    return {item.strip() for item in items if re.fullmatch(r"[a-z]+(?::[a-z]+)+", item.strip())}


def _positive_int(value: Any, *, default: int, maximum: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if 0 < number <= maximum else default


def _elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def _proxy_env_present() -> bool:
    return any(
        bool(os.environ.get(name))
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
    )


def _endpoint_name(url: str) -> str:
    if url == _DEVICE_URL:
        return "device"
    if url == _TOKEN_URL:
        return "token"
    if url == _VALIDATE_URL:
        return "validate"
    return "unknown"


def _retryable_request_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    try:
        import aiohttp
    except Exception:
        return False
    return isinstance(exc, aiohttp.ClientConnectionError)


def _verification_uri(value: Any) -> str:
    text = _text(value, limit=200)
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return ""
    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"twitch.tv", "www.twitch.tv"}
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/activate"
        or parsed.fragment
    ):
        return ""
    return text


def _oauth_error(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    message = _text(data.get("message"), limit=80).lower()
    return message if message in {"access_denied", "authorization_pending", "slow_down", "expired_token", "invalid device code"} else ""
