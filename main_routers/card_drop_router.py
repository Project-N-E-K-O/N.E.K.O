"""Community-login + forge-credit proxy routes for NEKO (``/api/card-drop`` prefix).

Drop decisions remain in the private NEKO-PC forge-dropper, while this service
owns the installation-local credit ledger. The community may read and reserve
credits only after its JWT has been synced through the native one-time ticket.
Desktop community login is OAuth PKCE via ``main_routers.community_oauth``
(``/api/card-drop/oauth/*`` + ``/oauth/callback``). Legacy password/Steam
endpoints return 410. ``/auth-status`` remains here.

The cloud contract lives in N.E.K.O.Servers ``app/modules/cards/router.py``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger("neko.card_drop")

router = APIRouter(prefix="/api/card-drop", tags=["card-drop"])

_HTTP_TIMEOUT_SEC = 60.0
_DEFAULT_SOCIAL_BASE_URL = "http://localhost:8080"
_SOCIAL_SESSION_FILENAME = "social_session.json"
_SOCIAL_SESSION_SCHEMA_VERSION = 2
_BIND_OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"
_PLATFORM_TOKEN_SYNC_FORBIDDEN = "platform_token_native_sync_forbidden"
_SYNC_TICKET_TTL_SEC = 5 * 60
_SYNC_TICKET_MAX_ACTIVE = 16
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SUPPORTED_AUTH_SOURCES = frozenset({"legacy", "oauth"})
_native_sync_tickets: dict[str, float] = {}


class _ClientBindingConflict(Exception):
    """The local client belongs to another cloud user; do not publish new JWTs."""

    detail = _BIND_OWNERSHIP_CONFLICT


class _InvalidIdentityResponse(Exception):
    """The cloud accepted a token but did not return the frozen identity contract."""

    detail = "invalid_identity_response"


@dataclass(frozen=True)
class _CloudIdentity:
    local_user_id: str
    auth_source: str
    user: dict


@dataclass(frozen=True)
class _CloudIdentityLookup:
    identity: _CloudIdentity | None
    status_code: int
    failure: str | None = None


def _sync_ticket_digest(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()


def _normalize_sync_ticket(value: object) -> str:
    ticket = value.strip() if isinstance(value, str) else ""
    if not 32 <= len(ticket) <= 256:
        return ""
    if any(not (char.isalnum() or char in "_-") for char in ticket):
        return ""
    return ticket


def _prune_sync_tickets(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    expired = [digest for digest, expires_at in _native_sync_tickets.items() if expires_at <= current]
    for digest in expired:
        _native_sync_tickets.pop(digest, None)


def _issue_sync_ticket() -> str:
    now = time.monotonic()
    _prune_sync_tickets(now)
    while len(_native_sync_tickets) >= _SYNC_TICKET_MAX_ACTIVE:
        oldest = min(_native_sync_tickets, key=_native_sync_tickets.get)
        _native_sync_tickets.pop(oldest, None)
    ticket = secrets.token_urlsafe(32)
    _native_sync_tickets[_sync_ticket_digest(ticket)] = now + _SYNC_TICKET_TTL_SEC
    return ticket


def _sync_ticket_is_valid(value: object) -> bool:
    ticket = _normalize_sync_ticket(value)
    if not ticket:
        return False
    now = time.monotonic()
    _prune_sync_tickets(now)
    return _native_sync_tickets.get(_sync_ticket_digest(ticket), 0) > now


def _consume_sync_ticket(value: object) -> bool:
    ticket = _normalize_sync_ticket(value)
    if not ticket:
        return False
    now = time.monotonic()
    _prune_sync_tickets(now)
    digest = _sync_ticket_digest(ticket)
    expires_at = _native_sync_tickets.pop(digest, 0)
    return expires_at > now


def _social_base_url() -> str:
    """Return the cloud base URL, falling back to the local dev default."""
    raw = (os.environ.get("NEKO_SOCIAL_BASE_URL", "") or "").strip().rstrip("/")
    return raw or _DEFAULT_SOCIAL_BASE_URL


def _get_client_credentials() -> tuple[str, str] | None:
    """Return the persisted local client id and binding proof."""
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        client_id, client_proof = cm.ensure_cloudsave_client_credentials()
        if not client_id or not client_proof:
            return None
        return client_id, client_proof
    except Exception as exc:  # noqa: BLE001
        logger.warning("card_drop: failed to load or persist client credentials: %s", exc)
    return None


def _get_client_id() -> str | None:
    credentials = _get_client_credentials()
    return credentials[0] if credentials else None


def _require_ctx() -> tuple[str, str]:
    cid = _get_client_id()
    if not cid:
        raise HTTPException(status_code=409, detail="client_not_registered")
    return _social_base_url(), cid


def _relay(r: httpx.Response):
    """Relay a cloud response, returning JSON or raising an HTTP error."""
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail") or r.text[:200]
        except Exception:  # noqa: BLE001
            detail = r.text[:200]
        raise HTTPException(status_code=r.status_code, detail=detail)
    return r.json()


def _origin_port(parsed) -> int | None:
    if parsed.port:
        return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def _same_originish(a: str, b: str) -> bool:
    try:
        pa = urlparse(a)
        pb = urlparse(b)
    except Exception:  # noqa: BLE001
        return False
    if pa.scheme != pb.scheme or _origin_port(pa) != _origin_port(pb):
        return False
    ha = (pa.hostname or "").lower()
    hb = (pb.hostname or "").lower()
    if ha == hb:
        return True
    return ha in _LOOPBACK_HOSTS and hb in _LOOPBACK_HOSTS


def _normalized_origin(value: str) -> str:
    """Return a browser-style HTTP(S) origin, or an empty string when invalid."""
    try:
        parsed = urlparse((value or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        port = _origin_port(parsed)
    except (TypeError, ValueError):
        return ""
    default_port = 80 if parsed.scheme == "http" else 443
    suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{parsed.scheme}://{host}{suffix}"


def _exact_origin_matches(a: str, b: str) -> bool:
    left = _normalized_origin(a)
    return bool(left and left == _normalized_origin(b))


def _local_mutation_origin_allowed(request: Request) -> bool:
    """Allow native callers or browser requests from the local NEKO origin only."""
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return True
    try:
        origin_host = (urlparse(origin).hostname or "").lower()
        request_base = str(request.base_url).rstrip("/")
        request_host = (urlparse(request_base).hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return (
        origin_host in _LOOPBACK_HOSTS
        and request_host in _LOOPBACK_HOSTS
        and _same_originish(origin, request_base)
    )


def _require_local_mutation_ticket(request: Request, payload: dict | None) -> None:
    """Authorize a local state mutation and atomically consume its ticket."""
    if not _local_mutation_origin_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    sync_ticket = (payload or {}).get("sync_ticket") or (payload or {}).get(
        "syncTicket"
    )
    if not _consume_sync_ticket(sync_ticket):
        raise HTTPException(status_code=403, detail="invalid_sync_ticket")


def _local_request_source_allowed(request: Request) -> bool:
    """Allow same-origin local browser calls and non-browser native clients only."""
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    fetch_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if not origin:
        # Native HTTP clients do not send Fetch Metadata.  A browser-originated
        # blind GET (img/fetch no-cors) does, so it cannot churn the bounded pool.
        return fetch_site in {"", "same-origin"}
    request_origin = str(request.base_url).rstrip("/")
    try:
        origin_host = (urlparse(origin).hostname or "").lower()
        request_host = (urlparse(request_origin).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return (
        origin_host in _LOOPBACK_HOSTS
        and request_host in _LOOPBACK_HOSTS
        and _exact_origin_matches(origin, request_origin)
        and fetch_site in {"", "same-origin"}
    )


def _sync_cors_headers(request: Request) -> dict[str, str] | None:
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return {}
    if not _same_originish(origin, _social_base_url()):
        return None
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }
    if (request.headers.get("access-control-request-private-network") or "").lower() == "true":
        headers["Access-Control-Allow-Private-Network"] = "true"
    return headers


def _session_status_cors_headers(request: Request) -> dict[str, str] | None:
    """Exact configured-origin CORS for the read-only desktop sync status check."""
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin or not _exact_origin_matches(origin, _social_base_url()):
        return None
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "authorization",
        "Access-Control-Max-Age": "600",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Vary": "Origin",
    }
    if (request.headers.get("access-control-request-private-network") or "").lower() == "true":
        headers["Access-Control-Allow-Private-Network"] = "true"
    return headers


def _facts_cors_headers(request: Request) -> dict[str, str] | None:
    """CORS for private local-memory reads; an explicit trusted Origin is mandatory."""
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin or not _same_originish(origin, _social_base_url()):
        return None
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "authorization, content-type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
    }
    if (request.headers.get("access-control-request-private-network") or "").lower() == "true":
        headers["Access-Control-Allow-Private-Network"] = "true"
    return headers


def _credit_cors_headers(request: Request) -> dict[str, str] | None:
    headers = _facts_cors_headers(request)
    if headers is not None:
        headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return headers


# ---- 社区账号登录：JWT 存本地 community_auth.json；draw 时带 Authorization ----
_AUTH_FILENAME = "community_auth.json"


def _auth_path() -> Path | None:
    try:
        from utils.config_manager import get_config_manager
        return Path(get_config_manager().memory_dir).parent / _AUTH_FILENAME
    except Exception as exc:  # noqa: BLE001
        logger.debug("card_drop: auth path resolve failed: %s", exc)
        return None


def _legacy_social_session_path() -> Path | None:
    p = _auth_path()
    return (p.parent / _SOCIAL_SESSION_FILENAME) if p else None


def _social_session_path() -> Path | None:
    """Return the Electron-visible session path when the desktop host supplies it."""
    override = (os.environ.get("NEKO_USER_DATA_DIR") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_absolute():
            return candidate / _SOCIAL_SESSION_FILENAME
        logger.warning("card_drop: ignoring relative NEKO_USER_DATA_DIR")
    return _legacy_social_session_path()


def _social_session_paths() -> list[Path]:
    paths: list[Path] = []
    for candidate in (_social_session_path(), _legacy_social_session_path()):
        if candidate is not None and candidate not in paths:
            paths.append(candidate)
    return paths


def _read_json_dict(path: Path | None) -> dict | None:
    if not path or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _load_auth() -> dict | None:
    return _read_json_dict(_auth_path())


def _load_social_session() -> dict | None:
    """Load the authoritative Electron session, with the legacy path as fallback."""
    for path in _social_session_paths():
        data = _read_json_dict(path)
        if data and isinstance(data.get("token"), str) and data["token"].strip():
            return data
    return None


def _normalize_local_user_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError):
        return ""


def _normalize_auth_source(value: object) -> str:
    source = value.strip().lower() if isinstance(value, str) else ""
    return source if source in _SUPPORTED_AUTH_SOURCES else ""


def _desktop_session_snapshot() -> dict | None:
    """Normalize the desktop session while preferring Electron's refreshed token."""
    social = _load_social_session()
    if social is not None:
        return {
            "base_url": str(social.get("baseUrl") or _social_base_url()).strip().rstrip("/"),
            "access_token": str(social.get("token") or "").strip(),
            "refresh_token": (
                str(social.get("refresh_token") or "").strip() or None
            ),
            "local_user_id": _normalize_local_user_id(social.get("local_user_id")),
            "auth_source": _normalize_auth_source(social.get("auth_source")),
        }

    auth = _load_auth()
    if auth is None:
        return None
    access = auth.get("access_token")
    if not isinstance(access, str) or not access.strip():
        return None
    return {
        "base_url": _social_base_url(),
        "access_token": access.strip(),
        "refresh_token": (
            str(auth.get("refresh_token") or "").strip() or None
        ),
        "local_user_id": _normalize_local_user_id(auth.get("local_user_id")),
        "auth_source": _normalize_auth_source(auth.get("auth_source")),
    }


