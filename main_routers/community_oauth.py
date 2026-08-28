"""Desktop community OAuth (neko-auth-platform → N.E.K.O.Servers).

Owns PKCE for ``neko-servers-desktop-{env}`` with loopback callback
``http://127.0.0.1:<port>/oauth/callback``. Market's ``neko-desktop`` client and
``/market/oauth/*`` paths are intentionally separate.
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
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

import main_routers.card_drop_router as C
from main_logic import client_registration

logger = logging.getLogger("neko.community_oauth")

router = APIRouter(prefix="/api/card-drop", tags=["community-oauth"])
callback_router = APIRouter(tags=["community-oauth"])

_OAUTH_SCOPE = "openid email profile offline"
_OAUTH_PENDING_FILENAME = "community_oauth_pending.json"
_OAUTH_PENDING_TTL_SEC = 600
_OAUTH_REDIRECT_PATH = "/oauth/callback"
_DEFAULT_DESKTOP_CLIENT_ID = "neko-servers-desktop-prod"
_DEFAULT_AUTH_URL = "https://auth.project-neko.cn"
_HTTP_TIMEOUT_SEC = 30.0
_BIND_OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"
_oauth_start_lock = asyncio.Lock()
_oauth_status_lock = asyncio.Lock()
_oauth_status_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}

_CALLBACK_PAGE = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>{title}</title>
    <style>
      body {{
        font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI",
          "PingFang SC", sans-serif;
        background: #0f0f1a;
        color: #f8fafc;
        display: grid;
        min-height: 100vh;
        place-items: center;
        margin: 0;
      }}
      main {{
        max-width: 520px;
        padding: 32px;
        border: 1px solid rgba(148, 163, 184, 0.24);
        border-radius: 18px;
        background: rgba(26, 26, 46, 0.92);
        text-align: center;
      }}
      h1 {{ font-size: 1.35rem; margin: 0 0 12px; }}
      p {{ color: #cbd5e1; line-height: 1.7; margin: 0; }}
    </style>
  </head>
  <body>
    <main>
      <h1>{title}</h1>
      <p>{message}</p>
    </main>
  </body>
</html>
"""


def _desktop_client_id() -> str:
    raw = (os.environ.get("NEKO_SERVERS_DESKTOP_CLIENT_ID") or "").strip()
    if raw and raw != "neko-desktop":
        return raw
    return _DEFAULT_DESKTOP_CLIENT_ID


def _auth_public_url() -> str:
    raw = (os.environ.get("NEKO_AUTH_URL") or "").strip().rstrip("/")
    if raw:
        return raw
    return _DEFAULT_AUTH_URL


def _main_server_port() -> int:
    try:
        import config

        return int(config.MAIN_SERVER_PORT)
    except Exception:  # noqa: BLE001
        return 48911


def _pkce_s256_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _oauth_redirect_uri(request: Request | None = None) -> str:
    port: int | None = None
    if request is not None:
        port = request.url.port
    if port is None:
        port = _main_server_port()
    return f"http://127.0.0.1:{port}{_OAUTH_REDIRECT_PATH}"


def _oauth_pending_path() -> Path | None:
    auth_path = C._auth_path()
    if auth_path is not None:
        return auth_path.parent / _OAUTH_PENDING_FILENAME
    social = C._social_session_path()
    if social is not None:
        return social.parent / _OAUTH_PENDING_FILENAME
    return None


def _callback_html(title: str, message: str, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _CALLBACK_PAGE.format(
            title=html.escape(title),
            message=html.escape(message),
        ),
        status_code=status_code,
    )


def _unlink_pending() -> None:
    path = _oauth_pending_path()
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("community_oauth: pending unlink failed: %s", exc)


def _load_oauth_status_records() -> tuple[dict | None, dict]:
    """Read the status snapshot together on a worker thread."""
    return C._desktop_session_snapshot(), C._load_auth() or {}


