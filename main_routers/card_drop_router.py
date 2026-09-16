"""Community login and local forge-memory routes (``/api/card-drop`` prefix).

Drop decisions remain in the private NEKO-PC forge-dropper. Forge credits are
cloud authoritative; this service only exposes local character/memory context.
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
import stat
import tempfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from main_logic import client_registration
from utils import single_instance
from utils.file_utils import (
    atomic_write_json,
    fsync_directory_best_effort,
    publish_without_replacing,
)
from utils.storage.community_private_state import (
    COMMUNITY_AUTH_FILENAME as _AUTH_FILENAME,
    COMMUNITY_OAUTH_PENDING_FILENAME as _OAUTH_PENDING_FILENAME,
    COMMUNITY_STEAM_PENDING_FILENAME as _STEAM_PENDING_FILENAME,
    SOCIAL_SESSION_FILENAME as _SOCIAL_SESSION_FILENAME,
    SOCIAL_LOCK_OWNER_ORPHANED,
    SOCIAL_LOCK_SCHEMA_VERSION,
    backend_can_recover_social_lock_owner,
    classify_social_lock_owner,
    parse_social_lock_owner,
    probe_social_lock_process,
    probe_retained_community_state,
    read_private_json_state,
    retained_community_snapshot_matches,
)
from utils.storage_policy import (
    StoragePolicyError,
    path_chain_has_symlink,
    paths_equal,
    publish_fixed_anchor_state_json,
    read_fixed_anchor_state_json,
)

logger = logging.getLogger("neko.card_drop")

router = APIRouter(prefix="/api/card-drop", tags=["card-drop"])

_HTTP_TIMEOUT_SEC = 60.0
_DEFAULT_SOCIAL_BASE_URL = "https://community.project-neko.cn"
_SOCIAL_SESSION_LOCK_SUFFIX = ".lock"
_SOCIAL_SESSION_LOCK_TIMEOUT_SEC = 2.0
_SOCIAL_SESSION_LOCK_POLL_SEC = 0.02
_SOCIAL_SESSION_SCHEMA_VERSION = 2
_COMMUNITY_LOGOUT_STATE_FILENAME = "community_logout.json"
_PRIVATE_EPOCH_FILENAMES = {
    _AUTH_FILENAME,
    _SOCIAL_SESSION_FILENAME,
    _OAUTH_PENDING_FILENAME,
    _STEAM_PENDING_FILENAME,
}
_SOCIAL_LOCK_SINGLE_INSTANCE_PROOF_ENV = "NEKO_LAUNCHER_SINGLE_INSTANCE_PROVEN"
# Orphan reclamation publishes while holding this mutex; publication-failure
# bookkeeping protects the same recovery state and therefore re-enters it.
_SOCIAL_LOCK_RECOVERY_MUTEX = threading.RLock()
_SOCIAL_LOCK_RECOVERY_GUARD_FILE = "social-session-recovery.lock"
_SOCIAL_LOCK_OWNER_IDENTITY_MUTEX = threading.Lock()
_SOCIAL_LOCK_OWNER_IDENTITY: tuple[str, str, str] | None = None
_SOCIAL_LOCK_ABANDONED_OWNERSHIP: dict[
    tuple[object, ...], tuple[object | None, str]
] = {}
_SOCIAL_SESSION_LOCK_CONTEXT = threading.local()
_WINDOWS_DELETE_ACCESS = 0x00010000
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_DELETE = 0x00000004
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_ATTRIBUTE_NORMAL = 0x00000080
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_DISPOSITION_INFO_CLASS = 4
_WINDOWS_ERROR_SHARING_VIOLATION = 32
_BIND_OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"
_PLATFORM_TOKEN_SYNC_FORBIDDEN = "platform_token_native_sync_forbidden"
_SYNC_TICKET_TTL_SEC = 5 * 60
_SYNC_TICKET_MAX_ACTIVE = 16
_NATIVE_DELEGATE_TTL_SEC = 10 * 60
_NATIVE_DELEGATE_MAX_ACTIVE = 8
_NATIVE_DELEGATE_SCOPES = frozenset(
    {"facts:read"}
)
_FACT_QUERY_MAX_EXCLUSIONS = 200
_FACT_QUERY_MAX_EXCLUSION_LENGTH = 128
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_SUPPORTED_AUTH_SOURCES = frozenset({"legacy", "oauth"})
_native_sync_tickets: dict[str, dict] = {}
# digest -> {expires_at, scopes, local_user_id, audience, session_fingerprint}
_native_delegates: dict[str, dict] = {}
# /sync-ticket 在事件循环线程签发，social-session-init 经 asyncio.to_thread 在
# worker 线程消费（_clear_auth 也可能在线程里清 delegates）；两条线程都会迭代并
# 修改这两张表，整段持锁，否则并发增删会让迭代抛 RuntimeError 把请求变成 500。
_native_sync_tickets_lock = threading.Lock()
_native_delegates_lock = threading.Lock()


class _ClientBindingConflict(Exception):
    """The local client belongs to another cloud user; do not publish new JWTs."""

    detail = _BIND_OWNERSHIP_CONFLICT


class _InvalidIdentityResponse(Exception):
    """The cloud accepted a token but did not return the frozen identity contract."""

    detail = "invalid_identity_response"


class _SocialLockReplacedError(OSError):
    """The lock name moved to another inode during a stable snapshot read."""


class _SocialLockBusyError(OSError):
    """The exact Windows lock object is temporarily unavailable for sharing."""


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


@dataclass(frozen=True)
class _BrowserAuth:
    state: str | None
    local_user_id: str = ""


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
    """Caller must hold ``_native_sync_tickets_lock`` (it is not reentrant)."""
    current = time.monotonic() if now is None else now
    expired = [
        digest
        for digest, entry in _native_sync_tickets.items()
        if entry["expires_at"] <= current
    ]
    for digest in expired:
        _native_sync_tickets.pop(digest, None)


def _issue_sync_ticket(session_fingerprint: str = "") -> str:
    now = time.monotonic()
    with _native_sync_tickets_lock:
        _prune_sync_tickets(now)
        while len(_native_sync_tickets) >= _SYNC_TICKET_MAX_ACTIVE:
            oldest = min(_native_sync_tickets, key=lambda key: _native_sync_tickets[key]["expires_at"])
            _native_sync_tickets.pop(oldest, None)
        ticket = secrets.token_urlsafe(32)
        _native_sync_tickets[_sync_ticket_digest(ticket)] = {
            "expires_at": now + _SYNC_TICKET_TTL_SEC,
            "session_fingerprint": session_fingerprint,
        }
    return ticket


def _sync_ticket_is_valid(value: object, *, session_fingerprint: str | None = None) -> bool:
    ticket = _normalize_sync_ticket(value)
    if not ticket:
        return False
    now = time.monotonic()
    with _native_sync_tickets_lock:
        _prune_sync_tickets(now)
        entry = _native_sync_tickets.get(_sync_ticket_digest(ticket))
        return bool(entry and (session_fingerprint is None or (
            session_fingerprint and entry["session_fingerprint"] == session_fingerprint
        )))


def _consume_sync_ticket(value: object, *, session_fingerprint: str | None = None) -> bool:
    ticket = _normalize_sync_ticket(value)
    if not ticket:
        return False
    now = time.monotonic()
    with _native_sync_tickets_lock:
        _prune_sync_tickets(now)
        digest = _sync_ticket_digest(ticket)
        entry = _native_sync_tickets.get(digest)
        if not entry or (session_fingerprint is not None and (
            not session_fingerprint or entry["session_fingerprint"] != session_fingerprint
        )):
            return False
        _native_sync_tickets.pop(digest)
        return True


def _prune_native_delegates(now: float | None = None) -> None:
    """Caller must hold ``_native_delegates_lock`` (it is not reentrant)."""
    current = time.monotonic() if now is None else now
    expired = [
        digest
        for digest, entry in _native_delegates.items()
        if float(entry.get("expires_at") or 0) <= current
    ]
    for digest in expired:
        _native_delegates.pop(digest, None)


def _clear_native_delegates() -> None:
    with _native_delegates_lock:
        _native_delegates.clear()


def _desktop_session_fingerprint(snapshot: dict | None) -> str:
    """Bind a delegate to the exact persisted desktop login without retaining it."""
    if not isinstance(snapshot, dict):
        return ""
    local_user_id = _normalize_local_user_id(snapshot.get("local_user_id"))
    auth_source = _normalize_auth_source(snapshot.get("auth_source"))
    access_token = str(snapshot.get("access_token") or "").strip()
    base_url = str(snapshot.get("base_url") or "").strip().rstrip("/")
    if not local_user_id or not auth_source or not access_token or not base_url:
        return ""
    serialized = json.dumps(
        {
            "access_token": access_token,
            "auth_source": auth_source,
            "base_url": base_url,
            "local_user_id": local_user_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _issue_native_delegate(
    *,
    local_user_id: str,
    audience: str,
    session_fingerprint: str,
    scopes: frozenset[str] | None = None,
) -> str:
    now = time.monotonic()
    with _native_delegates_lock:
        _prune_native_delegates(now)
        while len(_native_delegates) >= _NATIVE_DELEGATE_MAX_ACTIVE:
            oldest = min(
                _native_delegates,
                key=lambda digest: float(_native_delegates[digest].get("expires_at") or 0),
            )
            _native_delegates.pop(oldest, None)
        ticket = secrets.token_urlsafe(32)
        requested_scopes = _NATIVE_DELEGATE_SCOPES if scopes is None else frozenset(scopes)
        scoped = requested_scopes & _NATIVE_DELEGATE_SCOPES
        _native_delegates[_sync_ticket_digest(ticket)] = {
            "expires_at": now + _NATIVE_DELEGATE_TTL_SEC,
            "scopes": scoped,
            "local_user_id": _normalize_local_user_id(local_user_id),
            "audience": (audience or "").strip().rstrip("/"),
            "session_fingerprint": str(session_fingerprint or ""),
        }
    return ticket


def _native_delegate_entry(value: object) -> dict | None:
    ticket = _normalize_sync_ticket(value)
    if not ticket:
        return None
    now = time.monotonic()
    with _native_delegates_lock:
        _prune_native_delegates(now)
        entry = _native_delegates.get(_sync_ticket_digest(ticket))
        if not entry or float(entry.get("expires_at") or 0) <= now:
            return None
        return dict(entry)


def _discard_native_delegate(value: object) -> None:
    ticket = _normalize_sync_ticket(value)
    if ticket:
        with _native_delegates_lock:
            _native_delegates.pop(_sync_ticket_digest(ticket), None)


def _social_base_url() -> str:
    """Return the cloud base URL, falling back to production."""
    return client_registration.social_base_url()


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
        pa_port = _origin_port(pa)
        pb_port = _origin_port(pb)
        ha = (pa.hostname or "").lower()
        hb = (pb.hostname or "").lower()
    except Exception:  # noqa: BLE001
        return False
    if pa.scheme != pb.scheme or pa_port != pb_port:
        return False
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


def _local_ui_request_source_allowed(request: Request) -> bool:
    """Require browser Fetch Metadata proving a request came from this local UI."""
    if (request.headers.get("sec-fetch-site") or "").strip().lower() != "same-origin":
        return False
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin:
        return True
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
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": (
            "authorization, content-type, x-neko-local-user-id"
        ),
        "Access-Control-Max-Age": "600",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Vary": "Origin",
    }
    if (request.headers.get("access-control-request-private-network") or "").lower() == "true":
        headers["Access-Control-Allow-Private-Network"] = "true"
    return headers


def _capabilities_cors_headers(request: Request) -> dict[str, str] | None:
    """Expose protocol metadata only to the exact configured community origin."""
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin or not _exact_origin_matches(origin, _social_base_url()):
        return None
    headers = {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Max-Age": "600",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
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
_OAUTH_PENDING_TTL_SEC = 600


def _oauth_pending_record_has_shape(data: dict) -> bool:
    if not (
        str(data.get("state") or "").strip()
        and str(data.get("code_verifier") or "").strip()
    ):
        return False
    try:
        return float(data.get("expires_at") or 0) > 0
    except (TypeError, ValueError):
        return False


def _oauth_pending_record_is_fresh(data: dict) -> bool:
    return _oauth_pending_record_has_shape(data) and float(data["expires_at"]) > time.time()


def _steam_pending_record_has_shape(data: dict) -> bool:
    if not str(data.get("state") or "").strip():
        return False
    try:
        return float(data.get("ts") or 0) > 0
    except (TypeError, ValueError):
        return False


def _steam_pending_record_is_fresh(data: dict) -> bool:
    if not _steam_pending_record_has_shape(data):
        return False
    timestamp = float(data["ts"])
    return bool(timestamp) and (time.time() - timestamp) <= _STEAM_PENDING_TTL_SEC


def _community_state_dir(config_manager=None) -> Path | None:
    """Return the fixed-anchor directory for private community state."""
    try:
        if config_manager is None:
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()
        state_dir = Path(config_manager.local_state_dir)
        if path_chain_has_symlink(state_dir):
            logger.warning("card_drop: community state directory is unsafe")
            return None
        return state_dir
    except Exception as exc:  # noqa: BLE001
        logger.debug("card_drop: community state path resolve failed: %s", exc)
        return None


def _community_state_path(filename: str, *, config_manager=None) -> Path | None:
    state_dir = _community_state_dir(config_manager)
    return state_dir / filename if state_dir is not None else None


def _current_logout_epoch(*, config_manager=None) -> int:
    """Read the fixed-anchor logout generation; malformed state fails closed."""

    auth_path = (
        _community_state_path(_AUTH_FILENAME, config_manager=config_manager)
        if config_manager is not None
        else _auth_path()
    )
    path = (
        auth_path.parent / _COMMUNITY_LOGOUT_STATE_FILENAME
        if auth_path is not None
        else None
    )
    if path is None:
        raise OSError("community logout state is unavailable")
    state, payload = _read_private_json_state(path)
    if state == "absent":
        return 0
    if state != "valid" or not isinstance(payload, dict):
        raise OSError("community logout state is unreadable")
    epoch = payload.get("logout_epoch")
    if (
        payload.get("version") != 1
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or epoch < 0
    ):
        raise OSError("community logout state is malformed")
    return epoch


def _private_record_epoch(data: dict) -> int | None:
    epoch = data.get("credential_epoch", 0)
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        return None
    return epoch


def _fsync_logout_epoch_directory_required(path: Path) -> None:
    """Require POSIX durability for the logout tombstone's directory entry."""

    if os.name == "nt":
        # atomic_write_json already attempts the platform's best available
        # directory barrier. Windows does not expose POSIX directory fsync.
        return
    directory_fd = -1
    try:
        directory_fd = os.open(
            os.fspath(path),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        os.fsync(directory_fd)
    finally:
        if directory_fd >= 0:
            with suppress(OSError):
                os.close(directory_fd)


def _advance_logout_epoch(*, config_manager=None) -> int:
    auth_path = (
        _community_state_path(_AUTH_FILENAME, config_manager=config_manager)
        if config_manager is not None
        else _auth_path()
    )
    path = (
        auth_path.parent / _COMMUNITY_LOGOUT_STATE_FILENAME
        if auth_path is not None
        else None
    )
    if path is None:
        raise OSError("community logout state is unavailable")
    next_epoch = _current_logout_epoch(config_manager=config_manager) + 1
    atomic_write_json(
        path,
        {
            "version": 1,
            "logout_epoch": next_epoch,
            "updated_at": int(time.time()),
        },
        ensure_ascii=False,
        indent=2,
    )
    _fsync_logout_epoch_directory_required(path.parent)
    if _current_logout_epoch(config_manager=config_manager) != next_epoch:
        raise OSError("community logout state verification failed")
    return next_epoch


def _legacy_root_candidates(
    config_manager=None,
    *,
    include_cleanup_in_progress: bool = False,
) -> tuple[list[Path], list[Path]]:
    """Return ``(import_roots, conflict_witness_roots)`` for legacy state.

    The completed migration checkpoint is the authority for a retained source;
    its target may prove a conflict but can never supply a missing retained
    record. Arbitrary root_state strings are deliberately not followed.
    """
    try:
        from utils.storage_migration import (
            load_storage_migration,
            retained_source_identity_from_checkpoint,
        )

        if config_manager is None:
            from utils.config_manager import get_config_manager

            config_manager = get_config_manager()
        current_root = Path(config_manager.memory_dir).parent
        committed_root = Path(
            getattr(config_manager, "committed_selected_root", current_root)
        ).expanduser()
        import_roots: list[Path] = []
        witness_roots: list[Path] = []
        try:
            migration = load_storage_migration(config_manager)
        except Exception as exc:  # noqa: BLE001
            logger.warning("card_drop: cannot inspect retained credential source: %s", exc)
            # A corrupt or unreadable checkpoint destroys the authority needed
            # to decide whether ``current_root`` is the old source, committed
            # target, or an anchor fallback. Never guess with credentials.
            return [], []
        if (
            isinstance(migration, dict)
            and str(migration.get("status") or "").strip() == "completed"
            and str(migration.get("target_root") or "").strip()
            and paths_equal(migration["target_root"], committed_root)
        ):
            retained_mode = str(migration.get("retained_source_mode") or "").strip()
            retained_allowed = retained_mode != "cleaned" and (
                include_cleanup_in_progress
                or retained_mode != "cleanup_in_progress"
            )
            retained = str(
                migration.get("retained_source_root")
                or migration.get("backup_root")
                or (
                    ""
                    if migration.get("legacy_private_state_source_root")
                    else migration.get("source_root")
                )
                or ""
            ).strip()
            retained_root: Path | None = None
            retained_path_candidate: Path | None = None
            if retained:
                retained_candidate = Path(retained).expanduser()
                if retained_candidate.is_absolute() and not path_chain_has_symlink(
                    retained_candidate
                ):
                    retained_path_candidate = retained_candidate
                    expected_retained_identity = (
                        retained_source_identity_from_checkpoint(
                            migration,
                            retained_candidate,
                        )
                    )
                    try:
                        retained_metadata = retained_candidate.lstat()
                    except OSError:
                        retained_metadata = None
                    if (
                        retained_metadata is not None
                        and expected_retained_identity is not None
                        and stat.S_ISDIR(retained_metadata.st_mode)
                        and not stat.S_ISLNK(retained_metadata.st_mode)
                        and int(retained_metadata.st_dev)
                        == expected_retained_identity["device"]
                        and int(retained_metadata.st_ino)
                        == expected_retained_identity["inode"]
                    ):
                        retained_root = retained_candidate
                if retained_root is not None and retained_allowed:
                    import_roots.append(retained_root)
                    witness_roots.append(retained_root)

            private_source = str(
                migration.get("legacy_private_state_source_root") or ""
            ).strip()
            private_identity = migration.get(
                "legacy_private_state_source_identity"
            )
            if (
                private_source
                and isinstance(private_identity, list)
                and len(private_identity) == 2
                and all(isinstance(value, int) for value in private_identity)
            ):
                private_root = Path(private_source).expanduser()
                private_matches_retained = bool(
                    retained_path_candidate is not None
                    and paths_equal(private_root, retained_path_candidate)
                )
                if (
                    private_root.is_absolute()
                    and not path_chain_has_symlink(private_root)
                    and not paths_equal(private_root, committed_root)
                    and (retained_allowed or not private_matches_retained)
                ):
                    try:
                        private_metadata = private_root.lstat()
                    except OSError:
                        private_metadata = None
                    private_is_link_like = bool(
                        private_metadata is not None
                        and (
                            stat.S_ISLNK(private_metadata.st_mode)
                            or int(
                                getattr(private_metadata, "st_file_attributes", 0)
                                or 0
                            )
                            & int(
                                getattr(
                                    stat,
                                    "FILE_ATTRIBUTE_REPARSE_POINT",
                                    0,
                                )
                                or 0
                            )
                        )
                    )
                    if (
                        private_metadata is not None
                        and not private_is_link_like
                        and stat.S_ISDIR(private_metadata.st_mode)
                        and (
                            int(private_metadata.st_dev),
                            int(private_metadata.st_ino),
                        )
                        == tuple(private_identity)
                    ):
                        import_roots.append(private_root)
                        witness_roots.append(private_root)

            # This compatibility read only needs file presence. Process
            # identity probing may spawn PowerShell/ps and must not block an
            # async social request merely to classify a lock.
            has_authoritative_private_state = False
            for candidate_root in import_roots:
                candidate_inventory = probe_retained_community_state(
                    candidate_root,
                    classify_social_lock_process=False,
                )
                has_authoritative_private_state = bool(
                    has_authoritative_private_state
                    or candidate_inventory.has_managed_content
                    or (
                        retained_root is not None
                        and paths_equal(candidate_root, retained_root)
                        and include_cleanup_in_progress
                        and retained_mode == "cleanup_in_progress"
                    )
                )
            if has_authoritative_private_state and not bool(
                getattr(
                    config_manager,
                    "recovery_committed_root_unavailable",
                    False,
                )
            ):
                witness_roots.append(committed_root)
        else:
            import_roots.append(current_root)
            witness_roots.append(current_root)

        def _dedupe(roots: list[Path]) -> list[Path]:
            unique: list[Path] = []
            for root in roots:
                if path_chain_has_symlink(root):
                    continue
                if not any(paths_equal(root, existing) for existing in unique):
                    unique.append(root)
            return unique

        return _dedupe(import_roots), _dedupe(witness_roots)
    except Exception as exc:  # noqa: BLE001
        logger.debug("card_drop: legacy community paths unavailable: %s", exc)
        return [], []


def _legacy_selected_roots(
    config_manager=None,
    *,
    include_cleanup_in_progress: bool = False,
) -> list[Path]:
    return _legacy_root_candidates(
        config_manager,
        include_cleanup_in_progress=include_cleanup_in_progress,
    )[0]


def _legacy_conflict_witness_roots(
    config_manager=None,
    *,
    include_cleanup_in_progress: bool = False,
) -> list[Path]:
    return _legacy_root_candidates(
        config_manager,
        include_cleanup_in_progress=include_cleanup_in_progress,
    )[1]


def _legacy_private_file_paths(filename: str) -> list[Path]:
    canonical_dir = _community_state_dir()
    paths: list[Path] = []
    for root in _legacy_selected_roots():
        if canonical_dir is not None and paths_equal(root, canonical_dir):
            continue
        paths.append(root / filename)
    return paths


def _legacy_private_conflict_paths(filename: str) -> list[Path]:
    canonical_dir = _community_state_dir()
    return [
        root / filename
        for root in _legacy_conflict_witness_roots()
        if canonical_dir is None or not paths_equal(root, canonical_dir)
    ]


def _logout_private_file_paths(filename: str) -> list[Path]:
    """Return exact app-owned legacy files that logout must invalidate.

    A cleaned checkpoint never contributes its old source. The active effective
    and committed selected roots are included independently so a target that was
    only a conflict witness cannot retain a live secret after logout.
    """
    # An in-progress retained source is excluded: after a completed physical
    # cleanup and failed final metadata writes, that path may be reused by an
    # unrelated future installation. Current/committed target roots are added
    # below and remain safe exact app-owned logout targets.
    roots = _legacy_conflict_witness_roots()
    try:
        from utils.config_manager import get_config_manager

        config_manager = get_config_manager()
        roots.extend(
            [
                Path(config_manager.memory_dir).parent,
                Path(
                    getattr(
                        config_manager,
                        "committed_selected_root",
                        Path(config_manager.memory_dir).parent,
                    )
                ).expanduser(),
            ]
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("card_drop: logout legacy paths unavailable: %s", exc)

    paths: list[Path] = []
    for root in roots:
        if not root.is_absolute() or path_chain_has_symlink(root):
            continue
        candidate = root / filename
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _logout_storage_ready() -> bool:
    """Prove the committed selected root is reachable before deleting any secret."""
    try:
        from utils.config_manager import get_config_manager

        config_manager = get_config_manager()
        if bool(getattr(config_manager, "recovery_committed_root_unavailable", False)):
            return False
        committed_root = Path(
            getattr(
                config_manager,
                "committed_selected_root",
                Path(config_manager.memory_dir).parent,
            )
        ).expanduser()
        metadata = committed_root.lstat()
        return bool(
            stat.S_ISDIR(metadata.st_mode)
            and not path_chain_has_symlink(committed_root)
            and os.access(committed_root, os.R_OK | os.W_OK | os.X_OK)
        )
    except (AttributeError, OSError):
        return False


def _auth_path() -> Path | None:
    return _community_state_path(_AUTH_FILENAME)


def _legacy_auth_path() -> Path | None:
    paths = _legacy_private_file_paths(_AUTH_FILENAME)
    return paths[0] if paths else None


def _auth_paths() -> list[Path]:
    paths: list[Path] = []
    for candidate in ([_auth_path()] + _legacy_private_file_paths(_AUTH_FILENAME)):
        if candidate is not None and candidate not in paths:
            paths.append(candidate)
    return paths


def _legacy_social_session_path() -> Path | None:
    # Preserve the historical coupling for embedders/tests that supply an auth
    # path directly, while normal runtime resolution continues to use the
    # selected/retained-root inventory below.
    auth_path = _auth_path()
    canonical_auth = _community_state_path(_AUTH_FILENAME)
    if auth_path is not None and auth_path != canonical_auth:
        return auth_path.parent / _SOCIAL_SESSION_FILENAME
    paths = _legacy_private_file_paths(_SOCIAL_SESSION_FILENAME)
    return paths[0] if paths else None


def _social_session_path() -> Path | None:
    """Return the Electron-visible session path when the desktop host supplies it."""
    override = (os.environ.get("NEKO_USER_DATA_DIR") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_absolute():
            return candidate / _SOCIAL_SESSION_FILENAME
        logger.warning("card_drop: ignoring relative NEKO_USER_DATA_DIR")
    return _community_state_path(_SOCIAL_SESSION_FILENAME)


def _social_session_paths() -> list[Path]:
    paths: list[Path] = []
    canonical_state_session = _community_state_path(_SOCIAL_SESSION_FILENAME)
    candidates = [
        _social_session_path(),
        canonical_state_session,
        _legacy_social_session_path(),
        *_legacy_private_file_paths(_SOCIAL_SESSION_FILENAME),
    ]
    for candidate in candidates:
        if candidate is not None and candidate not in paths:
            paths.append(candidate)
    return paths


def _social_lock_fingerprint(raw: bytes) -> str:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, TypeError):
        payload = None
    token = str(payload.get("token") or "").strip() if isinstance(payload, dict) else ""
    if token:
        return f"token:{token}"
    # A digest lets the creating process clean up a malformed partial write.
    # Existing locks are never taken over based on their age.
    return f"digest:{hashlib.sha256(raw).hexdigest()}"


def _backend_social_lock_recovery_authority() -> bool:
    """Require the launcher's positive backend-singleton proof before reaping."""
    return bool(
        os.environ.get(_SOCIAL_LOCK_SINGLE_INSTANCE_PROOF_ENV, "").strip()
    )


def _current_social_lock_record(token: str, *, owner_kind: str = "neko") -> dict:
    global _SOCIAL_LOCK_OWNER_IDENTITY
    with _SOCIAL_LOCK_OWNER_IDENTITY_MUTEX:
        if _SOCIAL_LOCK_OWNER_IDENTITY is None:
            _SOCIAL_LOCK_OWNER_IDENTITY = probe_social_lock_process(os.getpid())
        state, start_token, scheme = _SOCIAL_LOCK_OWNER_IDENTITY
    return {
        "schema_version": SOCIAL_LOCK_SCHEMA_VERSION,
        "token": token,
        "owner_kind": owner_kind,
        "pid": os.getpid(),
        "start_token": start_token if state != "unknown" else "",
        "start_token_scheme": scheme if start_token else "",
        "created_at": int(time.time() * 1000),
    }


def _social_lock_metadata_equal(left, right) -> bool:
    return bool(
        os.path.samestat(left, right)
        and int(left.st_size) == int(right.st_size)
        and int(left.st_mtime_ns) == int(right.st_mtime_ns)
    )


def _open_windows_social_lock_fd(
    lock_path: Path | str,
    *,
    delete_access: bool,
) -> int:
    """Open a Windows lock snapshot without forfeiting exact-handle deletion."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    desired_access = _WINDOWS_GENERIC_READ
    if delete_access:
        desired_access |= _WINDOWS_DELETE_ACCESS
    # Published lock records are immutable. Sharing reads keeps contenders
    # observable and sharing deletion lets the owner retire the exact object;
    # deliberately deny new writers so its verified bytes cannot change before
    # the handle disposition is committed.
    share_mode = _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_DELETE
    handle = create_file(
        os.fspath(lock_path),
        desired_access,
        share_mode,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_ATTRIBUTE_NORMAL | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if handle == invalid_handle:
        error_number = ctypes.get_last_error()
        message = ctypes.FormatError(error_number).strip()
        if error_number in {2, 3}:
            raise FileNotFoundError(error_number, message, os.fspath(lock_path))
        if error_number == 5:
            raise PermissionError(error_number, message, os.fspath(lock_path))
        if error_number == _WINDOWS_ERROR_SHARING_VIOLATION:
            raise _SocialLockBusyError(error_number, message, os.fspath(lock_path))
        raise OSError(error_number, message, os.fspath(lock_path))
    try:
        return msvcrt.open_osfhandle(
            int(handle),
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


def _open_social_lock_fd(
    lock_path: Path | str,
    *,
    dir_fd: int | None = None,
    delete_access: bool = False,
) -> int:
    if os.name == "nt":
        if dir_fd is not None:
            raise NotImplementedError("Windows social lock dir_fd is unavailable")
        return _open_windows_social_lock_fd(
            lock_path,
            delete_access=delete_access,
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    return os.open(lock_path, flags, dir_fd=dir_fd)


def _delete_windows_social_lock_handle(fd: int) -> None:
    """Mark the exact verified Windows handle for deletion on close."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class _FileDispositionInfo(ctypes.Structure):
        # Win32 FILE_DISPOSITION_INFO uses BOOLEAN (one byte), not BOOL.
        _fields_ = [("DeleteFile", wintypes.BOOLEAN)]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_file_information = kernel32.SetFileInformationByHandle
    set_file_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    set_file_information.restype = wintypes.BOOL
    disposition = _FileDispositionInfo(True)
    handle = msvcrt.get_osfhandle(fd)
    if not set_file_information(
        handle,
        _WINDOWS_FILE_DISPOSITION_INFO_CLASS,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error_number = ctypes.get_last_error()
        raise OSError(error_number, ctypes.FormatError(error_number).strip())


def _open_social_lock_snapshot(
    lock_path: Path | str,
    *,
    dir_fd: int | None = None,
    delete_access: bool = False,
) -> tuple[int, object, str, dict | None]:
    """Open a lock without following it and prove the name still names that inode."""
    before = os.stat(lock_path, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise OSError("unsafe social session lock")
    fd = _open_social_lock_fd(
        lock_path,
        dir_fd=dir_fd,
        delete_access=delete_access,
    )
    try:
        opened = os.fstat(fd)
        if not os.path.samestat(before, opened):
            raise _SocialLockReplacedError(
                "social session lock was replaced while opening"
            )
        if not _social_lock_metadata_equal(before, opened):
            raise OSError("social session lock changed while opening")
        chunks: list[bytes] = []
        remaining = 4097
        while remaining:
            chunk = os.read(fd, min(remaining, 4096))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(fd)
        if len(raw) > 4096 or not _social_lock_metadata_equal(opened, after):
            raise OSError("social session lock changed while reading")
        named = os.stat(lock_path, dir_fd=dir_fd, follow_symlinks=False)
        if not os.path.samestat(after, named):
            raise _SocialLockReplacedError(
                "social session lock was replaced while reading"
            )
        if not _social_lock_metadata_equal(after, named):
            raise OSError("social session lock changed while resolving its name")
        return fd, after, _social_lock_fingerprint(raw), parse_social_lock_owner(raw)
    except BaseException:
        os.close(fd)
        raise


def _read_social_lock_snapshot(
    lock_path: Path | str,
    *,
    dir_fd: int | None = None,
) -> tuple[object, str, dict | None]:
    fd, metadata, fingerprint, owner = _open_social_lock_snapshot(
        lock_path,
        dir_fd=dir_fd,
    )
    os.close(fd)
    return metadata, fingerprint, owner


def _unlink_social_lock_if_unchanged(
    lock_path: Path | str,
    expected_metadata,
    expected_fingerprint: str,
    *,
    dir_fd: int | None = None,
) -> bool:
    """Delete only the exact open-and-reverified lock inode/token snapshot."""
    try:
        fd, current_metadata, current_fingerprint, _owner = _open_social_lock_snapshot(
            lock_path,
            dir_fd=dir_fd,
            delete_access=os.name == "nt",
        )
    except FileNotFoundError:
        return False
    try:
        if (
            not _social_lock_metadata_equal(expected_metadata, current_metadata)
            or current_fingerprint != expected_fingerprint
        ):
            return False
        # Keep the verified object open through deletion. POSIX unlink names
        # the still-open inode; Windows must mark that same DELETE-capable
        # handle instead, because path deletion while a normal fd is open is
        # rejected and close-then-unlink would restore a name-replacement race.
        if os.name == "nt":
            _delete_windows_social_lock_handle(fd)
        else:
            os.unlink(lock_path, dir_fd=dir_fd)
        return True
    finally:
        os.close(fd)


def _social_lock_location_key(
    lock_path: Path | str,
    *,
    dir_fd: int | None = None,
) -> tuple[object, ...]:
    """Identify one public lock name without trusting path spelling aliases."""
    name = os.fspath(lock_path)
    if dir_fd is not None:
        parent = os.fstat(dir_fd)
        return "dirfd", int(parent.st_dev), int(parent.st_ino), name
    path = Path(lock_path)
    parent_path = path.parent
    parent = parent_path.stat()
    return (
        "path",
        os.path.normcase(os.path.abspath(os.fspath(parent_path))),
        int(parent.st_dev),
        int(parent.st_ino),
        path.name,
    )


def _forget_abandoned_social_lock(
    lock_path: Path | str,
    *,
    dir_fd: int | None = None,
) -> None:
    try:
        location = _social_lock_location_key(lock_path, dir_fd=dir_fd)
    except OSError:
        return
    with _SOCIAL_LOCK_RECOVERY_MUTEX:
        _SOCIAL_LOCK_ABANDONED_OWNERSHIP.pop(location, None)


def _remember_abandoned_social_lock(
    lock_path: Path | str,
    metadata: object | None,
    fingerprint: str,
    *,
    dir_fd: int | None = None,
) -> None:
    try:
        location = _social_lock_location_key(lock_path, dir_fd=dir_fd)
    except OSError:
        return
    with _SOCIAL_LOCK_RECOVERY_MUTEX:
        _SOCIAL_LOCK_ABANDONED_OWNERSHIP[location] = (metadata, fingerprint)


def _retry_abandoned_social_lock_release(
    lock_path: Path | str,
    current_metadata,
    current_fingerprint: str,
    *,
    dir_fd: int | None = None,
) -> bool:
    """Retry only a lock this process previously failed to release exactly."""
    try:
        location = _social_lock_location_key(lock_path, dir_fd=dir_fd)
    except OSError:
        return False
    with _SOCIAL_LOCK_RECOVERY_MUTEX:
        abandoned = _SOCIAL_LOCK_ABANDONED_OWNERSHIP.get(location)
        if abandoned is None:
            return False
        abandoned_metadata, abandoned_fingerprint = abandoned
        if (
            abandoned_fingerprint != current_fingerprint
            or (
                abandoned_metadata is not None
                and not _social_lock_metadata_equal(
                    abandoned_metadata,
                    current_metadata,
                )
            )
        ):
            _SOCIAL_LOCK_ABANDONED_OWNERSHIP.pop(location, None)
            return False
        try:
            _unlink_social_lock_if_unchanged(
                lock_path,
                abandoned_metadata or current_metadata,
                abandoned_fingerprint,
                dir_fd=dir_fd,
            )
        except OSError:
            # The exact object is still ours but temporarily cannot be retired.
            return True
        _SOCIAL_LOCK_ABANDONED_OWNERSHIP.pop(location, None)
        # Whether it was removed or replaced during the second snapshot, retry
        # acquisition from the public name instead of using an older observation.
        return True


def _release_owned_social_lock(
    lock_path: Path | str,
    owned_metadata,
    owned_fingerprint: str,
    *,
    dir_fd: int | None = None,
) -> None:
    try:
        _unlink_social_lock_if_unchanged(
            lock_path,
            owned_metadata,
            owned_fingerprint,
            dir_fd=dir_fd,
        )
    except OSError:
        _remember_abandoned_social_lock(
            lock_path,
            owned_metadata,
            owned_fingerprint,
            dir_fd=dir_fd,
        )
        return
    # False means the public name changed before deletion, so the remembered
    # exact object is no longer the lock that can block this location either.
    _forget_abandoned_social_lock(lock_path, dir_fd=dir_fd)


def _legacy_social_path_ready(path: Path) -> bool:
    """Permit only a backend-owned path whose complete lock is provably orphaned."""
    lock_path = Path(f"{path}{_SOCIAL_SESSION_LOCK_SUFFIX}")
    try:
        _metadata, _fingerprint, owner = _read_social_lock_snapshot(lock_path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return bool(
        _backend_social_lock_recovery_authority()
        and backend_can_recover_social_lock_owner(owner)
        and classify_social_lock_owner(owner) == SOCIAL_LOCK_OWNER_ORPHANED
    )


def _read_private_json_state(path: Path) -> tuple[str, dict | None]:
    """Distinguish absence from corrupt/unreadable credential authority."""
    return read_private_json_state(path)


def _read_private_json_state_at(dir_fd: int, filename: str) -> tuple[str, dict | None]:
    """Read one retained-root file through an already verified directory handle."""
    return read_private_json_state(filename, dir_fd=dir_fd)


def _read_fixed_anchor_private_json_state(path: Path) -> tuple[str, dict | None]:
    """Read canonical private state through the fixed anchor directory chain."""

    if path.parent.name != "state":
        return _read_private_json_state(path)
    try:
        payload = read_fixed_anchor_state_json(path.parent.parent, path.name)
    except FileNotFoundError:
        return "absent", None
    except StoragePolicyError:
        return "unreadable", None
    except (UnicodeError, ValueError, TypeError):
        return "invalid", None
    if not isinstance(payload, dict):
        return "invalid", None
    return "valid", payload


def _write_private_json_no_replace(path: Path, data: dict) -> bool:
    """Atomically publish a private JSON file without replacing a race winner."""
    if path.parent.name != "state":
        path.parent.mkdir(parents=True, exist_ok=True)
        if path_chain_has_symlink(path.parent):
            raise OSError("unsafe private state directory")
        fd, raw_tmp = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        tmp = Path(raw_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                tmp.chmod(0o600)
            except OSError:
                pass
            try:
                publish_without_replacing(tmp, path)
            except FileExistsError:
                return False
            fsync_directory_best_effort(path.parent)
            return True
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        return publish_fixed_anchor_state_json(
            path.parent.parent,
            path.name,
            data,
            ensure_ascii=False,
            indent=2,
        )
    except StoragePolicyError as exc:
        raise OSError("unsafe private state directory") from exc


def _load_or_migrate_private_json(
    canonical_path: Path | None,
    legacy_paths: list[Path],
    *,
    validator,
    conflict_paths: list[Path] | None = None,
) -> dict | None:
    """Load canonical state or copy one valid legacy record exactly once.

    Existing canonical data, including malformed/unreadable data, is never
    replaced or bypassed with a stale legacy token.  A destination write failure
    leaves the legacy file intact and usable for this read, so migration is
    retried without logging the user out or destroying evidence.
    """
    if canonical_path is None:
        return None
    try:
        logout_epoch = _current_logout_epoch()
    except OSError as exc:
        logger.warning("card_drop: logout generation is unavailable: %s", exc)
        return None

    def _usable(data: dict) -> bool:
        return bool(
            validator(data)
            and _private_record_epoch(data) == logout_epoch
        )

    canonical_state, canonical_data = _read_fixed_anchor_private_json_state(
        canonical_path
    )
    if canonical_state != "absent":
        if canonical_state == "valid" and _usable(canonical_data or {}):
            return canonical_data
        logger.warning("card_drop: canonical %s is not usable", canonical_path.name)
        return None

    inspected_candidates: dict[Path, dict] = {}
    unusable_candidate = False
    paths_to_inspect = conflict_paths if conflict_paths is not None else legacy_paths
    for legacy_path in paths_to_inspect:
        if legacy_path == canonical_path:
            continue
        legacy_state, legacy_data = _read_private_json_state(legacy_path)
        if legacy_state == "absent":
            continue
        if legacy_state != "valid" or not _usable(legacy_data or {}):
            logger.warning("card_drop: legacy %s is not usable", legacy_path.name)
            unusable_candidate = True
            continue
        inspected_candidates[legacy_path] = legacy_data or {}

    if unusable_candidate:
        return None
    witness_payloads = list(inspected_candidates.values())
    if witness_payloads and any(
        candidate_data != witness_payloads[0]
        for candidate_data in witness_payloads[1:]
    ):
        logger.warning("card_drop: conflicting legacy %s records", canonical_path.name)
        return None
    usable_candidates = [
        (legacy_path, inspected_candidates[legacy_path])
        for legacy_path in legacy_paths
        if legacy_path in inspected_candidates
    ]
    if not usable_candidates:
        return None
    legacy_path, legacy_data = usable_candidates[0]

    try:
        published = _write_private_json_no_replace(canonical_path, legacy_data)
    except OSError as exc:
        logger.warning("card_drop: private state migration failed for %s: %s", canonical_path.name, exc)
        return legacy_data
    if not published:
        winner_state, winner_data = _read_fixed_anchor_private_json_state(
            canonical_path
        )
        return (
            winner_data
            if winner_state == "valid" and _usable(winner_data or {})
            else None
        )

    winner_state, winner_data = _read_fixed_anchor_private_json_state(
        canonical_path
    )
    if winner_state != "valid" or not _usable(winner_data or {}):
        return None
    # Delete only the exact record copied. A concurrent refresh must remain.
    latest_state, latest_data = _read_private_json_state(legacy_path)
    if latest_state == "valid" and latest_data == legacy_data:
        try:
            legacy_path.unlink()
            fsync_directory_best_effort(legacy_path.parent)
        except OSError as exc:
            logger.warning("card_drop: legacy %s cleanup failed: %s", legacy_path.name, exc)
    return winner_data


def _legacy_credential_sources_conflict(
    config_manager=None,
    *,
    include_cleanup_in_progress: bool = False,
    retained_root: Path | None = None,
    retained_dir_fd: int | None = None,
) -> bool:
    """Return True unless multiple credential roots prove the same identity."""
    source_records: list[tuple[str, set[str]]] = []
    sources: list[tuple[tuple[Path, str], ...]] = []
    state_dir = _community_state_dir(config_manager)
    if state_dir is not None:
        sources.append(
            (
                (state_dir / _AUTH_FILENAME, "access_token"),
                (state_dir / _SOCIAL_SESSION_FILENAME, "token"),
            )
        )
    social_override = (os.environ.get("NEKO_USER_DATA_DIR") or "").strip()
    if social_override and Path(social_override).expanduser().is_absolute():
        sources.append(
            ((Path(social_override).expanduser() / _SOCIAL_SESSION_FILENAME, "token"),)
        )
    for root in _legacy_conflict_witness_roots(
        config_manager,
        include_cleanup_in_progress=include_cleanup_in_progress,
    ):
        sources.append(
            (
                (root / _AUTH_FILENAME, "access_token"),
                (root / _SOCIAL_SESSION_FILENAME, "token"),
            )
        )

    for source in sources:
        users: set[str] = set()
        tokens: set[str] = set()
        record_users: list[str] = []
        has_record = False
        for path, token_field in source:
            if (
                retained_dir_fd is not None
                and retained_root is not None
                and paths_equal(path.parent, retained_root)
            ):
                state, data = _read_private_json_state_at(retained_dir_fd, path.name)
            else:
                state, data = _read_private_json_state(path)
            if state == "absent":
                continue
            if state != "valid":
                return True
            token = str((data or {}).get(token_field) or "").strip()
            if not token:
                return True
            has_record = True
            tokens.add(token)
            user_id = _normalize_local_user_id((data or {}).get("local_user_id"))
            record_users.append(user_id)
            if user_id:
                users.add(user_id)
        if has_record:
            identity = next(iter(users)) if len(users) == 1 and all(record_users) else ""
            if len(users) > 1 or (len(tokens) > 1 and not identity):
                return True
            source_records.append((identity, tokens))

    for index, (left_identity, left_tokens) in enumerate(source_records):
        for right_identity, right_tokens in source_records[index + 1 :]:
            if left_identity and right_identity:
                if left_identity != right_identity:
                    return True
                # Electron rotates social_session.json independently from the
                # anchored compatibility mirror.  Matching valid UUIDs prove
                # the account even when the bearer generations differ.
                continue
            if not left_tokens or not right_tokens or left_tokens.isdisjoint(right_tokens):
                return True
    return False


def _private_state_records_equivalent(
    filename: str,
    left: dict | None,
    right: dict | None,
) -> bool:
    """Compare private records without mistaking token rotation for an account change."""
    if left == right:
        return True
    if filename not in {_AUTH_FILENAME, _SOCIAL_SESSION_FILENAME}:
        return False
    left_user = _normalize_local_user_id((left or {}).get("local_user_id"))
    right_user = _normalize_local_user_id((right or {}).get("local_user_id"))
    return bool(left_user and right_user and left_user == right_user)


def _existing_safe_parent(path: Path) -> bool:
    try:
        return path.parent.is_dir() and not path_chain_has_symlink(path.parent)
    except OSError:
        return False


def prepare_retained_community_state_cleanup(
    retained_root: Path | str,
    *,
    config_manager=None,
    expected_snapshot: dict[str, str] | None = None,
    retained_dir_fd: int | None = None,
) -> None:
    """Lock the social mirror for the full retained-state cleanup transaction."""
    root = Path(retained_root).expanduser()
    if not root.is_absolute() or path_chain_has_symlink(root):
        raise OSError("unsafe retained community state root")
    social_override = (os.environ.get("NEKO_USER_DATA_DIR") or "").strip()
    if social_override and Path(social_override).expanduser().is_absolute():
        canonical_social = Path(social_override).expanduser() / _SOCIAL_SESSION_FILENAME
    else:
        canonical_social = _community_state_path(
            _SOCIAL_SESSION_FILENAME,
            config_manager=config_manager,
        )
    legacy_social = root / _SOCIAL_SESSION_FILENAME
    lock_paths = [canonical_social] if canonical_social is not None else []
    for legacy_root in _legacy_conflict_witness_roots(
        config_manager,
        include_cleanup_in_progress=True,
    ):
        candidate = legacy_root / _SOCIAL_SESSION_FILENAME
        if retained_dir_fd is not None and paths_equal(legacy_root, root):
            continue
        if _existing_safe_parent(candidate):
            lock_paths.append(candidate)
    if (
        retained_dir_fd is None
        and _existing_safe_parent(legacy_social)
        and legacy_social not in lock_paths
    ):
        lock_paths.append(legacy_social)
    with _social_session_locks(lock_paths, retained_dir_fd=retained_dir_fd):
        if expected_snapshot is not None and not retained_community_snapshot_matches(
            root,
            expected_snapshot,
            dir_fd=retained_dir_fd,
        ):
            raise OSError("retained community state changed after cleanup intent")
        _prepare_retained_community_state_cleanup_locked(
            root,
            config_manager=config_manager,
            retained_dir_fd=retained_dir_fd,
        )


def _prepare_retained_community_state_cleanup_locked(
    retained_root: Path | str,
    *,
    config_manager=None,
    retained_dir_fd: int | None = None,
) -> None:
    """Make recognized legacy private files safe to remove with an old root.

    This is intentionally separate from the runtime-entry inventory: credentials
    belong to fixed local state, while PKCE records are local ephemeral state.
    A cleanup request may remove an old copy only after an identical usable
    canonical record exists. No lock file is migrated.
    """
    root = Path(retained_root).expanduser()
    if not root.is_absolute() or path_chain_has_symlink(root):
        raise OSError("unsafe retained community state root")

    state_auth_path = _community_state_path(_AUTH_FILENAME, config_manager=config_manager)
    social_override = (os.environ.get("NEKO_USER_DATA_DIR") or "").strip()
    if social_override and Path(social_override).expanduser().is_absolute():
        state_social_path = Path(social_override).expanduser() / _SOCIAL_SESSION_FILENAME
    else:
        state_social_path = _community_state_path(
            _SOCIAL_SESSION_FILENAME,
            config_manager=config_manager,
        )
    specs = (
        (
            _AUTH_FILENAME,
            state_auth_path,
            lambda data: bool(str(data.get("access_token") or "").strip()),
            False,
        ),
        (
            _SOCIAL_SESSION_FILENAME,
            state_social_path,
            lambda data: bool(str(data.get("token") or "").strip()),
            False,
        ),
        (
            _OAUTH_PENDING_FILENAME,
            _community_state_path(_OAUTH_PENDING_FILENAME, config_manager=config_manager),
            _oauth_pending_record_is_fresh,
            True,
        ),
        (
            _STEAM_PENDING_FILENAME,
            _community_state_path(_STEAM_PENDING_FILENAME, config_manager=config_manager),
            _steam_pending_record_is_fresh,
            True,
        ),
    )
    if _legacy_credential_sources_conflict(
        config_manager,
        include_cleanup_in_progress=True,
        retained_root=root,
        retained_dir_fd=retained_dir_fd,
    ):
        raise OSError("conflicting legacy credential roots")
    legacy_roots = _legacy_conflict_witness_roots(
        config_manager,
        include_cleanup_in_progress=True,
    )

    def _read_retained(filename: str) -> tuple[str, dict | None]:
        if retained_dir_fd is not None:
            return _read_private_json_state_at(retained_dir_fd, filename)
        return _read_private_json_state(root / filename)

    # Preflight every candidate before publishing the first canonical record.
    # A second valid-but-different target/source file is recovery evidence, not
    # permission to guess which account or PKCE flow should win.
    for filename, _canonical_path, _validator, _ephemeral in specs:
        retained_state, retained_data = _read_retained(filename)
        if retained_state == "absent":
            continue
        for candidate_root in legacy_roots:
            candidate = candidate_root / filename
            if paths_equal(candidate_root, root):
                continue
            candidate_state, candidate_data = _read_private_json_state(candidate)
            if candidate_state == "absent":
                continue
            if (
                candidate_state != "valid"
                or retained_state != "valid"
                or not _private_state_records_equivalent(
                    filename,
                    candidate_data,
                    retained_data,
                )
            ):
                raise OSError(f"conflicting legacy {filename} records")
    verified: list[tuple[Path, dict, Path | None, object | None, dict | None]] = []
    for filename, canonical_path, validator, ephemeral in specs:
        legacy_path = root / filename
        legacy_state, legacy_data = _read_retained(filename)
        if legacy_state == "absent":
            continue
        if legacy_state != "valid":
            if ephemeral:
                # Pending filenames are owned, one-shot local state. Corrupt
                # records cannot authenticate a callback and are safe to discard
                # during an explicit old-root cleanup; never promote them.
                verified.append((legacy_path, {}, None, None, None))
                continue
            raise OSError(f"legacy {filename} is unreadable or malformed")
        if not validator(legacy_data or {}):
            well_formed_expired = (
                filename == _OAUTH_PENDING_FILENAME
                and _oauth_pending_record_has_shape(legacy_data or {})
            ) or (
                filename == _STEAM_PENDING_FILENAME
                and _steam_pending_record_has_shape(legacy_data or {})
            )
            if ephemeral and well_formed_expired:
                # Expired well-formed PKCE state has no recovery value and must
                # not be promoted into fixed state during explicit old-root cleanup.
                verified.append((legacy_path, legacy_data or {}, None, None, None))
                continue
            raise OSError(f"legacy {filename} is unreadable or malformed")
        if canonical_path is None or canonical_path == legacy_path:
            raise OSError(f"canonical {filename} is unavailable")

        canonical_state, canonical_data = _read_fixed_anchor_private_json_state(
            canonical_path
        )
        if canonical_state == "absent":
            if not _write_private_json_no_replace(canonical_path, legacy_data or {}):
                canonical_state, canonical_data = _read_fixed_anchor_private_json_state(
                    canonical_path
                )
            else:
                canonical_state, canonical_data = _read_fixed_anchor_private_json_state(
                    canonical_path
                )
        if (
            canonical_state != "valid"
            or not validator(canonical_data or {})
            or not _private_state_records_equivalent(
                filename,
                canonical_data,
                legacy_data,
            )
        ):
            raise OSError(f"canonical {filename} is unreadable or malformed")
        verified.append(
            (
                legacy_path,
                legacy_data or {},
                canonical_path,
                validator,
                dict(canonical_data or {}),
            )
        )

    # Validate every source and destination again before deleting the first old
    # record. This prevents a concurrent refresh from turning a safe cleanup into
    # deletion of the newest/only credentials.
    for legacy_path, expected_legacy, canonical_path, validator, expected_canonical in verified:
        if canonical_path is None and validator is None and not expected_legacy:
            try:
                metadata = legacy_path.lstat()
            except OSError as exc:
                raise OSError(f"legacy {legacy_path.name} changed during cleanup") from exc
            if stat.S_ISLNK(metadata.st_mode) or path_chain_has_symlink(legacy_path):
                raise OSError(f"legacy {legacy_path.name} changed during cleanup")
            continue
        legacy_state, legacy_data = _read_retained(legacy_path.name)
        if legacy_state != "valid" or legacy_data != expected_legacy:
            raise OSError(f"legacy {legacy_path.name} changed during cleanup")
        if canonical_path is None or validator is None:
            continue
        canonical_state, canonical_data = _read_fixed_anchor_private_json_state(
            canonical_path
        )
        if (
            canonical_state != "valid"
            or not validator(canonical_data or {})
            or canonical_data != expected_canonical
        ):
            raise OSError(f"canonical {canonical_path.name} changed during cleanup")

    for legacy_path, _expected_legacy, _canonical_path, _validator, _expected_canonical in verified:
        if retained_dir_fd is None:
            legacy_path.unlink()
            fsync_directory_best_effort(legacy_path.parent)
        else:
            os.unlink(legacy_path.name, dir_fd=retained_dir_fd)
            os.fsync(retained_dir_fd)


def _read_json_dict(path: Path | None) -> dict | None:
    if not path:
        return None
    state, data = read_private_json_state(path)
    return data if state == "valid" else None


def _load_auth() -> dict | None:
    canonical = _auth_path()
    canonical_state = (
        _read_private_json_state(canonical)[0]
        if canonical is not None
        else "unavailable"
    )
    if canonical_state == "absent" and _legacy_credential_sources_conflict():
        logger.warning("card_drop: conflicting legacy credential roots")
        return None
    return _load_or_migrate_private_json(
        canonical,
        _legacy_private_file_paths(_AUTH_FILENAME),
        validator=lambda data: bool(str(data.get("access_token") or "").strip()),
        conflict_paths=_legacy_private_conflict_paths(_AUTH_FILENAME),
    )


def _load_social_session() -> dict | None:
    """Load the authoritative Electron session, with the legacy path as fallback."""
    canonical = _social_session_path()
    legacy_paths = [
        path
        for path in _social_session_paths()
        if path != canonical
    ]
    conflict_paths = _legacy_private_conflict_paths(_SOCIAL_SESSION_FILENAME)
    conflict_paths = list(dict.fromkeys([*legacy_paths, *conflict_paths]))
    lock_paths = [canonical] if canonical is not None else []
    lock_paths.extend(path for path in conflict_paths if _existing_safe_parent(path))

    def _load_while_locked() -> dict | None:
        canonical_state = (
            _read_private_json_state(canonical)[0]
            if canonical is not None
            else "unavailable"
        )
        if canonical_state == "absent" and _legacy_credential_sources_conflict():
            logger.warning("card_drop: conflicting legacy credential roots")
            return None
        return _load_or_migrate_private_json(
            canonical,
            legacy_paths,
            validator=lambda data: bool(str(data.get("token") or "").strip()),
            conflict_paths=conflict_paths,
        )

    if int(getattr(_SOCIAL_SESSION_LOCK_CONTEXT, "depth", 0) or 0) > 0:
        return _load_while_locked()
    with _social_session_locks(lock_paths):
        return _load_while_locked()


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
    # Migrate the mirror first so a legacy social record can be checked against
    # the same account instead of combining credentials from different roots.
    auth = _load_auth()
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
            "auth_public_url": str(social.get("auth_public_url") or "").strip().rstrip("/"),
            "client_id": str(social.get("client_id") or "").strip(),
        }

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
        "auth_public_url": str(auth.get("auth_public_url") or "").strip().rstrip("/"),
        "client_id": str(auth.get("client_id") or "").strip(),
    }


def _write_private_json(path: Path, data: dict) -> None:
    """Atomically persist local credentials with owner-only permissions where supported."""
    payload = dict(data)
    if path.name in _PRIVATE_EPOCH_FILENAMES:
        payload["credential_epoch"] = _current_logout_epoch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
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


def _try_publish_social_lock(
    lock_path: Path | str,
    token: str,
    *,
    dir_fd: int | None = None,
) -> tuple[object, str] | None:
    """Publish a complete lock record atomically, or return None if busy."""
    encoded = json.dumps(
        _current_social_lock_record(token),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    display_name = Path(lock_path).name
    tmp_name = f".{display_name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    tmp_path = Path(lock_path).with_name(tmp_name) if dir_fd is None else tmp_name
    fd = -1
    published = False
    try:
        fd = os.open(
            tmp_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=dir_fd,
        )
        offset = 0
        while offset < len(encoded):
            written = os.write(fd, encoded[offset:])
            if written <= 0:
                raise OSError("short social session lock write")
            offset += written
        os.fsync(fd)
        fchmod = getattr(os, "fchmod", None)
        if callable(fchmod):
            with suppress(OSError):
                fchmod(fd, 0o600)
        os.close(fd)
        fd = -1
        try:
            if dir_fd is None:
                publish_without_replacing(tmp_path, Path(lock_path))
            else:
                os.link(
                    tmp_name,
                    lock_path,
                    src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd,
                    follow_symlinks=False,
                )
            published = True
        except FileExistsError:
            return None
        metadata, fingerprint, _owner = _read_social_lock_snapshot(
            lock_path,
            dir_fd=dir_fd,
        )
        if fingerprint != f"token:{token}":
            raise OSError("published social session lock changed unexpectedly")
        return metadata, fingerprint
    except BaseException:
        if published:
            retired_or_replaced = False
            try:
                metadata, fingerprint, _owner = _read_social_lock_snapshot(
                    lock_path,
                    dir_fd=dir_fd,
                )
                if fingerprint == f"token:{token}":
                    retired_or_replaced = _unlink_social_lock_if_unchanged(
                        lock_path,
                        metadata,
                        fingerprint,
                        dir_fd=dir_fd,
                    )
                else:
                    retired_or_replaced = True
            except OSError:
                pass
            if not retired_or_replaced:
                # Publication succeeded, but the exact object could not be
                # reopened to capture metadata or retire it. The random token
                # remains enough to recognize only this candidate on a later
                # stable snapshot at the same physical lock location.
                _remember_abandoned_social_lock(
                    lock_path,
                    None,
                    f"token:{token}",
                    dir_fd=dir_fd,
                )
        raise
    finally:
        if fd >= 0:
            with suppress(OSError):
                os.close(fd)
        with suppress(OSError):
            os.unlink(tmp_path, dir_fd=dir_fd)
        if published:
            if dir_fd is None:
                fsync_directory_best_effort(Path(lock_path).parent)
            else:
                with suppress(OSError):
                    os.fsync(dir_fd)


def _reclaim_orphaned_social_lock(
    lock_path: Path | str,
    expected_metadata,
    expected_fingerprint: str,
    expected_owner: dict | None,
    replacement_token: str,
    *,
    dir_fd: int | None = None,
) -> tuple[object, str] | None:
    """Reclaim only under the launcher's unique authority proof."""
    if (
        not backend_can_recover_social_lock_owner(expected_owner)
        or classify_social_lock_owner(expected_owner) != SOCIAL_LOCK_OWNER_ORPHANED
    ):
        return None
    with _SOCIAL_LOCK_RECOVERY_MUTEX:
        try:
            authority = single_instance.try_acquire_auxiliary_lock(
                _SOCIAL_LOCK_RECOVERY_GUARD_FILE
            )
        except (OSError, ValueError):
            return None
        if authority is None:
            return None
        with authority:
            try:
                current_metadata, current_fingerprint, current_owner = _read_social_lock_snapshot(
                    lock_path,
                    dir_fd=dir_fd,
                )
            except FileNotFoundError:
                return _try_publish_social_lock(
                    lock_path,
                    replacement_token,
                    dir_fd=dir_fd,
                )
            if (
                not _social_lock_metadata_equal(expected_metadata, current_metadata)
                or expected_fingerprint != current_fingerprint
                or not backend_can_recover_social_lock_owner(current_owner)
                or classify_social_lock_owner(current_owner) != SOCIAL_LOCK_OWNER_ORPHANED
            ):
                return None
            if not _unlink_social_lock_if_unchanged(
                lock_path,
                current_metadata,
                current_fingerprint,
                dir_fd=dir_fd,
            ):
                return None
            # Hold the kernel recovery guard until this authority either
            # publishes its complete replacement or observes another writer.
            return _try_publish_social_lock(
                lock_path,
                replacement_token,
                dir_fd=dir_fd,
            )


@contextmanager
def _social_session_lock(path: Path):
    """Serialize social-session CAS writes with the Electron main process."""
    lock_path = Path(f"{path}{_SOCIAL_SESSION_LOCK_SUFFIX}")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if path_chain_has_symlink(lock_path.parent):
        raise OSError("unsafe social session lock directory")
    token = f"{os.getpid()}:{secrets.token_hex(16)}"
    token_fingerprint = f"token:{token}"
    owned_metadata = None
    deadline = time.monotonic() + _SOCIAL_SESSION_LOCK_TIMEOUT_SEC
    while True:
        try:
            published = _try_publish_social_lock(lock_path, token)
        except _SocialLockBusyError:
            if time.monotonic() >= deadline:
                raise TimeoutError("social session lock is busy")
            time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
            continue
        if published is None:
            try:
                lock_metadata, lock_fingerprint, lock_owner = _read_social_lock_snapshot(lock_path)
                if lock_fingerprint == token_fingerprint:
                    owned_metadata = lock_metadata
                    _forget_abandoned_social_lock(lock_path)
                    break
                if _retry_abandoned_social_lock_release(
                    lock_path,
                    lock_metadata,
                    lock_fingerprint,
                ):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("social session lock is busy")
                    time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
                    continue
                recovered = None
                if _backend_social_lock_recovery_authority():
                    recovered = _reclaim_orphaned_social_lock(
                        lock_path,
                        lock_metadata,
                        lock_fingerprint,
                        lock_owner,
                        token,
                    )
            except FileNotFoundError:
                _forget_abandoned_social_lock(lock_path)
                if time.monotonic() >= deadline:
                    raise TimeoutError("social session lock is busy")
                continue
            except (_SocialLockReplacedError, _SocialLockBusyError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("social session lock is busy")
                time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
                continue
            if recovered is not None:
                owned_metadata, token_fingerprint = recovered
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("social session lock is busy")
            time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
            continue
        owned_metadata, token_fingerprint = published
        _forget_abandoned_social_lock(lock_path)
        break

    try:
        yield
    finally:
        if owned_metadata is not None:
            _release_owned_social_lock(
                lock_path,
                owned_metadata,
                token_fingerprint,
            )


@contextmanager
def _social_session_lock_at(dir_fd: int):
    """Acquire the legacy social lock relative to a pinned retained-root handle."""
    lock_name = f"{_SOCIAL_SESSION_FILENAME}{_SOCIAL_SESSION_LOCK_SUFFIX}"
    token = f"{os.getpid()}:{secrets.token_hex(16)}"
    token_fingerprint = f"token:{token}"
    owned_metadata = None
    deadline = time.monotonic() + _SOCIAL_SESSION_LOCK_TIMEOUT_SEC
    while True:
        try:
            published = _try_publish_social_lock(lock_name, token, dir_fd=dir_fd)
        except _SocialLockBusyError:
            if time.monotonic() >= deadline:
                raise TimeoutError("social session lock is busy")
            time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
            continue
        if published is None:
            try:
                lock_metadata, lock_fingerprint, lock_owner = _read_social_lock_snapshot(
                    lock_name,
                    dir_fd=dir_fd,
                )
                if lock_fingerprint == token_fingerprint:
                    owned_metadata = lock_metadata
                    _forget_abandoned_social_lock(lock_name, dir_fd=dir_fd)
                    break
                if _retry_abandoned_social_lock_release(
                    lock_name,
                    lock_metadata,
                    lock_fingerprint,
                    dir_fd=dir_fd,
                ):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("social session lock is busy")
                    time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
                    continue
                recovered = None
                if _backend_social_lock_recovery_authority():
                    recovered = _reclaim_orphaned_social_lock(
                        lock_name,
                        lock_metadata,
                        lock_fingerprint,
                        lock_owner,
                        token,
                        dir_fd=dir_fd,
                    )
            except FileNotFoundError:
                _forget_abandoned_social_lock(lock_name, dir_fd=dir_fd)
                if time.monotonic() >= deadline:
                    raise TimeoutError("social session lock is busy")
                continue
            except (_SocialLockReplacedError, _SocialLockBusyError):
                if time.monotonic() >= deadline:
                    raise TimeoutError("social session lock is busy")
                time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
                continue
            if recovered is not None:
                owned_metadata, token_fingerprint = recovered
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("social session lock is busy")
            time.sleep(_SOCIAL_SESSION_LOCK_POLL_SEC)
            continue
        owned_metadata, token_fingerprint = published
        _forget_abandoned_social_lock(lock_name, dir_fd=dir_fd)
        break

    try:
        yield
    finally:
        if owned_metadata is not None:
            _release_owned_social_lock(
                lock_name,
                owned_metadata,
                token_fingerprint,
                dir_fd=dir_fd,
            )


@contextmanager
def _social_session_locks(paths: list[Path], *, retained_dir_fd: int | None = None):
    """Acquire path and pinned-fd social locks in one physical stable order."""
    requests: dict[tuple[object, ...], tuple[str, object]] = {}
    for path in paths:
        expanded = path.expanduser()
        try:
            parent_metadata = expanded.parent.stat()
            # One physical parent plus the exact app-owned leaf is one lock,
            # even through APFS case aliases that have different spellings.
            key = (
                "inode",
                int(parent_metadata.st_dev),
                int(parent_metadata.st_ino),
                expanded.name,
            )
        except OSError:
            key = (
                "path",
                os.path.normcase(str(expanded.resolve(strict=False))),
            )
        requests.setdefault(key, ("path", path))
    if retained_dir_fd is not None:
        retained_metadata = os.fstat(retained_dir_fd)
        retained_key = (
            "inode",
            int(retained_metadata.st_dev),
            int(retained_metadata.st_ino),
            _SOCIAL_SESSION_FILENAME,
        )
        # The pinned handle wins when a public alias names this same root.
        requests[retained_key] = ("fd", retained_dir_fd)
    with ExitStack() as stack:
        for key in sorted(requests, key=repr):
            request_kind, value = requests[key]
            if request_kind == "fd":
                stack.enter_context(_social_session_lock_at(int(value)))
            else:
                stack.enter_context(_social_session_lock(value))
        previous_depth = int(getattr(_SOCIAL_SESSION_LOCK_CONTEXT, "depth", 0) or 0)
        _SOCIAL_SESSION_LOCK_CONTEXT.depth = previous_depth + 1
        try:
            yield
        finally:
            _SOCIAL_SESSION_LOCK_CONTEXT.depth = previous_depth


def _write_social_session_record(path: Path, data: dict) -> None:
    with _social_session_lock(path):
        _write_private_json(path, data)


def _save_auth_unlocked(data: dict) -> bool:
    """Write community_auth.json without locking; caller must hold the lock."""
    p = _auth_path()
    if not p:
        return False
    try:
        _write_private_json(p, data)
    except OSError as exc:
        logger.warning("card_drop: save auth failed: %s", exc)
        return False
    return True


def _save_auth(data: dict) -> bool:
    social_path = _social_session_path()
    if social_path is None:
        return _save_auth_unlocked(data)
    try:
        with _social_session_locks(_social_session_paths()):
            return _save_auth_unlocked(data)
    except (OSError, TimeoutError) as exc:
        logger.warning("card_drop: save auth failed: %s", exc)
        return False


def _persist_repaired_bind(access_token: str, bind: dict) -> None:
    """Record a repaired bind without clobbering a concurrent refresh.

    Serialized on the social-session lock rather than one keyed to the auth
    file: ``_persist_refreshed_oauth_tokens`` rewrites ``community_auth.json``
    while holding that lock, so it is the only lock both writers share.
    """
    path = _auth_path()
    social_path = _social_session_path()
    if path is None or social_path is None:
        return
    try:
        with _social_session_locks(_social_session_paths()):
            current = _read_json_dict(path)
            if not current:
                return
            if str(current.get("access_token") or "").strip() != access_token:
                # 镜像 token 与本次 bind 的 token 不一致有两种含义：更新的登录或
                # 刷新赢了（不该动），或者 refresh 已写入权威 social_session.json
                # 而 community_auth.json 镜像那次 best-effort 写失败（该记，否则
                # /auth-status 永远停在旧的瞬时错误上）。以权威快照裁决。
                social = _desktop_session_snapshot()
                authoritative = str((social or {}).get("access_token") or "").strip()
                if authoritative != access_token:
                    return
                # A two-file account switch can leave a *newer* auth mirror
                # beside the old social file. Only a matching identity proves
                # that this is a lagging refresh mirror for the same account.
                mirror_user = _normalize_local_user_id(current.get("local_user_id"))
                if not mirror_user or mirror_user != (social or {}).get("local_user_id"):
                    return
                if _normalize_auth_source(current.get("auth_source")) != social.get("auth_source"):
                    return
                # Persist the validated bind's session separately: the mirror
                # may contain either lagging or ahead-of-social credentials.
                # Rewriting its tokens here could roll back a newer refresh.
                current["bind_session_fingerprint"] = _desktop_session_fingerprint(social)
            else:
                current.pop("bind_session_fingerprint", None)
            _write_private_json(path, {**current, "bind": bind})
    except (OSError, TimeoutError) as exc:
        logger.warning("card_drop: persist repaired bind failed: %s", exc)


def _save_social_session_unlocked(
    path: Path,
    base: str,
    access: str | None,
    refresh: str | None,
    *,
    local_user_id: str,
    auth_source: str,
    auth_public_url: str | None = None,
    client_id: str | None = None,
) -> bool:
    """Write social session without acquiring lock; caller must hold it."""
    normalized_user_id = _normalize_local_user_id(local_user_id)
    normalized_source = _normalize_auth_source(auth_source)
    if not access or not normalized_user_id or not normalized_source:
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
        _write_private_json(path, data)
    except OSError as exc:
        logger.warning("card_drop: save social session failed: %s", exc)
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
    p = _social_session_path()
    if not p:
        return False
    try:
        with _social_session_lock(p):
            return _save_social_session_unlocked(
                p,
                base,
                access,
                refresh,
                local_user_id=local_user_id,
                auth_source=auth_source,
                auth_public_url=auth_public_url,
                client_id=client_id,
            )
    except (OSError, TimeoutError) as exc:
        logger.warning("card_drop: save social session failed: %s", exc)
        return False


def _persist_session_credentials(
    auth_payload: dict,
    bind: dict,
    base: str,
    access: str,
    refresh: str | None,
    *,
    local_user_id: str,
    auth_source: str,
) -> None:
    """Persist both desktop credential files from a worker thread."""
    auth_saved = _save_auth(auth_payload)
    social_saved = _save_social_session(
        base,
        access,
        refresh,
        local_user_id=local_user_id,
        auth_source=auth_source,
    )
    if not (auth_saved and social_saved):
        bind["local_save_failed"] = True
        # If auth was written before the Electron session failed, persist the
        # partial-success marker there as well so auth-status can surface it.
        if auth_saved:
            _save_auth(auth_payload)
    if auth_saved or social_saved:
        # Any credential publication can represent an account switch or token
        # rotation. Existing browser delegates must be reissued for that session.
        _clear_native_delegates()


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
    paths = _social_session_paths()
    try:
        # The fallback creation and auth mirror update must share the same
        # fence as deletion, not just the write to an individual social file.
        with _social_session_locks(paths):
            auth = _load_auth()
            for path in paths:
                data = _read_json_dict(path)
                access = str((data or {}).get("token") or "").strip()
                if not data or not access:
                    continue
                base = str(data.get("baseUrl") or _social_base_url()).strip().rstrip("/")
                refresh = str(data.get("refresh_token") or "").strip()
                if (access, base, refresh) != (
                    expected_access, expected_base, expected_refresh,
                ):
                    return False
                _write_private_json(path, {
                    **data,
                    "schema_version": _SOCIAL_SESSION_SCHEMA_VERSION,
                    "local_user_id": normalized_user_id,
                    "auth_source": normalized_source,
                })
                break
            else:
                # Only a still-current auth-only session can create its social
                # companion. A snapshot validated before logout is not enough.
                if not auth or not paths or (
                    str(auth.get("access_token") or "").strip(),
                    str(auth.get("refresh_token") or "").strip(),
                ) != (expected_access, expected_refresh):
                    return False
                if not _save_social_session_unlocked(
                    paths[0], expected_base, expected_access, expected_refresh or None,
                    local_user_id=normalized_user_id, auth_source=normalized_source,
                ):
                    return False

            if auth is not None:
                # Do not apply an older identity to a newer auth mirror.
                if str(auth.get("access_token") or "").strip() != expected_access:
                    return False
                user = auth.get("user") if isinstance(auth.get("user"), dict) else {}
                return _save_auth_unlocked({
                    **auth,
                    "schema_version": _SOCIAL_SESSION_SCHEMA_VERSION,
                    "local_user_id": normalized_user_id,
                    "auth_source": normalized_source,
                    "user": {**user, "id": normalized_user_id},
                })
            return True
    except (OSError, TimeoutError) as exc:
        logger.warning("card_drop: save social identity metadata failed: %s", exc)
        return False


def _unlink_credentials(paths: list[Path]) -> bool:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("card_drop: clear credential failed for %s: %s", path, exc)
            return False
    return True


def _clear_auth() -> bool:
    if not _logout_storage_ready():
        logger.warning(
            "card_drop: committed storage root is unavailable; credential clear deferred"
        )
        return False
    auth_path = _auth_path()
    authoritative_paths = (
        [auth_path, _social_session_path(), _community_state_path(_SOCIAL_SESSION_FILENAME)]
        + [_community_state_path(_OAUTH_PENDING_FILENAME)]
        + [_community_state_path(_STEAM_PENDING_FILENAME)]
    )
    authoritative_paths = [path for path in authoritative_paths if path is not None]
    authoritative_paths = list(dict.fromkeys(authoritative_paths))
    legacy_paths = (
        _legacy_private_file_paths(_AUTH_FILENAME)
        + [_legacy_social_session_path()]
        + _legacy_private_file_paths(_SOCIAL_SESSION_FILENAME)
        + _logout_private_file_paths(_AUTH_FILENAME)
        + _logout_private_file_paths(_SOCIAL_SESSION_FILENAME)
        + _logout_private_file_paths(_OAUTH_PENDING_FILENAME)
        + _logout_private_file_paths(_STEAM_PENDING_FILENAME)
    )
    legacy_paths = [
        path
        for path in dict.fromkeys(legacy_paths)
        if path is not None and path not in authoritative_paths
    ]
    paths = [*legacy_paths, *authoritative_paths]
    if auth_path is None:
        logger.warning("card_drop: cannot resolve auth path while clearing credentials")
        return False
    lock_paths = _social_session_paths()
    # Logout also removes a committed/effective target that can be retained
    # solely as a conflict witness and therefore omitted from normal reads.
    # If that root already exists, fence its Electron-visible session leaf in
    # the same transaction; never create a missing or unsafe legacy root just
    # to place a lock in it.
    for path in paths:
        if (
            path.name == _SOCIAL_SESSION_FILENAME
            and path not in lock_paths
            and _existing_safe_parent(path)
        ):
            lock_paths.append(path)
    # community_auth.json 的删除也必须在 social-session 锁内：否则
    # _persist_repaired_bind 可在锁内读到旧记录、在本次删除之后把它写回去，
    # 登出只清掉 social 文件而镜像复活，clear 还会误报失败。
    try:
        with _social_session_locks(lock_paths):
            # Preflight every exact app-owned file before deleting the first
            # one. Failure to reach or mutate the selected target must leave
            # canonical/host credentials intact so logout remains retryable.
            for path in paths:
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    logger.warning(
                        "card_drop: cannot inspect credential before clear for %s: %s",
                        path,
                        exc,
                    )
                    return False
                if stat.S_ISLNK(metadata.st_mode) or path_chain_has_symlink(path.parent):
                    logger.warning("card_drop: unsafe credential path while clearing: %s", path)
                    return False
                if not os.access(path.parent, os.W_OK | os.X_OK):
                    logger.warning(
                        "card_drop: credential directory is not writable: %s",
                        path.parent,
                    )
                    return False
            # Commit logical logout before deleting reachable copies.  A
            # phase-0 legacy source may be temporarily offline and therefore
            # impossible to erase; its older generation must remain invalid
            # if the same directory later reappears.
            try:
                _advance_logout_epoch()
            except OSError as exc:
                logger.warning("card_drop: cannot persist logout generation: %s", exc)
                return False
            if not _unlink_credentials(paths):
                return False
            for path in paths:
                try:
                    path.lstat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    logger.warning("card_drop: cannot verify credential clear for %s: %s", path, exc)
                    return False
                else:
                    logger.warning("card_drop: credential still exists after clear: %s", path)
                    return False
            _clear_native_delegates()
            # A post-logout guest can mint a ticket as soon as these file
            # locks are released, so invalidate older proofs before then.
            with _native_sync_tickets_lock:
                _native_sync_tickets.clear()
    except (OSError, TimeoutError) as exc:
        logger.warning("card_drop: clear credentials failed to fence writers: %s", exc)
        return False
    return True


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


async def _request_desktop_session_auth(base: str, access: str) -> _BrowserAuth:
    """Validate a Desktop bearer and return its verified local user principal."""
    request_lookup = await _lookup_cloud_identity(base, access)
    if request_lookup.identity is None:
        state = (
            "unavailable"
            if request_lookup.failure in {"unavailable", "malformed"}
            else "mismatch"
        )
        return _BrowserAuth(state)
    desktop_lookup = await _resolve_saved_desktop_identity(base)
    if desktop_lookup.identity is None:
        state = (
            "unavailable"
            if desktop_lookup.failure in {"unavailable", "malformed"}
            else "mismatch"
        )
        return _BrowserAuth(state)
    if secrets.compare_digest(
        request_lookup.identity.local_user_id,
        desktop_lookup.identity.local_user_id,
    ):
        return _BrowserAuth(
            "match",
            request_lookup.identity.local_user_id,
        )
    return _BrowserAuth("mismatch")


async def _request_matches_desktop_session(base: str, access: str) -> str:
    """Return ``match``, ``mismatch``, or ``unavailable`` for a request bearer."""
    return (await _request_desktop_session_auth(base, access)).state or "mismatch"


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
    cid = await asyncio.to_thread(_get_client_id) if bind_client else None
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
    await asyncio.to_thread(
        _persist_session_credentials,
        auth_payload,
        bind,
        base,
        access,
        refresh,
        local_user_id=local_user_id,
        auth_source=normalized_source,
    )
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
_STEAM_PENDING_TTL_SEC = 600  # 点登录后 10 分钟内必须完成回调
_STEAM_PENDING_CONSUME_LOCK = threading.Lock()


def _steam_pending_path() -> Path | None:
    return _community_state_path(_STEAM_PENDING_FILENAME)


def _steam_pending_paths() -> list[Path]:
    canonical = _steam_pending_path()
    paths = [canonical] if canonical is not None else []
    for candidate in _legacy_private_file_paths(_STEAM_PENDING_FILENAME):
        if candidate not in paths:
            paths.append(candidate)
    return paths


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


def _steam_pending_consume_paths() -> list[Path]:
    paths: list[Path] = []
    for candidate in [
        *_steam_pending_paths(),
        *_legacy_private_conflict_paths(_STEAM_PENDING_FILENAME),
    ]:
        if candidate not in paths:
            paths.append(candidate)
    return paths


def _drop_matching_steam_pending_copies(expected: dict, candidates: list[Path]) -> bool:
    """Claim every accepted copy and prove this consumer removed at least one."""

    claimed = False
    for candidate in candidates:
        candidate_state, candidate_data = _read_private_json_state(candidate)
        if candidate_state != "valid" or candidate_data != expected:
            continue
        try:
            candidate.unlink()
            claimed = True
            fsync_directory_best_effort(candidate.parent)
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.debug("card_drop: consume steam pending failed for %s: %s", candidate, exc)

    if not claimed:
        return False
    for candidate in candidates:
        candidate_state, candidate_data = _read_private_json_state(candidate)
        if candidate_state == "valid" and candidate_data == expected:
            return False
    return True


def _consume_steam_pending(state: str) -> tuple[bool, str | None]:
    with _STEAM_PENDING_CONSUME_LOCK:
        return _consume_steam_pending_locked(state)


def _consume_steam_pending_locked(state: str) -> tuple[bool, str | None]:
    """Consume the one-shot pending marker (exists, fresh, state matches).

    Returns ``(ok, code_verifier)``. On success, ``code_verifier`` is the stored
    PKCE verifier (``None`` for legacy markers without it — exchange omits
    verifier for backward compatibility). Delete the marker only when it is
    corrupt, expired, or the state matches; keep it until TTL on mismatch so a
    wrong-state callback cannot DoS a legitimate login. If state matches but
    delete fails, return ``(False, None)`` to preserve one-shot semantics.
    """
    p = _steam_pending_path()
    if not p:
        return False, None
    consume_paths = _steam_pending_consume_paths()
    data: object = _load_or_migrate_private_json(
        p,
        [candidate for candidate in _steam_pending_paths() if candidate != p],
        validator=_steam_pending_record_is_fresh,
        conflict_paths=_legacy_private_conflict_paths(_STEAM_PENDING_FILENAME),
    ) or {}

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
    if not _drop_matching_steam_pending_copies(data, consume_paths):
        return False, None
    return True, stored_verifier


@router.get("/auth-status", summary="社区登录状态")
async def auth_status_endpoint(request: Request):
    if not _local_request_source_allowed(request):
        return JSONResponse(
            {"detail": "origin_not_allowed"},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    # Validate the bearer (and refresh OAuth sessions when necessary) before
    # telling the UI that it is logged in.  Import lazily to keep the router
    # modules' existing dependency direction intact.
    from main_routers import community_oauth

    status = await community_oauth.resolve_saved_oauth_status()
    if status["logged_in"]:
        a = status["auth"]
        u = a.get("user") if isinstance(a.get("user"), dict) else {}
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
    """Issue a short-lived ticket readable only by the local NEKO page.

    Minting uses the same browser boundary as /native-delegate, with Fetch
    Metadata and local Origin checks. These headers do not authenticate raw
    local processes, which can forge them. Bearer redemption additionally
    requires the ticket to match its persisted issuing session.
    """
    if not _local_ui_request_source_allowed(request):
        return JSONResponse(
            {"detail": "origin_not_allowed"},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    try:
        # Resolve first, as for native delegates: redemption performs this same
        # refresh/identity backfill and must not invalidate a just-minted proof.
        # Missing or offline sessions still retain the guest-bind ticket path.
        await _native_delegate_session_snapshot()
        ticket = await asyncio.to_thread(_issue_sync_ticket_for_session)
    except (OSError, TimeoutError):
        return JSONResponse(
            {"detail": "desktop_session_busy"}, status_code=503,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    return JSONResponse(
        {"sync_ticket": ticket, "expires_in": _SYNC_TICKET_TTL_SEC},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _issue_sync_ticket_for_session() -> str:
    with _social_session_locks(_social_session_paths()):
        return _issue_sync_ticket(_desktop_session_fingerprint(_desktop_session_snapshot()))


def _load_auth_for_verified_session(snapshot: dict) -> dict | None:
    """Read the mirror only while the remotely verified session still owns it."""
    try:
        with _social_session_locks(_social_session_paths()):
            auth = _load_auth() or {}
            if _desktop_session_fingerprint(_desktop_session_snapshot()) != _desktop_session_fingerprint(snapshot):
                return None
            return auth
    except (OSError, TimeoutError):
        return None


def _consume_sync_ticket_for_verified_session(
    sync_ticket: object,
    access_token: str,
) -> str:
    """Consume the ticket only while the verified session still owns the file.

    Returns "ok", "changed" (desktop session was replaced / lock busy; ticket
    preserved), or "invalid" (the ticket itself is spent).
    """
    try:
        # The Electron main process takes this same lock for logout / account
        # switch / token refresh, so its write either completed before this
        # scope (the re-read below mismatches -> 409) or it waits until the
        # ticket is consumed and the response is already being queued.
        with _social_session_locks(_social_session_paths()):
            current = _desktop_session_snapshot()
            current_token = str((current or {}).get("access_token") or "").strip()
            if current_token != access_token:
                return "changed"
            return "ok" if _consume_sync_ticket(
                sync_ticket, session_fingerprint=_desktop_session_fingerprint(current)
            ) else "invalid"
    except (OSError, TimeoutError) as exc:
        # A concurrent writer is holding the lock; assume the session is being
        # replaced and keep the ticket for a retry.
        logger.warning("card_drop: sync ticket consume fenced off: %s", exc)
        return "changed"


def _handoff_return_url(return_to: str | None, audience: str) -> str | None:
    """Accept only same-origin return URLs under the configured social base."""
    base = (audience or "").strip().rstrip("/")
    if not base:
        return None
    candidate = (return_to or base).strip() or base
    try:
        parsed = urlparse(candidate)
        base_parsed = urlparse(base)
    except (TypeError, ValueError):
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not _same_originish(candidate, base):
        return None
    path = parsed.path or "/cards"
    if not path.startswith("/"):
        path = "/" + path
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{base_parsed.scheme}://{base_parsed.netloc}{path}{query}"


async def _native_delegate_session_snapshot() -> tuple[dict | None, str]:
    """Refresh, validate, and load a fingerprintable desktop session."""
    # Pet requests this endpoint before its separate auth-status probe. Resolve
    # OAuth first so a just-refreshed access token cannot invalidate the newly
    # issued delegate immediately after the community tab opens.
    from main_routers import community_oauth

    status = await community_oauth.resolve_saved_oauth_status()
    if not status.get("logged_in"):
        return None, "unavailable" if status.get("snapshot") else "missing"

    snapshot = await asyncio.to_thread(_desktop_session_snapshot)
    if snapshot is None:
        return None, "missing"
    verified = status.get("snapshot") or {}
    credentials_changed = any(
        snapshot.get(key) != verified.get(key)
        for key in ("base_url", "access_token", "refresh_token")
    )
    identity_changed = any(
        verified.get(key) and snapshot.get(key) != verified.get(key)
        for key in ("local_user_id", "auth_source")
    )
    if credentials_changed or identity_changed:
        # A local replacement after cloud validation is not itself validated.
        return None, "missing"
    # A concurrent proof request may have backfilled missing identity metadata
    # for these same validated credentials. Preserve that verified enrichment.
    if _desktop_session_fingerprint(snapshot):
        return snapshot, ""

    base = str(snapshot.get("base_url") or _social_base_url()).strip().rstrip("/")
    lookup = await _resolve_saved_desktop_identity(base)
    if lookup.identity is None:
        return None, lookup.failure or "rejected"

    # Delegate validation re-reads the persisted desktop session on every use.
    # Issue only after the verified legacy identity was durably backfilled.
    snapshot = await asyncio.to_thread(_desktop_session_snapshot)
    if snapshot is None or not _desktop_session_fingerprint(snapshot):
        return None, "unavailable"
    return snapshot, ""


@router.get("/native-delegate", summary="签发短时 scoped native delegate（facts）")
async def native_delegate_endpoint(request: Request):
    """Mint a reusable short-lived proof for the community Web tab.

    Issued only from the trusted local NEKO UI (never from the community Origin).
    The Web tab presents this bearer to facts endpoints instead of the platform
    OAuth access token, so a rogue localhost listener cannot harvest refreshable
    Web credentials. Forge-credit reads go directly to the cloud with OAuth.
    """
    if not _local_ui_request_source_allowed(request):
        return JSONResponse(
            {"detail": "origin_not_allowed"},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    snapshot, failure = await _native_delegate_session_snapshot()
    local_user_id = (snapshot or {}).get("local_user_id") or ""
    session_fingerprint = _desktop_session_fingerprint(snapshot)
    if not snapshot or not local_user_id or not session_fingerprint:
        unavailable = failure in {"unavailable", "malformed"}
        return JSONResponse(
            {
                "detail": (
                    "identity_verification_unavailable"
                    if unavailable
                    else "desktop_login_required"
                )
            },
            status_code=503 if unavailable else 409,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    audience = _social_base_url()
    delegate = _issue_native_delegate(
        local_user_id=str(local_user_id),
        audience=audience,
        session_fingerprint=session_fingerprint,
    )
    return JSONResponse(
        {
            "native_delegate": delegate,
            "expires_in": _NATIVE_DELEGATE_TTL_SEC,
            "scopes": sorted(_NATIVE_DELEGATE_SCOPES),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.get("/native-delegate/handoff", summary="浏览器回跳领取 scoped native delegate")
async def native_delegate_handoff_endpoint(
    request: Request,
    return_to: str | None = Query(default=None, max_length=2048),
):
    """Bounce the community tab through NEKO to attach ``#native_delegate``.

    Only a same-origin Pet navigation may enter this compatibility path;
    ``return_to`` must stay on the configured social origin.
    """
    if not _local_ui_request_source_allowed(request):
        return JSONResponse(
            {"detail": "origin_not_allowed"},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    audience = _social_base_url()
    dest = _handoff_return_url(return_to, audience)
    if not dest:
        return JSONResponse(
            {"detail": "return_to_not_allowed"},
            status_code=400,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    snapshot, failure = await _native_delegate_session_snapshot()
    local_user_id = (snapshot or {}).get("local_user_id") or ""
    session_fingerprint = _desktop_session_fingerprint(snapshot)
    if not snapshot or not local_user_id or not session_fingerprint:
        unavailable = failure in {"unavailable", "malformed"}
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>需要 Desktop 登录</title>"
            "<body style='font-family:sans-serif;padding:40px'>"
            + (
                "<h1>暂时无法验证 Desktop 登录状态</h1>"
                "<p>请稍后重试打开猫娘社区。</p>"
                if unavailable
                else "<h1>请先在 N.E.K.O. 桌宠完成社区登录</h1>"
                "<p>登录后再打开猫娘社区，铸造券即可连接本机账本。</p>"
            )
            + "</body>",
            status_code=503 if unavailable else 409,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    delegate = _issue_native_delegate(
        local_user_id=str(local_user_id),
        audience=audience,
        session_fingerprint=session_fingerprint,
    )
    return RedirectResponse(
        f"{dest}#native_delegate={quote(delegate, safe='')}",
        status_code=302,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.options("/social-session-init", summary="社区网页复用 Desktop 登录态预检")
async def social_session_init_options(request: Request):
    cors = _sync_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    if not client_registration.proof_transport_allowed(_social_base_url()):
        return JSONResponse(
            {"detail": "insecure_transport"}, status_code=403, headers=cors
        )
    return JSONResponse({"ok": True}, headers=cors)


@router.post("/social-session-init", summary="一次性消费 native_sync ticket，交付 Desktop OAuth 会话")
async def social_session_init_endpoint(request: Request, payload: dict = Body(...)):
    """Hand the Desktop OAuth session to the community tab so it logs in once.

    The community SPA (opened by NEKO with a ``#native_sync`` ticket) redeems
    that one-time ticket here. In exchange it receives the Desktop
    ``neko-servers-desktop-*`` access token, which N.E.K.O.Servers already
    accepts (``AUTH_ALLOWED_DESKTOP_CLIENT_IDS``). The SPA then runs its normal
    ``/api/auth/session/bootstrap`` and publishes the session, so no second
    browser login is needed.

    The refresh token stays here: Desktop is the sole owner of that rotating
    family, and a second independent rotator would invalidate both sides. The
    community session therefore lives as long as the handed-over access token.

    Direction is Python → SPA over the allowlisted community Origin only; the
    Web tab never sends its own bearer to localhost. Ticket is single-use, and
    is consumed only once the response is known to be deliverable.
    """
    if not (request.headers.get("origin") or "").strip():
        # Require the browser-origin contract even though raw local clients
        # can forge Origin; this check alone is not process authentication.
        return JSONResponse(
            {"detail": "origin_required"},
            status_code=403,
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    cors = _sync_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    if not client_registration.proof_transport_allowed(_social_base_url()):
        return JSONResponse(
            {"detail": "insecure_transport"}, status_code=403, headers=cors
        )
    sync_ticket = payload.get("sync_ticket") or payload.get("syncTicket")
    if not _sync_ticket_is_valid(sync_ticket):
        return JSONResponse(
            {"detail": "invalid_sync_ticket"}, status_code=403, headers=cors
        )
    snapshot, failure = await _native_delegate_session_snapshot()
    access_token = str((snapshot or {}).get("access_token") or "").strip()
    local_user_id = str((snapshot or {}).get("local_user_id") or "").strip()
    if not snapshot or not access_token or not local_user_id:
        unavailable = failure in {"unavailable", "malformed"}
        return JSONResponse(
            {
                "detail": (
                    "identity_verification_unavailable"
                    if unavailable
                    else "desktop_login_required"
                )
            },
            status_code=503 if unavailable else 409,
            headers=cors,
        )
    if snapshot.get("auth_source") != "oauth":
        return JSONResponse(
            {"detail": "legacy_session_not_supported"}, status_code=409, headers=cors
        )
    if not _sync_ticket_is_valid(
        sync_ticket, session_fingerprint=_desktop_session_fingerprint(snapshot)
    ):
        return JSONResponse({"detail": "invalid_sync_ticket"}, status_code=403, headers=cors)
    snapshot_base = str(snapshot.get("base_url") or "").strip().rstrip("/")
    if snapshot_base and not _same_originish(snapshot_base, _social_base_url()):
        # The saved login belongs to another community; refuse rather than ship
        # its bearer to the currently configured one. The session stays on disk
        # so pointing NEKO_SOCIAL_BASE_URL back restores it.
        return JSONResponse(
            {"detail": "desktop_login_required"}, status_code=409, headers=cors
        )
    from main_routers import community_oauth as _co

    # Fence the local await against logout/refresh/account changes without
    # repeating the remote identity lookup that just validated this snapshot.
    auth = await asyncio.to_thread(_load_auth_for_verified_session, snapshot)
    if auth is None:
        return JSONResponse(
            {"detail": "desktop_login_required"}, status_code=409, headers=cors
        )
    # The auth mirror may already belong to a newer login while the social
    # file is still authoritative for this one. Never reuse its bind outcome.
    # Older mirrors without identity metadata are still tied by the exact
    # bearer; a missing bind field retains the existing compatibility default.
    auth_matches_session = (
        str(auth.get("access_token") or "").strip() == access_token
        and (not auth.get("local_user_id")
             or _normalize_local_user_id(auth["local_user_id"]) == local_user_id)
        and (not auth.get("auth_source")
             or _normalize_auth_source(auth["auth_source"]) == snapshot["auth_source"])
    )
    bind = auth.get("bind") or {"bound": True, "error": None}
    if auth and not auth_matches_session:
        repaired_bind_matches = (
            auth.get("bind_session_fingerprint") == _desktop_session_fingerprint(snapshot)
            and _normalize_local_user_id(auth.get("local_user_id")) == local_user_id
            and _normalize_auth_source(auth.get("auth_source")) == snapshot["auth_source"]
        )
        if not repaired_bind_matches:
            bind = {"bound": False, "error": "desktop_bind_state_unavailable"}
        # Issuer/client metadata must not leak across this boundary either.
        # The authoritative snapshot or configured defaults supply the issuer.
        auth = {}
    bind_retried = False
    if not bind.get("bound") and bind.get("error") != _BIND_OWNERSHIP_CONFLICT:
        # Desktop binds once at callback time and never retries. Redeeming the
        # ticket used to be the SPA's chance to repair a failed bind, so retry
        # here before it is spent — but only after the session revalidation
        # above, so a superseded account never reaches the cloud bind.
        bind_retried = True
        bind = await _co._oauth_guest_bind(_social_base_url(), access_token)
        if bind.get("bound") or bind.get("error") == _BIND_OWNERSHIP_CONFLICT:
            # Persist repaired binds and terminal conflicts alike, or
            # /auth-status keeps reporting the stale transient failure and
            # every later handoff repeats the bind round trip.
            await asyncio.to_thread(_persist_repaired_bind, access_token, bind)
    if bind_retried:
        # bind 可能包含多个最长 30 秒的云端往返；期间 token 可能已被云端撤销而
        # 本地文件未变，锁内复读只比本地 token 发现不了。重跑一次带云端校验的
        # 复查（仅 bind 分支付出这个延迟），拒绝就 409 并保留票据给用户重试。
        post_bind_snapshot, _ = await _native_delegate_session_snapshot()
        post_bind_token = (
            str(post_bind_snapshot.get("access_token") or "").strip()
            if post_bind_snapshot
            else ""
        )
        if post_bind_token != access_token:
            return JSONResponse(
                {"detail": "desktop_login_required"}, status_code=409, headers=cors
            )
    # 「复查 → 消费」在 _social_session_lock 里执行：Electron 侧的登出/切号/刷新
    # 写 social_session.json 时持有同一把锁（见 social-session-refresh.js 的
    # .lock 协议），所以那次写入要么发生在进锁之前（锁内复读磁盘发现 token 已变，
    # 返回 409），要么被挡到 ticket 消费完成之后。bind 重试本身仍是 await 点，
    # 没法放进同步锁作用域；残余窗口只剩「出锁 → 响应送达」这段本地回环时间。
    consume_outcome = await asyncio.to_thread(
        _consume_sync_ticket_for_verified_session, sync_ticket, access_token
    )
    if consume_outcome == "invalid":
        return JSONResponse(
            {"detail": "invalid_sync_ticket"}, status_code=403, headers=cors
        )
    if consume_outcome != "ok":
        return JSONResponse(
            {"detail": "desktop_login_required"}, status_code=409, headers=cors
        )
    return JSONResponse(
        {
            "access_token": access_token,
            "local_user_id": local_user_id,
            "auth_public_url": str(
                snapshot.get("auth_public_url")
                or auth.get("auth_public_url")
                or _co._auth_public_url()
            ).rstrip("/"),
            "client_id": str(
                snapshot.get("client_id")
                or auth.get("client_id")
                or _co._desktop_client_id()
            ),
            "bind": bind,
        },
        headers={**cors, "Cache-Control": "no-store", "Pragma": "no-cache"},
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
    credentials = await asyncio.to_thread(_get_client_credentials)
    if not credentials:
        return JSONResponse(
            {"detail": "client_not_registered"}, status_code=409, headers=cors
        )
    client_id, client_proof = credentials
    base_url = _social_base_url()
    approval_payload = {
        "client_id": client_id,
        "binding_challenge": challenge,
        "client_proof": client_proof,
    }

    async def _approve():
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SEC) as client:
                res = await client.post(
                    f"{base_url}/api/clients/bind-approval", json=approval_payload
                )
        except (httpx.HTTPError, OSError):
            return None, None
        try:
            return res, res.json().get("detail")
        except (ValueError, TypeError, AttributeError):
            return res, None

    response, detail = await _approve()
    if response is None:
        return JSONResponse(
            {"detail": "cloud_unreachable"}, status_code=502, headers=cors
        )
    # Register-then-retry once: the cloud reports an unknown client_id the same
    # way it reports a bad proof, and this install may never have registered.
    if client_registration.looks_unregistered(
        response.status_code, detail
    ) and await client_registration.ensure_client_registered(base_url, force=True):
        response, detail = await _approve()
        if response is None:
            return JSONResponse(
                {"detail": "cloud_unreachable"}, status_code=502, headers=cors
            )
    if response.status_code >= 400:
        return JSONResponse(
            {"detail": detail or f"http_{response.status_code}"},
            status_code=response.status_code,
            headers=cors,
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
        current_access = await asyncio.to_thread(_access_token) or ""
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
        if not await asyncio.to_thread(_clear_auth):
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


def _delegate_audience_matches(request: Request, audience: str) -> bool:
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    if not origin or not audience:
        return False
    return _same_originish(origin, audience) or _exact_origin_matches(origin, audience)


async def _scoped_native_auth(
    request: Request,
    required_scope: str,
) -> _BrowserAuth:
    """Validate a scoped native delegate and return its bound principal."""
    supplied = _request_bearer_token(request)
    if not supplied:
        return _BrowserAuth(None)
    expected_user = _normalize_local_user_id(
        request.headers.get("x-neko-local-user-id")
    )
    entry = _native_delegate_entry(supplied)
    if entry is None:
        # Native delegates are opaque and intentionally indistinguishable from
        # other bearer tokens.  The principal header marks this as a delegate
        # request, so an expired/evicted value must fail closed instead of being
        # retried as a cloud access token.
        return _BrowserAuth("mismatch" if expected_user else None)
    scopes = entry.get("scopes") or frozenset()
    if required_scope not in scopes:
        return _BrowserAuth("mismatch")
    if not _delegate_audience_matches(request, str(entry.get("audience") or "")):
        return _BrowserAuth("mismatch")
    bound_user = _normalize_local_user_id(entry.get("local_user_id"))
    if not bound_user or not expected_user or expected_user != bound_user:
        return _BrowserAuth("mismatch")
    snapshot = await asyncio.to_thread(_desktop_session_snapshot)
    current_user = _normalize_local_user_id((snapshot or {}).get("local_user_id"))
    current_fingerprint = _desktop_session_fingerprint(snapshot)
    bound_fingerprint = str(entry.get("session_fingerprint") or "")
    if (
        not current_user
        or not current_fingerprint
        or current_user != bound_user
        or not secrets.compare_digest(current_fingerprint, bound_fingerprint)
    ):
        _discard_native_delegate(supplied)
        return _BrowserAuth("mismatch")
    return _BrowserAuth("match", bound_user)


async def _scoped_native_auth_state(
    request: Request,
    required_scope: str,
) -> str | None:
    """Return auth state when the bearer is a scoped native delegate, else None."""
    return (await _scoped_native_auth(request, required_scope)).state


async def _facts_request_auth_state(request: Request) -> str:
    scoped = await _scoped_native_auth_state(request, "facts:read")
    if scoped is not None:
        return scoped
    supplied = _request_bearer_token(request)
    if not supplied:
        return "mismatch"
    return await _request_matches_desktop_session(_social_base_url(), supplied)


async def _build_local_forge_facts(**kwargs):
    """Lazy import keeps the normal main-server import surface lightweight."""
    from main_logic.card_forge_facts import build_forge_facts_payload

    return await build_forge_facts_payload(**kwargs)


def _validate_fact_exclusions(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field}_must_be_an_array")
    if len(value) > _FACT_QUERY_MAX_EXCLUSIONS:
        raise ValueError(f"{field}_too_many_items")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field}_item_must_be_a_string")
        text = item.strip()
        if (
            not 1 <= len(text) <= _FACT_QUERY_MAX_EXCLUSION_LENGTH
            or "," in text
            or any(ord(char) < 32 for char in text)
        ):
            raise ValueError(f"{field}_item_invalid")
        normalized.append(text)
    return normalized


def _validate_facts_query(payload: dict) -> dict:
    runtime_character_hint = payload.get("runtime_character_hint")
    if runtime_character_hint is not None:
        if not isinstance(runtime_character_hint, str):
            raise ValueError("runtime_character_hint_must_be_a_string")
        runtime_character_hint = runtime_character_hint.strip()
        if len(runtime_character_hint) > 64:
            raise ValueError("runtime_character_hint_too_long")

    min_importance = payload.get("min_importance", 0)
    if (
        isinstance(min_importance, bool)
        or not isinstance(min_importance, int)
        or not 0 <= min_importance <= 10
    ):
        raise ValueError("min_importance_out_of_range")

    include_absorbed = payload.get("include_absorbed", True)
    if not isinstance(include_absorbed, bool):
        raise ValueError("include_absorbed_must_be_boolean")

    limit = payload.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError("limit_out_of_range")

    exclude_fact_ids = _validate_fact_exclusions(
        payload.get("exclude_fact_ids"),
        "exclude_fact_ids",
    )
    exclude_hashes = _validate_fact_exclusions(
        payload.get("exclude_hashes"),
        "exclude_hashes",
    )
    return {
        "runtime_character_hint": runtime_character_hint,
        "min_importance": min_importance,
        "include_absorbed": include_absorbed,
        "limit": limit,
        "exclude_fact_ids": ",".join(exclude_fact_ids) or None,
        "exclude_hashes": ",".join(exclude_hashes) or None,
    }


async def _forge_facts_response(request: Request, *, query: dict):
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
        return JSONResponse(
            {"detail": "local_session_mismatch"},
            status_code=401,
            headers=cors,
        )
    payload = await _build_local_forge_facts(**query)
    return JSONResponse(payload, headers=cors)


@router.options("/capabilities", summary="社区探测本机 card-drop 协议")
async def card_drop_capabilities_options(request: Request):
    cors = _capabilities_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse({"ok": True}, headers=cors)


@router.get("/capabilities", summary="社区探测本机 card-drop 协议")
async def card_drop_capabilities_endpoint(request: Request):
    cors = _capabilities_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse(
        {
            "protocol": "neko-card-drop",
            "version": 1,
            "active_character": {
                "path": "/api/card-drop/active-character",
            },
            "facts": {
                "query_path": "/api/card-drop/facts/query",
                "method": "POST",
                "max_exclude_hashes": _FACT_QUERY_MAX_EXCLUSIONS,
            },
            "credits": {"authority": "cloud"},
            "delegate": {
                "scopes": sorted(_NATIVE_DELEGATE_SCOPES),
                "principal_header": "x-neko-local-user-id",
            },
        },
        headers=cors,
    )


@router.options("/facts", summary="社区读取本地 NEKO 记忆候选预检")
@router.options("/facts/query", summary="社区读取本地 NEKO 记忆候选预检")
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
    return await _forge_facts_response(
        request,
        query={
            "runtime_character_hint": runtime_character_hint,
            "min_importance": min_importance,
            "include_absorbed": include_absorbed,
            "limit": limit,
            "exclude_fact_ids": exclude_fact_ids,
            "exclude_hashes": exclude_hashes,
        },
    )


@router.post("/facts/query", summary="社区铸造：以有界 JSON 查询本地记忆候选")
async def forge_facts_query_endpoint(request: Request, payload: dict = Body(...)):
    cors = _facts_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    try:
        query = _validate_facts_query(payload)
    except ValueError as exc:
        return JSONResponse(
            {"detail": str(exc)},
            status_code=422,
            headers=cors,
        )
    return await _forge_facts_response(request, query=query)


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


@router.options("/credits")
@router.options("/credits/grant")
@router.options("/credits/local-summary")
@router.options("/credits/{credit_id}/reservations")
@router.options("/credits/{credit_id}/reservations/{operation_id}/commit")
@router.options("/credits/{credit_id}/reservations/{operation_id}")
async def credit_options(request: Request, credit_id: str = "", operation_id: str = ""):
    cors = _credit_cors_headers(request)
    if cors is None:
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)
    return JSONResponse({"ok": True}, headers=cors)


@router.post("/credits/grant", summary="本地发券已退役")
async def grant_credit_endpoint(request: Request):
    return JSONResponse(
        {"detail": "cloud_forge_credits_required"},
        status_code=410,
        headers=_credit_cors_headers(request) or {},
    )


@router.get("/credits", summary="本地券账本已退役")
async def credits_endpoint(request: Request):
    cors = _credit_cors_headers(request) or {}
    return JSONResponse(
        {"detail": "cloud_forge_credits_required"}, status_code=410, headers=cors
    )


@router.get("/credits/local-summary", summary="已退役：券状态改由云端推送")
async def local_credit_summary_endpoint(request: Request):
    return JSONResponse(
        {"detail": "cloud_forge_credits_required"},
        status_code=410,
        headers=_credit_cors_headers(request) or {},
    )


@router.post("/credits/{credit_id}/reservations", summary="已退役：云端事务直接锁券")
async def reserve_credit_endpoint(request: Request, credit_id: str):
    return JSONResponse(
        {"detail": "cloud_forge_credits_required"},
        status_code=410,
        headers=_credit_cors_headers(request) or {},
    )


@router.post("/credits/{credit_id}/reservations/{operation_id}/commit", summary="已退役：云端事务原子消费券")
async def commit_credit_endpoint(
    request: Request, credit_id: str, operation_id: str,
):
    return JSONResponse(
        {"detail": "cloud_forge_credits_required"},
        status_code=410,
        headers=_credit_cors_headers(request) or {},
    )


@router.delete("/credits/{credit_id}/reservations/{operation_id}", summary="已退役：失败事务自动回滚券")
async def release_credit_endpoint(request: Request, credit_id: str, operation_id: str):
    return JSONResponse(
        {"detail": "cloud_forge_credits_required"},
        status_code=410,
        headers=_credit_cors_headers(request) or {},
    )