def _write_private_json(path: Path, data: dict) -> None:
    """Atomically persist local credentials with owner-only permissions where supported."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _save_auth(data: dict) -> bool:
    p = _auth_path()
    if not p:
        return False
    try:
        _write_private_json(p, data)
    except OSError as exc:
        logger.warning("card_drop: save auth failed: %s", exc)
        return False
    return True


def _save_social_session(
    base: str,
    access: str | None,
    refresh: str | None,
    *,
    local_user_id: str,
    auth_source: str,
    auth_public_url: str | None = None,
    client_id: str | None = None,
) -> bool:
    normalized_user_id = _normalize_local_user_id(local_user_id)
    normalized_source = _normalize_auth_source(auth_source)
    if not access or not normalized_user_id or not normalized_source:
        return False
    p = _social_session_path()
    if not p:
        return False
    data = {
        "schema_version": _SOCIAL_SESSION_SCHEMA_VERSION,
        "baseUrl": (base or _social_base_url()).strip().rstrip("/"),
        "token": access,
        "access_token": access,
        "local_user_id": normalized_user_id,
        "auth_source": normalized_source,
    }
    if refresh:
        data["refresh_token"] = refresh
    auth_url = (auth_public_url or "").strip().rstrip("/")
    if auth_url:
        data["auth_public_url"] = auth_url
    oauth_client = (client_id or "").strip()
    if oauth_client:
        data["client_id"] = oauth_client
    try:
        _write_private_json(p, data)
    except OSError as exc:
        logger.warning("card_drop: save social session failed: %s", exc)
        return False
    return True


def _persist_session_identity_metadata(
    snapshot: dict,
    local_user_id: str,
    auth_source: str,
) -> bool:
    """Upgrade a validated legacy session without copying the request's bearer.

    Electron may rotate the file while the cloud identity lookup is in flight.
    Merge only into the exact credential snapshot that was validated, preserving
    refresh-manager fields instead of reconstructing (and potentially rolling
    back) the whole session record.
    """
    normalized_user_id = _normalize_local_user_id(local_user_id)
    normalized_source = _normalize_auth_source(auth_source)
    if not normalized_user_id or not normalized_source:
        return False

    expected_access = str(snapshot.get("access_token") or "").strip()
    expected_base = str(snapshot.get("base_url") or _social_base_url()).strip().rstrip("/")
    expected_refresh = str(snapshot.get("refresh_token") or "").strip()
    social_saved = False
    found_social_session = False
    for path in _social_session_paths():
        data = _read_json_dict(path)
        access = str((data or {}).get("token") or "").strip()
        if not data or not access:
            continue
        found_social_session = True
        base = str(data.get("baseUrl") or _social_base_url()).strip().rstrip("/")
        refresh = str(data.get("refresh_token") or "").strip()
        if (access, base, refresh) != (expected_access, expected_base, expected_refresh):
            # The authoritative Electron session changed after validation.
            return False
        upgraded = {
            **data,
            "schema_version": _SOCIAL_SESSION_SCHEMA_VERSION,
            "local_user_id": normalized_user_id,
            "auth_source": normalized_source,
        }
        try:
            _write_private_json(path, upgraded)
        except OSError as exc:
            logger.warning("card_drop: save social identity metadata failed: %s", exc)
            return False
        social_saved = True
        break

    if not found_social_session:
        # A pre-Electron community_auth.json session still needs the companion
        # file. It is safe to create because no authoritative social file exists.
        social_saved = _save_social_session(
            expected_base,
            expected_access,
            expected_refresh or None,
            local_user_id=normalized_user_id,
            auth_source=normalized_source,
        )

    auth = _load_auth()
    auth_saved = True
    if auth is not None:
        auth["schema_version"] = _SOCIAL_SESSION_SCHEMA_VERSION
        auth["local_user_id"] = normalized_user_id
        auth["auth_source"] = normalized_source
        user = auth.get("user") if isinstance(auth.get("user"), dict) else {}
        auth["user"] = {**user, "id": auth["local_user_id"]}
        auth_saved = _save_auth(auth)
    return social_saved and auth_saved


def _clear_auth() -> bool:
    auth_path = _auth_path()
    paths = ([auth_path] if auth_path is not None else []) + _social_session_paths()
    paths = list(dict.fromkeys(paths))
    if auth_path is None:
        logger.warning("card_drop: cannot resolve auth path while clearing credentials")
    success = auth_path is not None
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            success = False
            logger.warning("card_drop: clear credential failed for %s: %s", path, exc)
    for path in paths:
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            success = False
            logger.warning("card_drop: cannot verify credential clear for %s: %s", path, exc)
        else:
            success = False
            logger.warning("card_drop: credential still exists after clear: %s", path)
    return success


def _access_token() -> str | None:
    session = _desktop_session_snapshot()
    return session.get("access_token") if session else None


async def _lookup_cloud_identity(base: str, access: str) -> _CloudIdentityLookup:
    """Validate one bearer with Servers without logging or persisting it."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            response = await client.get(
                f"{base}/api/users/me",
                headers={"Authorization": f"Bearer {access}"},
            )
    except (httpx.HTTPError, OSError):
        return _CloudIdentityLookup(None, 503, "unavailable")

    if response.status_code >= 400:
        failure = "unavailable" if response.status_code >= 500 else "rejected"
        return _CloudIdentityLookup(None, response.status_code, failure)
    try:
        payload = response.json() or {}
    except (ValueError, TypeError):
        return _CloudIdentityLookup(None, 502, "malformed")
    if not isinstance(payload, dict):
        return _CloudIdentityLookup(None, 502, "malformed")
    user = payload.get("user")
    if not isinstance(user, dict):
        return _CloudIdentityLookup(None, 502, "malformed")
    local_user_id = _normalize_local_user_id(user.get("id"))
    auth_source = _normalize_auth_source(payload.get("auth_source"))
    if not local_user_id or not auth_source:
        return _CloudIdentityLookup(None, 502, "malformed")
    return _CloudIdentityLookup(
        _CloudIdentity(
            local_user_id=local_user_id,
            auth_source=auth_source,
            user=user,
        ),
        200,
    )


