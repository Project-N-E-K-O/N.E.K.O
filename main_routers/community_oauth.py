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

logger = logging.getLogger("neko.community_oauth")

router = APIRouter(prefix="/api/card-drop", tags=["community-oauth"])
callback_router = APIRouter(tags=["community-oauth"])

_OAUTH_SCOPE = "openid email profile offline"
_OAUTH_PENDING_FILENAME = "community_oauth_pending.json"
_OAUTH_PENDING_TTL_SEC = 600
_OAUTH_REDIRECT_PATH = "/oauth/callback"
_DEFAULT_DESKTOP_CLIENT_ID = "neko-servers-desktop-dev"
_DEFAULT_AUTH_URL = "https://auth.project-neko.cn"
_HTTP_TIMEOUT_SEC = 30.0
_BIND_OWNERSHIP_CONFLICT = "client_already_bound_to_other_user"

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


@router.post("/oauth/start", summary="启动社区统一账号 OAuth（Desktop PKCE）")
async def oauth_start_endpoint(request: Request):
    if not C._local_request_source_allowed(request):
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)

    auth_url_base = _auth_public_url()
    if not auth_url_base:
        raise HTTPException(status_code=400, detail="auth_url_not_configured")

    client_id = _desktop_client_id()
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _pkce_s256_challenge(code_verifier)
    expires_at = time.time() + _OAUTH_PENDING_TTL_SEC
    redirect_uri = _oauth_redirect_uri(request)
    pending_path = _oauth_pending_path()
    if pending_path is None:
        raise HTTPException(status_code=503, detail="oauth_pending_unavailable")

    try:
        C._write_private_json(
            pending_path,
            {
                "state": state,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "auth_public_url": auth_url_base,
                "created_at": time.time(),
                "expires_at": expires_at,
            },
        )
    except OSError as exc:
        logger.warning("community_oauth: failed to persist pending: %s", exc)
        raise HTTPException(status_code=503, detail="oauth_pending_unavailable") from exc

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
        "expires_in": _OAUTH_PENDING_TTL_SEC,
    }


@router.get("/oauth/status", summary="社区 OAuth 本地登录状态（不含 token）")
async def oauth_status_endpoint(request: Request):
    if not C._local_request_source_allowed(request):
        return JSONResponse({"detail": "origin_not_allowed"}, status_code=403)

    snapshot = C._desktop_session_snapshot()
    auth = C._load_auth() or {}
    if not snapshot or not snapshot.get("access_token"):
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

    snapshot = C._desktop_session_snapshot() or {}
    auth = C._load_auth() or {}
    client_id = (
        str(auth.get("client_id") or "").strip()
        or str((C._load_social_session() or {}).get("client_id") or "").strip()
        or _desktop_client_id()
    )
    await _revoke_tokens_best_effort(
        access_token=snapshot.get("access_token"),
        refresh_token=snapshot.get("refresh_token"),
        client_id=client_id,
        auth_public_url=_auth_public_url(),
    )
    _unlink_pending()
    if not C._clear_auth():
        raise HTTPException(status_code=500, detail="local_clear_failed")
    return {"ok": True}


async def _handle_oauth_callback(
    code: str | None,
    state: str | None,
    error: str | None = None,
) -> HTMLResponse:
    pending_path = _oauth_pending_path()
    pending = C._read_json_dict(pending_path) if pending_path else None
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
        _unlink_pending()
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
        _unlink_pending()
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
        _unlink_pending()
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
        _unlink_pending()
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
        _unlink_pending()
        detail = str(exc.detail) if exc.detail else "换取登录凭证失败"
        return _callback_html("登录失败", detail, status_code=400)

    access_token = str(token_payload.get("access_token") or "").strip()
    refresh_token = str(token_payload.get("refresh_token") or "").strip() or None
    if not access_token:
        _unlink_pending()
        return _callback_html(
            "登录失败",
            "Auth 未返回有效 access token。",
            status_code=400,
        )

    social_base = C._social_base_url()
    try:
        bootstrap = await _bootstrap_session(social_base, access_token)
    except HTTPException as exc:
        _unlink_pending()
        detail = str(exc.detail) if exc.detail else "无法建立社区会话"
        return _callback_html("登录失败", detail, status_code=400)

    user = bootstrap.get("user") if isinstance(bootstrap.get("user"), dict) else {}
    local_user_id = C._normalize_local_user_id(user.get("id"))
    if not local_user_id:
        _unlink_pending()
        return _callback_html(
            "登录失败",
            "社区身份响应无效。",
            status_code=400,
        )

    bind = await _oauth_guest_bind(social_base, access_token)
    if bind.get("error") == _BIND_OWNERSHIP_CONFLICT:
        _unlink_pending()
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
    auth_saved = await asyncio.to_thread(C._save_auth, auth_payload)
    social_saved = await asyncio.to_thread(
        C._save_social_session,
        social_base,
        access_token,
        refresh_token,
        local_user_id=local_user_id,
        auth_source="oauth",
        auth_public_url=auth_public_url,
        client_id=client_id,
    )
    _unlink_pending()
    if not (auth_saved and social_saved):
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
    credentials = C._get_client_credentials()
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

            approval_res = await client.post(
                f"{base}/api/clients/bind-approval",
                json={
                    "client_id": client_id,
                    "client_proof": client_proof,
                    "binding_challenge": challenge,
                },
            )
            if approval_res.status_code >= 400:
                try:
                    bind["error"] = (
                        approval_res.json().get("detail")
                        or f"http_{approval_res.status_code}"
                    )
                except (ValueError, TypeError, AttributeError):
                    bind["error"] = f"http_{approval_res.status_code}"
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