# The refresh rewrites the authoritative session file and the auth mirror
# without a shared lock, so these mirror fields trail the snapshot for a moment.
_AUTH_MIRROR_REFRESHED_FIELDS = (
    "access_token",
    "refresh_token",
    "session_generation",
    "schema_version",
)


def _oauth_status_records_key(snapshot: dict | None, auth: dict) -> str:
    """Fingerprint one credential snapshot without retaining tokens as map keys.

    The mirror's own token fields stay out of the fingerprint: a status read
    that lands between the two writes of a refresh would otherwise fingerprint
    an old-session/new-mirror pair that never existed, miss the in-flight task,
    and resolve the already-rotated refresh token a second time. The snapshot
    still carries the authoritative tokens, so a real credential change (login,
    logout, account switch) keeps producing a different key.
    """
    mirror = {
        key: value
        for key, value in (auth or {}).items()
        if key not in _AUTH_MIRROR_REFRESHED_FIELDS
    }
    encoded = json.dumps(
        {"snapshot": snapshot, "auth": mirror},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _status_snapshot_matches(current: dict | None, expected: dict) -> bool:
    if current is None:
        return False
    return all(
        current.get(field) == expected.get(field)
        for field in ("base_url", "access_token", "refresh_token", "local_user_id")
    )


def _persist_refreshed_oauth_tokens(
    expected: dict,
    access_token: str,
    refresh_token: str,
) -> bool:
    """CAS-update both local OAuth records without rolling back a newer session."""
    social_path = C._social_session_path()
    if social_path is None:
        return False
    try:
        with C._social_session_lock(social_path):
            social = C._read_json_dict(social_path)
            current = C._desktop_session_snapshot()
            if not social or not _status_snapshot_matches(current, expected):
                return False
            generation = int(social.get("session_generation") or 0) + 1
            C._write_private_json(
                social_path,
                {
                    **social,
                    "schema_version": C._SOCIAL_SESSION_SCHEMA_VERSION,
                    "token": access_token,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "session_generation": generation,
                },
            )
    except (OSError, TimeoutError, ValueError, TypeError) as exc:
        logger.warning("community_oauth: refreshed social session save failed: %s", exc)
        return False

    auth_path = C._auth_path()
    if auth_path is None:
        return True
    try:
        auth = C._read_json_dict(auth_path)
        if auth and str(auth.get("access_token") or "").strip() == str(
            expected.get("access_token") or ""
        ).strip():
            C._write_private_json(
                auth_path,
                {
                    **auth,
                    "schema_version": C._SOCIAL_SESSION_SCHEMA_VERSION,
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "session_generation": int(auth.get("session_generation") or 0) + 1,
                },
            )
    except (OSError, ValueError, TypeError) as exc:
        # The Electron-visible social session is authoritative.  A stale legacy
        # mirror must not make a successful refresh look logged out.
        logger.warning("community_oauth: refreshed auth mirror save failed: %s", exc)
    return True


def _clear_rejected_oauth_snapshot(expected: dict) -> bool:
    """Clear only the rejected credential snapshot; preserve a concurrent login."""
    success = True
    social_path = C._social_session_path()
    if social_path is not None:
        try:
            with C._social_session_lock(social_path):
                social = C._read_json_dict(social_path)
                current_access = str((social or {}).get("token") or "").strip()
                if current_access == str(expected.get("access_token") or "").strip():
                    social_path.unlink(missing_ok=True)
        except (OSError, TimeoutError):
            success = False
    auth_path = C._auth_path()
    if auth_path is not None:
        try:
            auth = C._read_json_dict(auth_path)
            if str((auth or {}).get("access_token") or "").strip() == str(
                expected.get("access_token") or ""
            ).strip():
                auth_path.unlink(missing_ok=True)
        except OSError:
            success = False
    return success


async def _refresh_oauth_token(
    *,
    refresh_token: str,
    client_id: str,
    auth_public_url: str,
) -> tuple[str, dict[str, Any] | None]:
    """Return ``ok``, ``rejected``, or ``unavailable`` for a refresh grant."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT_SEC)) as client:
            response = await client.post(
                f"{auth_public_url.rstrip('/')}/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                },
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                },
            )
    except httpx.HTTPError:
        return "unavailable", None

    try:
        payload = response.json()
    except (ValueError, TypeError):
        payload = None
    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        return "unavailable", None
    if response.status_code >= 400:
        if isinstance(payload, dict) and payload.get("error") == "invalid_grant":
            return "rejected", payload
        return "unavailable", None
    if not isinstance(payload, dict) or not str(payload.get("access_token") or "").strip():
        return "unavailable", None
    return "ok", payload


async def _resolve_saved_oauth_status(
    _attempt: int = 0,
    _records: tuple[dict | None, dict] | None = None,
) -> dict[str, Any]:
    """Validate and, for expired OAuth credentials, refresh the saved session."""
    if _records is None:
        snapshot, auth = await asyncio.to_thread(_load_oauth_status_records)
    else:
        snapshot, auth = _records
    if not snapshot or not snapshot.get("access_token"):
        return {"logged_in": False, "snapshot": None, "auth": auth}

    lookup = await C._lookup_cloud_identity(
        str(snapshot.get("base_url") or C._social_base_url()).rstrip("/"),
        str(snapshot["access_token"]),
    )
    if lookup.identity is not None:
        return {"logged_in": True, "snapshot": snapshot, "auth": auth}
    if lookup.failure != "rejected":
        # Do not erase a usable offline session on network/service failures, but
        # also do not claim that an unvalidated bearer is currently logged in.
        return {"logged_in": False, "snapshot": snapshot, "auth": auth}

    refresh_token = str(snapshot.get("refresh_token") or "").strip()
    auth_public_url = str(
        snapshot.get("auth_public_url")
        or auth.get("auth_public_url")
        or _auth_public_url()
    ).strip().rstrip("/")
    client_id = str(
        snapshot.get("client_id")
        or auth.get("client_id")
        or _desktop_client_id()
    ).strip()
    if (
        snapshot.get("auth_source") == "oauth"
        and refresh_token
        and auth_public_url
        and client_id
    ):
        outcome, payload = await _refresh_oauth_token(
            refresh_token=refresh_token,
            client_id=client_id,
            auth_public_url=auth_public_url,
        )
        if outcome == "ok" and payload is not None:
            access_token = str(payload.get("access_token") or "").strip()
            rotated_refresh = str(payload.get("refresh_token") or "").strip() or refresh_token
            saved = await asyncio.to_thread(
                _persist_refreshed_oauth_tokens,
                snapshot,
                access_token,
                rotated_refresh,
            )
            if saved:
                refreshed_snapshot, refreshed_auth = await asyncio.to_thread(
                    _load_oauth_status_records
                )
                refreshed_expected = {
                    **snapshot,
                    "access_token": access_token,
                    "refresh_token": rotated_refresh,
                }
                if _status_snapshot_matches(refreshed_snapshot, refreshed_expected):
                    return {
                        "logged_in": True,
                        "snapshot": refreshed_snapshot,
                        "auth": refreshed_auth,
                    }
                if refreshed_snapshot and _attempt < 2:
                    return await _resolve_saved_oauth_status(_attempt + 1)
                return {
                    "logged_in": False,
                    "snapshot": refreshed_snapshot,
                    "auth": refreshed_auth,
                }
            # A concurrent account switch won the CAS. Resolve the winner.
            current, current_auth = await asyncio.to_thread(_load_oauth_status_records)
            if (
                current
                and not _status_snapshot_matches(current, snapshot)
                and _attempt < 2
            ):
                return await _resolve_saved_oauth_status(_attempt + 1)
            return {"logged_in": False, "snapshot": current, "auth": current_auth}
        if outcome == "unavailable":
            return {"logged_in": False, "snapshot": snapshot, "auth": auth}

    cleared = await asyncio.to_thread(_clear_rejected_oauth_snapshot, snapshot)
    if cleared:
        return {"logged_in": False, "snapshot": None, "auth": {}}

    logger.warning("community_oauth: rejected credential cleanup did not complete")
    current, current_auth = await asyncio.to_thread(_load_oauth_status_records)
    if (
        current
        and not _status_snapshot_matches(current, snapshot)
        and _attempt < 2
    ):
        # A concurrent login replaced the rejected snapshot while cleanup ran.
        return await _resolve_saved_oauth_status(_attempt + 1)
    return {
        "logged_in": False,
        "snapshot": current or snapshot,
        "auth": current_auth or auth,
    }


async def _run_oauth_status_resolution(
    records_key: str,
    records: tuple[dict | None, dict],
) -> dict[str, Any]:
    try:
        return await _resolve_saved_oauth_status(_records=records)
    finally:
        async with _oauth_status_lock:
            if _oauth_status_tasks.get(records_key) is asyncio.current_task():
                _oauth_status_tasks.pop(records_key, None)


async def resolve_saved_oauth_status() -> dict[str, Any]:
    """Share one refresh-capable saved-session resolution at a time.

    A browser abandoning its local request must not cancel the underlying
    cloud validation. Concurrent callers await the same shielded task, so an
    auth-status fallback cannot queue behind and then repeat a slow delegate
    validation.
    """
    records = await asyncio.to_thread(_load_oauth_status_records)
    records_key = _oauth_status_records_key(*records)
    async with _oauth_status_lock:
        task = _oauth_status_tasks.get(records_key)
        if task is None or task.done():
            task = asyncio.create_task(
                _run_oauth_status_resolution(records_key, records)
            )
            _oauth_status_tasks[records_key] = task
    return await asyncio.shield(task)


def _load_oauth_logout_records() -> tuple[dict, dict, dict]:
    """Read all logout inputs together on a worker thread."""
    return (
        C._desktop_session_snapshot() or {},
        C._load_auth() or {},
        C._load_social_session() or {},
    )


def _load_oauth_pending() -> tuple[Path | None, dict | None]:
    """Resolve and read the pending OAuth record on a worker thread."""
    path = _oauth_pending_path()
    return path, C._read_json_dict(path) if path else None


def _persist_oauth_credentials(
    auth_payload: dict[str, Any],
    *,
    social_base: str,
    access_token: str,
    refresh_token: str | None,
    local_user_id: str,
    auth_public_url: str,
    client_id: str,
) -> bool:
    """Persist both OAuth credential files or restore their previous state."""
    auth_path = C._auth_path()
    social_path = C._social_session_path()
    if auth_path is None or social_path is None:
        return False

    snapshots: list[tuple[Path, bool, dict[str, Any] | None]] = []
    try:
        for path in (auth_path, social_path):
            existed = path.exists()
            payload = C._read_json_dict(path) if existed else None
            if existed and payload is None:
                logger.warning(
                    "community_oauth: refusing to replace unreadable credential file: %s",
                    path.name,
                )
                return False
            snapshots.append((path, existed, payload))
    except OSError as exc:
        logger.warning("community_oauth: credential snapshot failed: %s", exc)
        return False

    auth_saved = C._save_auth(auth_payload)
    social_saved = auth_saved and C._save_social_session(
        social_base,
        access_token,
        refresh_token,
        local_user_id=local_user_id,
        auth_source="oauth",
        auth_public_url=auth_public_url,
        client_id=client_id,
    )
    if auth_saved and social_saved:
        return True

    rollback_ok = True
    for path, existed, payload in snapshots:
        # _save_social_session() either atomically replaced the social file and
        # returned True, or left it untouched. Reaching rollback therefore
        # means only community_auth.json may need restoration; rewriting the
        # social snapshot here could overwrite a concurrent Desktop refresh.
        if path == social_path:
            continue
        try:
            if existed:
                C._write_private_json(path, payload or {})
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            rollback_ok = False
            logger.warning(
                "community_oauth: credential rollback failed for %s: %s",
                path.name,
                exc,
            )
    if not rollback_ok and not C._clear_auth():
        logger.warning("community_oauth: failed to clear credentials after rollback failure")
    return False


@router.post("/oauth/start", summary="启动社区统一账号 OAuth（Desktop PKCE）")
async def oauth_start_endpoint(request: Request):
    if not C._local_request_source_allowed(request):
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)

    auth_url_base = _auth_public_url()
    if not auth_url_base:
        raise HTTPException(status_code=400, detail="auth_url_not_configured")

    client_id = _desktop_client_id()
    redirect_uri = _oauth_redirect_uri(request)
    pending_path = await asyncio.to_thread(_oauth_pending_path)
    if pending_path is None:
        raise HTTPException(status_code=503, detail="oauth_pending_unavailable")

    reused_pending = False
    async with _oauth_start_lock:
        now = time.time()
        pending = await asyncio.to_thread(C._read_json_dict, pending_path)
        try:
            pending_expires_at = float((pending or {}).get("expires_at") or 0)
        except (TypeError, ValueError):
            pending_expires_at = 0.0
        pending_state = str((pending or {}).get("state") or "")
        pending_verifier = str((pending or {}).get("code_verifier") or "")
        if (
            pending_expires_at > now
            and pending_state
            and pending_verifier
            and str((pending or {}).get("redirect_uri") or "") == redirect_uri
            and str((pending or {}).get("client_id") or "") == client_id
            and str((pending or {}).get("auth_public_url") or "").rstrip("/")
            == auth_url_base
        ):
            state = pending_state
            code_verifier = pending_verifier
            expires_at = pending_expires_at
            reused_pending = True
        else:
            state = secrets.token_urlsafe(32)
            code_verifier = secrets.token_urlsafe(64)
            expires_at = now + _OAUTH_PENDING_TTL_SEC
            try:
                await asyncio.to_thread(
                    C._write_private_json,
                    pending_path,
                    {
                        "state": state,
                        "code_verifier": code_verifier,
                        "redirect_uri": redirect_uri,
                        "client_id": client_id,
                        "auth_public_url": auth_url_base,
                        "created_at": now,
                        "expires_at": expires_at,
                    },
                )
            except OSError as exc:
                logger.warning("community_oauth: failed to persist pending: %s", exc)
                raise HTTPException(
                    status_code=503,
                    detail="oauth_pending_unavailable",
                ) from exc

    code_challenge = _pkce_s256_challenge(code_verifier)
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "response_type": "code",
            "scope": _OAUTH_SCOPE,
        }
    )
    auth_url = f"{auth_url_base}/oauth2/auth?{query}"
    return {
        "auth_url": auth_url,
        "state": state,
        "expires_in": (
            max(1, int(expires_at - time.time()))
            if reused_pending
            else _OAUTH_PENDING_TTL_SEC
        ),
    }


@router.get("/oauth/status", summary="社区 OAuth 本地登录状态（不含 token）")
async def oauth_status_endpoint(request: Request):
    if not C._local_request_source_allowed(request):
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)

    status = await resolve_saved_oauth_status()
    snapshot = status["snapshot"]
    auth = status["auth"]
    if not status["logged_in"] or not snapshot:
        return {
            "logged_in": False,
            "auth_source": None,
            "local_user_id": None,
            "user": None,
        }
    user = auth.get("user") if isinstance(auth.get("user"), dict) else {}
    return {
        "logged_in": True,
        "auth_source": snapshot.get("auth_source") or None,
        "local_user_id": snapshot.get("local_user_id") or None,
        "user": {
            "display_name": user.get("display_name"),
            "email": user.get("email"),
        },
    }


@router.post("/oauth/logout", summary="清除社区 OAuth 本地会话（best-effort revoke）")
async def oauth_logout_endpoint(request: Request):
    if not C._local_request_source_allowed(request):
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)

    snapshot, auth, social = await asyncio.to_thread(_load_oauth_logout_records)
    client_id = (
        str(auth.get("client_id") or "").strip()
        or str(social.get("client_id") or "").strip()
        or _desktop_client_id()
    )
    auth_public_url = str(
        snapshot.get("auth_public_url")
        or auth.get("auth_public_url")
        or social.get("auth_public_url")
        or _auth_public_url()
    ).strip().rstrip("/")
    await _revoke_tokens_best_effort(
        access_token=snapshot.get("access_token"),
        refresh_token=snapshot.get("refresh_token"),
        client_id=client_id,
        auth_public_url=auth_public_url,
    )
    await asyncio.to_thread(_unlink_pending)
    if not await asyncio.to_thread(C._clear_auth):
        raise HTTPException(status_code=500, detail="local_clear_failed")
    return {"ok": True}


async def _handle_oauth_callback(
    code: str | None,
    state: str | None,
    error: str | None = None,
) -> HTMLResponse:
    _pending_path, pending = await asyncio.to_thread(_load_oauth_pending)
    if not pending:
        return _callback_html(
            "登录尚未开始",
            "请回到 NEKO 重新点击社区登录。",
            status_code=400,
        )

    try:
        expires_at = float(pending.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    if time.time() > expires_at:
        await asyncio.to_thread(_unlink_pending)
        return _callback_html(
            "登录已过期",
            "请回到 NEKO 重新点击社区登录。",
            status_code=400,
        )

    expected_state = str(pending.get("state") or "")
    if not expected_state or not state or not secrets.compare_digest(state, expected_state):
        return _callback_html(
            "登录校验失败",
            "OAuth state 不匹配，请回到 NEKO 重试。",
            status_code=400,
        )

    if error:
        await asyncio.to_thread(_unlink_pending)
        if error == "access_denied":
            return _callback_html(
                "登录已取消",
                "你已取消社区登录，可关闭此页并回到 NEKO。",
                status_code=400,
            )
        return _callback_html(
            "登录未完成",
            "Auth 未完成授权，请回到 NEKO 重试。",
            status_code=400,
        )

    if not code:
        await asyncio.to_thread(_unlink_pending)
        return _callback_html(
            "登录未完成",
            "Auth 未返回授权码，请回到 NEKO 重试。",
            status_code=400,
        )

    code_verifier = str(pending.get("code_verifier") or "")
    redirect_uri = str(pending.get("redirect_uri") or _oauth_redirect_uri())
    client_id = str(pending.get("client_id") or _desktop_client_id())
    auth_public_url = str(pending.get("auth_public_url") or _auth_public_url()).rstrip("/")
    if not code_verifier:
        await asyncio.to_thread(_unlink_pending)
        return _callback_html(
            "登录数据不完整",
            "请回到 NEKO 重新点击社区登录。",
            status_code=400,
        )

    try:
        token_payload = await _exchange_oauth_code(
            code=code,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            client_id=client_id,
            auth_public_url=auth_public_url,
        )
    except HTTPException as exc:
        await asyncio.to_thread(_unlink_pending)
        detail = str(exc.detail) if exc.detail else "换取登录凭证失败"
        return _callback_html("登录失败", detail, status_code=400)

    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    if not access_token:
        await asyncio.to_thread(_unlink_pending)
        return _callback_html(
            "登录失败",
            "Auth 未返回有效 access token。",
            status_code=400,
        )

    social_base = C._social_base_url()
    try:
        bootstrap = await _bootstrap_session(social_base, access_token)
    except HTTPException as exc:
        await asyncio.to_thread(_unlink_pending)
        detail = str(exc.detail) if exc.detail else "无法建立社区会话"
        return _callback_html("登录失败", detail, status_code=400)

    user = bootstrap.get("user") if isinstance(bootstrap.get("user"), dict) else {}
    local_user_id = C._normalize_local_user_id(user.get("id"))
    if not local_user_id:
        await asyncio.to_thread(_unlink_pending)
        return _callback_html(
            "登录失败",
            "社区身份响应无效。",
            status_code=400,
        )

    bind = await _oauth_guest_bind(social_base, access_token)
    if bind.get("error") == _BIND_OWNERSHIP_CONFLICT:
        await asyncio.to_thread(_unlink_pending)
        return _callback_html(
            "登录冲突",
            "这台设备已经绑定其他社区账号，本次登录未生效；原登录状态保持不变。",
            status_code=400,
        )

    auth_payload = {
        "schema_version": C._SOCIAL_SESSION_SCHEMA_VERSION,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "local_user_id": local_user_id,
        "auth_source": "oauth",
        "auth_public_url": auth_public_url,
        "client_id": client_id,
        "user": {
            "id": local_user_id,
            "display_name": user.get("display_name"),
            "email": user.get("email"),
        },
        "bind": bind,
    }
    credentials_saved = await asyncio.to_thread(
        _persist_oauth_credentials,
        auth_payload,
        social_base=social_base,
        access_token=access_token,
        refresh_token=refresh_token,
        local_user_id=local_user_id,
        auth_public_url=auth_public_url,
        client_id=client_id,
    )
    await asyncio.to_thread(_unlink_pending)
    if not credentials_saved:
        return _callback_html(
            "登录未完成",
            "凭证未能完成本地保存，请回到 NEKO 重试。",
            status_code=400,
        )

    return _callback_html(
        "社区登录已完成",
        "可关闭此页，回到 NEKO 继续使用社区功能。",
        status_code=200,
    )


@callback_router.get("/oauth/callback", response_class=HTMLResponse)
async def oauth_callback_endpoint(
    code: str | None = Query(None, min_length=1),
    state: str | None = Query(None, min_length=1),
    error: str | None = Query(None, min_length=1, max_length=100),
):
    return await _handle_oauth_callback(code, state, error)


@callback_router.get("/api/card-drop/oauth/callback", response_class=HTMLResponse)
async def oauth_callback_alias_endpoint(
    code: str | None = Query(None, min_length=1),
    state: str | None = Query(None, min_length=1),
    error: str | None = Query(None, min_length=1, max_length=100),
):
    """Alias kept for logger redaction parity; primary Hydra URI is ``/oauth/callback``."""
    return await _handle_oauth_callback(code, state, error)


async def _exchange_oauth_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_id: str,
    auth_public_url: str,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT_SEC)) as client:
            response = await client.post(
                f"{auth_public_url.rstrip('/')}/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": redirect_uri,
                },
                headers={
                    "accept": "application/json",
                    "content-type": "application/x-www-form-urlencoded",
                },
            )
    except httpx.HTTPError as exc:
        logger.info("community_oauth: token exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="无法连接 Auth OAuth 服务") from exc

    if response.status_code >= 400:
        logger.info("community_oauth: token exchange rejected: %s", response.status_code)
        raise HTTPException(status_code=400, detail="Auth OAuth token 交换失败")

    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="Auth OAuth token 响应无效") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        raise HTTPException(status_code=502, detail="Auth OAuth token 响应无效")
    return data


async def _bootstrap_session(social_base: str, access_token: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT_SEC)) as client:
            response = await client.post(
                f"{social_base.rstrip('/')}/api/auth/session/bootstrap",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        logger.info("community_oauth: bootstrap failed: %s", exc)
        raise HTTPException(status_code=502, detail="无法连接社区服务") from exc

    if response.status_code >= 400:
        try:
            detail = response.json().get("detail") or f"http_{response.status_code}"
        except (ValueError, TypeError, AttributeError):
            detail = f"http_{response.status_code}"
        raise HTTPException(status_code=400, detail=str(detail))

    try:
        data = response.json()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail="社区身份响应无效") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="社区身份响应无效")
    return data


async def _oauth_guest_bind(social_base: str, access_token: str) -> dict[str, Any]:
    """Best-effort OAuth guest migration via binding challenge.

    Ownership conflicts must surface to the caller; other failures are recorded
    but do not abort the login.
    """
    bind: dict[str, Any] = {"bound": False, "error": None}
    credentials = await asyncio.to_thread(C._get_client_credentials)
    if not credentials:
        bind["error"] = "client_not_registered"
        return bind
    client_id, client_proof = credentials
    headers = {"Authorization": f"Bearer {access_token}"}
    base = social_base.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(_HTTP_TIMEOUT_SEC)) as client:
            challenge_res = await client.post(
                f"{base}/api/auth/bind-client/challenge",
                headers=headers,
                json={"client_id": client_id},
            )
            if challenge_res.status_code >= 400:
                try:
                    bind["error"] = (
                        challenge_res.json().get("detail")
                        or f"http_{challenge_res.status_code}"
                    )
                except (ValueError, TypeError, AttributeError):
                    bind["error"] = f"http_{challenge_res.status_code}"
                return bind

            try:
                challenge_body = challenge_res.json()
            except (ValueError, TypeError):
                challenge_body = None
            if not isinstance(challenge_body, dict):
                bind["error"] = "invalid_client_binding_challenge"
                return bind
            challenge = str(challenge_body.get("binding_challenge") or "").strip()
            if not challenge:
                bind["error"] = "invalid_client_binding_challenge"
                return bind

            approval_payload = {
                "client_id": client_id,
                "client_proof": client_proof,
                "binding_challenge": challenge,
            }

            async def _approve():
                res = await client.post(
                    f"{base}/api/clients/bind-approval", json=approval_payload
                )
                try:
                    detail = res.json().get("detail")
                except (ValueError, TypeError, AttributeError):
                    detail = None
                return res, detail

            approval_res, approval_detail = await _approve()
            # First cloud call from this install can predate registration; the
            # bind-approval 403 is indistinguishable from a stale proof, so
            # register and retry exactly once before surfacing the failure.
            if client_registration.looks_unregistered(
                approval_res.status_code, approval_detail
            ) and await client_registration.ensure_client_registered(base, force=True):
                approval_res, approval_detail = await _approve()

            if approval_res.status_code >= 400:
                bind["error"] = approval_detail or f"http_{approval_res.status_code}"
                return bind

            bind_res = await client.post(
                f"{base}/api/auth/bind-client",
                headers=headers,
                json={
                    "client_id": client_id,
                    "binding_challenge": challenge,
                },
            )
            if bind_res.status_code < 400:
                bind["bound"] = True
                return bind
            try:
                bind["error"] = bind_res.json().get("detail") or f"http_{bind_res.status_code}"
            except (ValueError, TypeError, AttributeError):
                bind["error"] = f"http_{bind_res.status_code}"
    except (httpx.HTTPError, OSError) as exc:
        bind["error"] = "cloud_unreachable"
        logger.info("community_oauth: guest bind failed: %s", exc)
    return bind


async def _revoke_tokens_best_effort(
    *,
    access_token: str | None,
    refresh_token: str | None,
    client_id: str,
    auth_public_url: str,
) -> None:
    if not auth_public_url:
        return
    tokens = [
        ("refresh_token", refresh_token),
        ("access_token", access_token),
    ]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            for token_type_hint, token_value in tokens:
                if not isinstance(token_value, str) or not token_value:
                    continue
                try:
                    await client.post(
                        f"{auth_public_url.rstrip('/')}/oauth2/revoke",
                        data={
                            "token": token_value,
                            "token_type_hint": token_type_hint,
                            "client_id": client_id,
                        },
                        headers={
                            "accept": "application/json",
                            "content-type": "application/x-www-form-urlencoded",
                        },
                    )
                except httpx.HTTPError as exc:
                    logger.debug(
                        "community_oauth: revoke failed for %s: %s",
                        token_type_hint,
                        exc,
                    )
    except httpx.HTTPError as exc:
        logger.debug("community_oauth: revoke client setup failed: %s", exc)