async def _resolve_saved_desktop_identity(base: str) -> _CloudIdentityLookup:
    """Return persisted UUID metadata, securely upgrading pre-v2 sessions."""
    for _attempt in range(3):
        snapshot = await asyncio.to_thread(_desktop_session_snapshot)
        if snapshot is None:
            return _CloudIdentityLookup(None, 200, "missing")
        local_user_id = snapshot.get("local_user_id") or ""
        auth_source = snapshot.get("auth_source") or ""
        if local_user_id and auth_source:
            return _CloudIdentityLookup(
                _CloudIdentity(local_user_id, auth_source, {}),
                200,
            )

        lookup = await _lookup_cloud_identity(base, snapshot["access_token"])
        if lookup.identity is None:
            return lookup
        if await asyncio.to_thread(
            _persist_session_identity_metadata,
            snapshot,
            lookup.identity.local_user_id,
            lookup.identity.auth_source,
        ):
            return lookup

        current = await asyncio.to_thread(_desktop_session_snapshot)
        if current is not None and all(
            current.get(field) == snapshot.get(field)
            for field in ("base_url", "access_token", "refresh_token")
        ):
            # Metadata persistence failed, but the validated desktop credential
            # did not change. The proof remains valid for this request.
            return lookup
        # A concurrent refresh/account switch won. Re-resolve the current file
        # rather than comparing the request against stale identity A.
    return _CloudIdentityLookup(None, 503, "unavailable")


