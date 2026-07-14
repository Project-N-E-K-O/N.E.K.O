"""Community-login + forge-credit proxy routes for NEKO (``/api/card-drop`` prefix).

The old conversation card-drop (local 5-pick overlay + ``/draw`` direct mint) has
been retired: drop decisions and credit grants now live in the private NEKO-PC
forge-dropper, and minting goes through the cloud forge-beta flow (the cloud
``POST /api/cards/draw`` is offline). This router now only proxies the live credit
endpoints (``/credits``) and the community Steam login
(``/steam-login`` / ``/steam-callback`` / ``/auth-status``).

The cloud contract lives in N.E.K.O.Servers ``app/modules/cards/router.py``.
"""

from __future__ import annotations

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


def _get_client_id() -> str | None:
    """Return a persisted ``client_id`` from the local cloudsave state."""
    try:
        from utils.config_manager import get_config_manager

        cm = get_config_manager()
        needs_persist = not cm.cloudsave_local_state_path.exists()
        state = cm.load_cloudsave_local_state()
        cid = state.get("client_id") if isinstance(state, dict) else None
        if not isinstance(cid, str) or not cid:
            state = cm.build_default_cloudsave_local_state()
            cid = state.get("client_id")
            needs_persist = True
        if not isinstance(cid, str) or not cid:
            return None
        if needs_persist:
            cm.save_cloudsave_local_state(state)
        return cid
    except Exception as exc:  # noqa: BLE001
        logger.warning("card_drop: failed to load or persist client_id: %s", exc)
    return None


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
        "local_user_id": normalized_user_id,
        "auth_source": normalized_source,
    }
    if refresh:
        data["refresh_token"] = refresh
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
        snapshot = _desktop_session_snapshot()
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
        if _persist_session_identity_metadata(
            snapshot,
            lookup.identity.local_user_id,
            lookup.identity.auth_source,
        ):
            return lookup

        current = _desktop_session_snapshot()
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
) -> dict:
    """Store JWTs and bind the local client so guest cards migrate to the user.

    Email/password and Steam login share this path. The bind result is persisted
    and exposed through auth-status so client-binding conflicts are visible.
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
    cid = _get_client_id()
    if not cid:
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
    a = _load_auth() or {}
    if _access_token():
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
    try:
        bind = await _store_session(
            base,
            access,
            refresh,
            user,
            auth_source=lookup.identity.auth_source,
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


@router.post("/login", summary="邮箱密码登录社区账号（存 JWT + 迁移游客卡）")
async def login_endpoint(request: Request, payload: dict = Body(...)):
    _require_local_mutation_ticket(request, payload)
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    if not email or not password:
        raise HTTPException(status_code=400, detail="missing_email_or_password")
    base = _social_base_url()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(f"{base}/api/auth/login", json={"email": email, "password": password})
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    user, bind = await _finish_login(base, _relay(r))
    return {"user": user, "bind": bind}


@router.post("/register", summary="邮箱密码注册社区账号（存 JWT + 迁移游客卡）")
async def register_endpoint(request: Request, payload: dict = Body(...)):
    _require_local_mutation_ticket(request, payload)
    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""
    display_name = (payload.get("display_name") or "").strip() or None
    if not email or not password:
        raise HTTPException(status_code=400, detail="missing_email_or_password")
    body = {"email": email, "password": password}
    if display_name:
        body["display_name"] = display_name
    base = _social_base_url()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(f"{base}/api/auth/register", json=body)
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    user, bind = await _finish_login(base, _relay(r))
    return {"user": user, "bind": bind}


@router.post("/logout", summary="登出（清本地 JWT）")
async def logout_endpoint(request: Request, payload: dict | None = Body(default=None)):
    _require_local_mutation_ticket(request, payload)
    if not _clear_auth():
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


@router.get("/steam-login", summary="返回 Steam 登录授权 URL（前端用浏览器打开）")
async def steam_login_endpoint(request: Request):
    if not _local_request_source_allowed(request):
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    base = _social_base_url()
    callback = _neko_steam_callback_url(request)
    pending = _mark_steam_pending()
    if not pending:
        # state 没落盘 → 回调无法本地校验，必失败；不如在入口直接报错，别让用户白跑一趟 Steam。
        raise HTTPException(status_code=503, detail="steam_login_state_unavailable")
    state, code_challenge = pending
    # PKCE：code_challenge 进 authorize URL，配对的 verifier 已落盘 pending，回调换 token 才出示。
    authorize_url = (
        f"{base}/api/auth/oauth/steam/authorize?redirect_to={quote(callback, safe='')}"
        f"&state={quote(state, safe='')}"
        f"&code_challenge={quote(code_challenge, safe='')}"
        f"&code_challenge_method=S256"
    )
    return {"authorize_url": authorize_url, "callback": callback}


@router.get(
    "/steam-callback",
    summary="Steam 登录回调：存 JWT + 迁移游客卡，返回提示页",
    response_class=HTMLResponse,
)
async def steam_callback_endpoint(
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
):
    # 校验 state（与发起登录时一致）+ 一次性窗口，挡 login-CSRF / 重放；同时取回 PKCE verifier。
    ok, code_verifier = _consume_steam_pending(state)
    if not ok:
        return _steam_callback_html("登录会话已失效", "请回到 NEKO 重新点一次「Steam 登录」。")
    base = _social_base_url()
    # 用一次性 code 服务端换 token（token 不经浏览器 URL/历史/日志）。
    # PKCE：带上发起登录时落盘的 code_verifier，云端校验 sha256(verifier)==challenge，把短码
    # 绑定到「发起登录的这台 NEKO」。老 pending（无 verifier）→ 不带，云端也不强制（向后兼容）。
    exchange_body: dict = {"code": code}
    if code_verifier:
        exchange_body["code_verifier"] = code_verifier
    access_token: str | None = None
    refresh_token: str | None = None
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.post(
                f"{base}/api/auth/oauth/native/exchange", json=exchange_body
            )
        if r.status_code == 200:
            d = r.json() or {}
            access_token = d.get("access_token")
            refresh_token = d.get("refresh_token")
        else:
            logger.info("card_drop: steam-callback exchange returned %s", r.status_code)
    except (httpx.HTTPError, OSError, ValueError) as exc:
        logger.info("card_drop: steam-callback exchange failed: %s", exc)
    if not access_token:
        return _steam_callback_html("登录失败", "换取登录凭证失败，请回到 NEKO 重试。")
    identity_lookup = await _lookup_cloud_identity(base, access_token)
    if identity_lookup.identity is None:
        logger.info(
            "card_drop: steam-callback identity verification failed (%s)",
            identity_lookup.failure or "rejected",
        )
        return _steam_callback_html("登录失败", "无法验证社区身份，请回到 NEKO 重试。")
    user = identity_lookup.identity.user
    try:
        bind = await _store_session(
            base,
            access_token,
            refresh_token,
            user,
            auth_source=identity_lookup.identity.auth_source,
        )
    except _ClientBindingConflict:
        return _steam_callback_html(
            "登录冲突",
            "这台设备已经绑定其他社区账号，本次登录未生效；原登录状态保持不变。",
            status_code=409,
        )
    except _InvalidIdentityResponse:
        return _steam_callback_html("登录失败", "社区身份响应无效，请回到 NEKO 重试。")
    name = user.get("display_name") or "你"
    if bind.get("bound"):
        sub = "卡片会存进你的卡册了，可以关掉本页回到 NEKO。"
    else:
        sub = "已登录，但游客卡迁移没完成（稍后可重试）。可关掉本页回到 NEKO。"
    return _steam_callback_html(f"已登录，欢迎 {name}", sub)


@router.get("/credits", summary="代理云端：列当前有效铸造券（供铸造 UI 显示数量/稀有度/到期）")
async def credits_endpoint():
    base, cid = _require_ctx()
    headers = {"X-Client-Id": cid}
    token = _access_token()  # 登录则按 user 账号查；否则按游客 client
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{base}/api/forge/credits"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
            r = await client.get(url, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(status_code=502, detail=f"cloud_unreachable: {exc}") from exc
    return _relay(r)