async def _request_matches_desktop_session(base: str, access: str) -> str:
    """Return ``match``, ``mismatch``, or ``unavailable`` for a request bearer."""
    request_lookup = await _lookup_cloud_identity(base, access)
    if request_lookup.identity is None:
        return "unavailable" if request_lookup.failure in {"unavailable", "malformed"} else "mismatch"
    desktop_lookup = await _resolve_saved_desktop_identity(base)
    if desktop_lookup.identity is None:
        return "unavailable" if desktop_lookup.failure in {"unavailable", "malformed"} else "mismatch"
    if secrets.compare_digest(
        request_lookup.identity.local_user_id,
        desktop_lookup.identity.local_user_id,
    ):
        return "match"
    return "mismatch"
async def _store_session(
    base: str,
    access: str | None,
    refresh: str | None,
    user: dict,
    *,
    auth_source: str = "legacy",
    bind_client: bool = True,
) -> dict:
    """Store JWTs and optionally bind the legacy guest client to the user.

    Email/password and Steam login share this path. The bind result is persisted
    and exposed through auth-status so client-binding conflicts are visible. A
    browser native-session sync deliberately skips client binding: forge credits
    belong to the installation and must remain readable after switching accounts.
    """
    local_user_id = _normalize_local_user_id(user.get("id"))
    normalized_source = _normalize_auth_source(auth_source)
    if not access or not local_user_id or not normalized_source:
        raise _InvalidIdentityResponse()

    # Bind before publishing either local credential file.  A client that belongs to another
    # user is a non-recoverable identity conflict: publishing the new JWT even briefly would let
    # Electron observe and use the wrong account before this request reports the conflict.
    # Other historical bind failures remain recoverable and still persist the cloud-validated
    # login below, together with their bind status.
    bind: dict = {"bound": False, "error": None}
    cid = _get_client_id() if bind_client else None
    if not bind_client:
        bind["skipped"] = "native_session_sync"
    elif not cid:
        bind["error"] = "client_not_registered"
    elif access:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
                r = await client.post(
                    f"{base}/api/auth/bind-client",
                    headers={"Authorization": f"Bearer {access}"},
                    json={"client_id": cid},
                )
            if r.status_code < 400:
                bind["bound"] = True
            else:
                try:
                    bind["error"] = r.json().get("detail") or f"http_{r.status_code}"
                except (ValueError, KeyError, AttributeError):
                    bind["error"] = f"http_{r.status_code}"
                logger.info("card_drop: bind-client returned %s: %s", r.status_code, bind["error"])
        except (httpx.HTTPError, OSError) as exc:
            bind["error"] = "cloud_unreachable"
            logger.info("card_drop: bind-client after login failed: %s", exc)

    if bind.get("error") == _BIND_OWNERSHIP_CONFLICT:
        raise _ClientBindingConflict()

    auth_payload = {
        "schema_version": _SOCIAL_SESSION_SCHEMA_VERSION,
        "access_token": access,
        "refresh_token": refresh,
        "local_user_id": local_user_id,
        "auth_source": normalized_source,
        "user": {
            "id": local_user_id,
            "display_name": user.get("display_name"),
            "email": user.get("email"),
        },
        "bind": bind,
    }
    auth_saved = _save_auth(auth_payload)
    social_saved = _save_social_session(
        base,
        access,
        refresh,
        local_user_id=local_user_id,
        auth_source=normalized_source,
    )
    if not (auth_saved and social_saved):
        bind["local_save_failed"] = True
        # If auth was written before the Electron session failed, persist the
        # partial-success marker there as well so auth-status can surface it.
        if auth_saved:
            _save_auth(auth_payload)
    return bind


async def _finish_login(base: str, login_out: dict) -> tuple[dict, dict]:
    """Complete email/password login by storing JWTs and binding the client."""
    tokens = login_out.get("tokens") or {}
    user = login_out.get("user") or {}
    try:
        bind = await _store_session(
            base,
            tokens.get("access_token"),
            tokens.get("refresh_token"),
            user,
            auth_source="legacy",
        )
    except _ClientBindingConflict as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except _InvalidIdentityResponse as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc
    return user, bind


# ---- Steam 登录：开浏览器到云端 OpenID → 云端验完重定向回本地 /steam-callback ----
# CSRF/会话固定防护：/steam-callback 用 access_token query 参数落地，是个本机端点，恶意网页
# 可能跨源 GET 它塞入攻击者 token（把用户游客卡 bind 到攻击者账号）。用一次性 pending 标记
# 把回调限定在「用户刚点过 Steam 登录」的短窗口内，挡掉无端调用。
_STEAM_PENDING_FILENAME = "community_steam_pending.json"
_STEAM_PENDING_TTL_SEC = 600  # 点登录后 10 分钟内必须完成回调


def _steam_pending_path() -> Path | None:
    p = _auth_path()
    return (p.parent / _STEAM_PENDING_FILENAME) if p else None


def _pkce_pair() -> tuple[str, str]:
    """Build a PKCE S256 pair ``(code_verifier, code_challenge)`` (RFC 7636).

    The verifier is kept locally (pending file); the challenge is
    ``base64url(sha256(verifier))`` without padding and is sent on the authorize
    URL. The cloud stores the challenge with the one-time code and checks the
    verifier on token exchange, binding the short code to this NEKO instance.
    Verifier uses ``token_urlsafe(32)`` (43 chars; RFC 43..128 unreserved).
    """
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _mark_steam_pending() -> tuple[str, str] | None:
    """Write a one-shot pending marker; return ``(state, code_challenge)``.

    ``state`` is an unguessable CSRF token (checked locally against login-CSRF).
    ``code_challenge`` is the PKCE S256 challenge put on the authorize URL; the
    matching ``code_verifier`` is stored in the pending file and only shown on
    token exchange. Returns ``None`` when persistence fails (no path / write
    error) so callers abort the login entry instead of sending the user into a
    Steam flow that cannot be verified locally.
    """
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()
    p = _steam_pending_path()
    if not p:
        return None
    try:
        _write_private_json(
            p,
            {"ts": time.time(), "state": state, "code_verifier": verifier},
        )
    except OSError as exc:
        logger.debug("card_drop: mark steam pending failed: %s", exc)
        return None
    return state, challenge


def _consume_steam_pending(state: str) -> tuple[bool, str | None]:
    """Consume the one-shot pending marker (exists, fresh, state matches).

    Returns ``(ok, code_verifier)``. On success, ``code_verifier`` is the stored
    PKCE verifier (``None`` for legacy markers without it — exchange omits
    verifier for backward compatibility). Delete the marker only when it is
    corrupt, expired, or the state matches; keep it until TTL on mismatch so a
    wrong-state callback cannot DoS a legitimate login. If state matches but
    delete fails, return ``(False, None)`` to preserve one-shot semantics.
    """
    p = _steam_pending_path()
    if not p or not p.exists():
        return False, None
    data: object = {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}

    def _drop() -> None:
        try:
            p.unlink()
        except OSError:
            pass

    if not isinstance(data, dict):
        _drop()
        return False, None
    try:
        ts = float(data.get("ts", 0) or 0)
    except (TypeError, ValueError):
        ts = 0.0
    stored_state = data.get("state") or ""
    stored_verifier = data.get("code_verifier")
    if not isinstance(stored_verifier, str) or not stored_verifier:
        stored_verifier = None
    if not stored_state or not isinstance(state, str) or not state:
        _drop()
        return False, None
    if not (bool(ts) and (time.time() - ts) <= _STEAM_PENDING_TTL_SEC):
        _drop()  # 过期：清掉
        return False, None
    if not secrets.compare_digest(str(stored_state), state):
        return False, None  # state 不匹配：保留标记，合法回调仍可在 TTL 内成功
    try:
        p.unlink()
    except OSError as exc:
        logger.debug("card_drop: consume steam pending failed: %s", exc)
        return False, None
    return True, stored_verifier


@router.get("/auth-status", summary="社区登录状态")
async def auth_status_endpoint():
    a = await asyncio.to_thread(_load_auth) or {}
    if await asyncio.to_thread(_access_token):
        u = a.get("user") or {}
        # 老会话没存 bind 字段 → 视为已绑（向后兼容，正常单账号场景成立）
        bind = a.get("bind") or {"bound": True, "error": None}
        return {
            "logged_in": True,
            "user": {"display_name": u.get("display_name"), "email": u.get("email")},
            "bind": bind,
        }
    return {"logged_in": False, "user": None, "bind": None}


@router.get("/sync-ticket", summary="签发一次性社区网页登录态同步票据")
async def sync_ticket_endpoint(request: Request):
    """Issue a short-lived ticket readable only by the local NEKO page."""
    if not _local_request_source_allowed(request):
        return JSONResponse(
            {"detail": "origin_not_allowed"},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    return JSONResponse(
        {"sync_ticket": _issue_sync_ticket(), "expires_in": _SYNC_TICKET_TTL_SEC},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.options("/sync-session", summary="社区网页登录态同步预检")
async def sync_session_options(request: Request):
    cors = _sync_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse({"ok": True}, headers=cors)


@router.options("/bind-client/approve", summary="游客 client_id 绑定持有证明预检")
async def bind_client_approve_options(request: Request):
    cors = _sync_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse({"ok": True}, headers=cors)


@router.post("/bind-client/approve", summary="由本机 NEKO 批准游客 client_id 绑定")
async def bind_client_approve_endpoint(request: Request, payload: dict = Body(...)):
    """Prove possession with the persisted local id, never the URL-provided hint."""
    cors = _sync_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    sync_ticket = payload.get("sync_ticket") or payload.get("syncTicket")
    if not _consume_sync_ticket(sync_ticket):
        return JSONResponse(
            {"detail": "invalid_sync_ticket"}, status_code=403, headers=cors
        )
    challenge = payload.get("binding_challenge") or payload.get("bindingChallenge")
    challenge = challenge.strip() if isinstance(challenge, str) else ""
    if not 32 <= len(challenge) <= 256:
        return JSONResponse(
            {"detail": "invalid_client_binding_challenge"}, status_code=400, headers=cors
        )
    credentials = _get_client_credentials()
    if not credentials:
        return JSONResponse(
            {"detail": "client_not_registered"}, status_code=409, headers=cors
        )
    client_id, client_proof = credentials
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            response = await client.post(
                f"{_social_base_url()}/api/clients/bind-approval",
                json={
                    "client_id": client_id,
                    "binding_challenge": challenge,
                    "client_proof": client_proof,
                },
            )
    except (httpx.HTTPError, OSError):
        return JSONResponse(
            {"detail": "cloud_unreachable"}, status_code=502, headers=cors
        )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or f"http_{response.status_code}"
        except (ValueError, TypeError, AttributeError):
            detail = f"http_{response.status_code}"
        return JSONResponse(
            {"detail": detail}, status_code=response.status_code, headers=cors
        )
    return JSONResponse({"ok": True}, headers=cors)


def _sync_status_response(
    synced: bool,
    *,
    status_code: int,
    cors: dict[str, str],
) -> JSONResponse:
    return JSONResponse(
        {"ok": True, "synced": synced},
        status_code=status_code,
        headers=cors,
    )


@router.options("/sync-session/status", summary="社区网页登录态与本地 Desktop 状态预检")
async def sync_session_status_options(request: Request):
    cors = _session_status_cors_headers(request)
    if cors is None:
        return JSONResponse(
            {"ok": True, "synced": False},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    return _sync_status_response(False, status_code=200, cors=cors)


@router.get("/sync-session/status", summary="检查网页账号是否与本地 Desktop 同步")
async def sync_session_status_endpoint(request: Request):
    cors = _session_status_cors_headers(request)
    if cors is None:
        return JSONResponse(
            {"ok": True, "synced": False},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    access = _request_bearer_token(request)
    if not access:
        return _sync_status_response(False, status_code=401, cors=cors)
    match = await _request_matches_desktop_session(_social_base_url(), access)
    if match == "unavailable":
        return _sync_status_response(False, status_code=503, cors=cors)
    return _sync_status_response(match == "match", status_code=200, cors=cors)


@router.post("/sync-session", summary="同步社区网页登录态到本地 NEKO / PC 掉券引擎")
async def sync_session_endpoint(request: Request, payload: dict = Body(...)):
    cors = _sync_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)

    base = (payload.get("base_url") or payload.get("baseUrl") or _social_base_url() or "").strip().rstrip("/")
    if not _same_originish(base, _social_base_url()):
        return JSONResponse({"detail": "base_url_not_allowed"}, status_code=400, headers=cors)

    sync_ticket = payload.get("sync_ticket") or payload.get("syncTicket")
    if not _sync_ticket_is_valid(sync_ticket):
        return JSONResponse({"detail": "invalid_sync_ticket"}, status_code=403, headers=cors)

    clear_requested = bool(
        payload.get("clear")
        or payload.get("logout")
        or str(payload.get("action") or "").strip().lower() in {"clear", "logout"}
    )
    if clear_requested:
        # Logout is account-scoped.  If Web login B could not replace the desktop's bound
        # account A, B's later logout must not erase A's still-valid local session.
        current_access = _access_token() or ""
        requested_access = (
            payload.get("access_token") or payload.get("accessToken") or ""
        ).strip()
        if current_access and (
            not requested_access
            or not secrets.compare_digest(current_access, requested_access)
        ):
            return JSONResponse(
                {"detail": "local_session_mismatch"}, status_code=409, headers=cors
            )
        if not _consume_sync_ticket(sync_ticket):
            return JSONResponse(
                {"detail": "invalid_sync_ticket"}, status_code=403, headers=cors
            )
        if not _clear_auth():
            return JSONResponse(
                {"detail": "local_clear_failed", "cleared": False},
                status_code=500,
                headers=cors,
            )
        return JSONResponse({"ok": True, "cleared": True}, headers=cors)

    access = (payload.get("access_token") or payload.get("accessToken") or "").strip()
    refresh = (payload.get("refresh_token") or payload.get("refreshToken") or "").strip() or None
    if not access:
        return JSONResponse({"detail": "missing_access_token"}, status_code=400, headers=cors)

    lookup = await _lookup_cloud_identity(base, access)
    if lookup.identity is None:
        if lookup.failure in {"unavailable", "malformed"}:
            return JSONResponse(
                {"detail": "identity_verification_unavailable"},
                status_code=503,
                headers=cors,
            )
        return JSONResponse(
            {"detail": "invalid_token"},
            status_code=lookup.status_code,
            headers=cors,
        )
    if lookup.identity.auth_source != "legacy":
        return JSONResponse(
            {"detail": _PLATFORM_TOKEN_SYNC_FORBIDDEN},
            status_code=409,
            headers=cors,
        )
    user = lookup.identity.user
    # Consume only after the cloud token is validated.  A 401 keeps the ticket usable so the
    # current browser tab can finish login and retry; concurrent reuse still has exactly one
    # winner at this atomic pop.
    if not _consume_sync_ticket(sync_ticket):
        return JSONResponse({"detail": "invalid_sync_ticket"}, status_code=403, headers=cors)
    # Web native sync only authorizes this browser account to read the installation-local
    # ledger and memories. Legacy guest-card ownership is unrelated and must not block
    # account switching with ``client_already_bound_to_other_user``.
    try:
        bind = await _store_session(
            base,
            access,
            refresh,
            user,
            auth_source=lookup.identity.auth_source,
            bind_client=False,
        )
    except _ClientBindingConflict as exc:
        return JSONResponse({"detail": exc.detail}, status_code=409, headers=cors)
    except _InvalidIdentityResponse as exc:
        return JSONResponse({"detail": exc.detail}, status_code=502, headers=cors)
    return JSONResponse(
        {
            "ok": True,
            "user": {"display_name": user.get("display_name"), "email": user.get("email")},
            "bind": bind,
        },
        headers=cors,
    )


def _request_bearer_token(request: Request) -> str:
    authorization = (request.headers.get("authorization") or "").strip()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


async def _facts_request_auth_state(request: Request) -> str:
    supplied = _request_bearer_token(request)
    if not supplied:
        return "mismatch"
    return await _request_matches_desktop_session(_social_base_url(), supplied)


async def _build_local_forge_facts(**kwargs):
    """Lazy import keeps the normal main-server import surface lightweight."""
    from main_logic.card_forge_facts import build_forge_facts_payload

    return await build_forge_facts_payload(**kwargs)


@router.options("/facts", summary="社区读取本地 NEKO 记忆候选预检")
async def forge_facts_options(request: Request):
    cors = _facts_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse({"ok": True}, headers=cors)


@router.get("/facts", summary="社区铸造：受控读取当前猫娘的本地记忆候选")
async def forge_facts_endpoint(
    request: Request,
    runtime_character_hint: str | None = Query(default=None, max_length=64),
    min_importance: int = Query(default=0, ge=0, le=10),
    include_absorbed: bool = Query(default=True),
    limit: int = Query(default=5, ge=1, le=10),
    exclude_fact_ids: str | None = Query(default=None, max_length=4096),
    exclude_hashes: str | None = Query(default=None, max_length=4096),
):
    cors = _facts_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    auth_state = await _facts_request_auth_state(request)
    if auth_state == "unavailable":
        return JSONResponse(
            {"detail": "identity_verification_unavailable"},
            status_code=503,
            headers=cors,
        )
    if auth_state != "match":
        return JSONResponse({"detail": "local_session_mismatch"}, status_code=401, headers=cors)
    payload = await _build_local_forge_facts(
        runtime_character_hint=runtime_character_hint,
        min_importance=min_importance,
        include_absorbed=include_absorbed,
        limit=limit,
        exclude_fact_ids=exclude_fact_ids,
        exclude_hashes=exclude_hashes,
    )
    return JSONResponse(payload, headers=cors)


@router.post("/login", summary="已移除：请使用统一账号 OAuth（/api/card-drop/oauth/start）")
async def login_endpoint(request: Request, payload: dict = Body(default=None)):
    raise HTTPException(status_code=410, detail="legacy_community_login_removed")


@router.post("/register", summary="已移除：请使用统一账号 OAuth（/api/card-drop/oauth/start）")
async def register_endpoint(request: Request, payload: dict = Body(default=None)):
    raise HTTPException(status_code=410, detail="legacy_community_login_removed")


@router.post("/logout", summary="登出（清本地 JWT）")
async def logout_endpoint(request: Request, payload: dict | None = Body(default=None)):
    _require_local_mutation_ticket(request, payload)
    if not await asyncio.to_thread(_clear_auth):
        raise HTTPException(status_code=500, detail="local_clear_failed")
    return {"logged_in": False}


def _neko_steam_callback_url(request: Request) -> str:
    """Return the local Steam callback URL using the request origin."""
    return f"{str(request.base_url).rstrip('/')}/api/card-drop/steam-callback"


_STEAM_CALLBACK_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>登录成功</title><style>
html,body{{margin:0;height:100%;background:#0f1020;color:#eef;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
.wrap{{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;text-align:center;padding:24px}}
.ok{{font-size:46px}}.t{{font-size:20px;font-weight:600}}.s{{font-size:14px;color:#9aa;max-width:360px;line-height:1.6}}
</style></head><body><div class="wrap">
<div class="ok">✦</div><div class="t">{title}</div>
<div class="s">{sub}</div></div>
<script>setTimeout(function(){{try{{window.close();}}catch(e){{}}}},1200);</script>
</body></html>"""


def _steam_callback_html(title: str, sub: str, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _STEAM_CALLBACK_PAGE.format(title=html.escape(title), sub=html.escape(sub)),
        status_code=status_code,
    )


@router.get("/steam-login", summary="已移除：请使用统一账号 OAuth（/api/card-drop/oauth/start）")
async def steam_login_endpoint(request: Request):
    raise HTTPException(status_code=410, detail="legacy_community_login_removed")


@router.get(
    "/steam-callback",
    summary="已移除：请使用统一账号 OAuth（/oauth/callback）",
    response_class=HTMLResponse,
)
async def steam_callback_endpoint(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
):
    raise HTTPException(status_code=410, detail="legacy_community_login_removed")


def _credit_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=409, detail=str(exc))


async def _require_credit_browser(request: Request) -> dict[str, str]:
    cors = _credit_cors_headers(request)
    if cors is None:
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    auth_state = await _facts_request_auth_state(request)
    if auth_state == "unavailable":
        raise HTTPException(
            status_code=503,
            detail="identity_verification_unavailable",
            headers=cors,
        )
    if auth_state != "match":
        raise HTTPException(
            status_code=401,
            detail="local_session_mismatch",
            headers=cors,
        )
    return cors


@router.options("/credits")
@router.options("/credits/{credit_id}/reservations")
@router.options("/credits/{credit_id}/reservations/{operation_id}/commit")
@router.options("/credits/{credit_id}/reservations/{operation_id}")
async def credit_options(request: Request, credit_id: str = "", operation_id: str = ""):
    cors = _credit_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse({"ok": True}, headers=cors)


@router.post("/credits/grant", summary="Electron 掉落引擎写入一张本机锻造券")
async def grant_credit_endpoint(request: Request, payload: dict = Body(...)):
    if not _local_mutation_origin_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    from main_logic.forge_credit_ledger import grant_credit

    try:
        return await asyncio.to_thread(grant_credit, payload)
    except (ValueError, LookupError, RuntimeError) as exc:
        raise _credit_error(exc) from exc


@router.get("/credits", summary="读取 N.E.K.O 本机有效锻造券与待恢复预占")
async def credits_endpoint(request: Request):
    cors = await _require_credit_browser(request)
    from main_logic.forge_credit_ledger import list_credits

    return JSONResponse(await asyncio.to_thread(list_credits), headers=cors)


@router.get("/credits/local-summary", summary="本体同源界面读取锻造券数量角标")
async def local_credit_summary_endpoint(request: Request):
    """Expose only a count and expiry hint to the trusted local N.E.K.O UI.

    The community-facing ``/credits`` endpoint remains native-session Bearer protected;
    this summary intentionally omits credit IDs, rarities and reservation operations.
    """
    if not _local_mutation_origin_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    from main_logic.forge_credit_ledger import list_credits

    snapshot = await asyncio.to_thread(list_credits)
    expiries = [
        str(credit.get("expires_at"))
        for credit in snapshot.get("credits", [])
        if credit.get("expires_at")
    ]
    return JSONResponse(
        {
            "count": int(snapshot.get("count") or 0),
            "next_expires_at": min(expiries) if expiries else None,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/credits/{credit_id}/reservations", summary="为一次云端铸造幂等预占本机券")
async def reserve_credit_endpoint(request: Request, credit_id: str, payload: dict = Body(...)):
    cors = await _require_credit_browser(request)
    from main_logic.forge_credit_ledger import reserve_credit

    try:
        result = await asyncio.to_thread(
            reserve_credit,
            credit_id,
            str(payload.get("operation_id") or ""),
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        error = _credit_error(exc)
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=cors)
    return JSONResponse(result, headers=cors)


@router.post("/credits/{credit_id}/reservations/{operation_id}/commit", summary="云端铸卡成功后确认消费本机券")
async def commit_credit_endpoint(
    request: Request, credit_id: str, operation_id: str, payload: dict = Body(...),
):
    cors = await _require_credit_browser(request)
    from main_logic.forge_credit_ledger import commit_credit

    try:
        result = await asyncio.to_thread(
            commit_credit,
            credit_id,
            operation_id,
            str(payload.get("card_id") or ""),
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        error = _credit_error(exc)
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=cors)
    return JSONResponse(result, headers=cors)


@router.delete("/credits/{credit_id}/reservations/{operation_id}", summary="云端明确失败后释放本机券预占")
async def release_credit_endpoint(request: Request, credit_id: str, operation_id: str):
    cors = await _require_credit_browser(request)
    from main_logic.forge_credit_ledger import release_credit

    try:
        result = await asyncio.to_thread(release_credit, credit_id, operation_id)
    except (ValueError, LookupError, RuntimeError) as exc:
        error = _credit_error(exc)
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=cors)
    return JSONResponse(result, headers=cors)
