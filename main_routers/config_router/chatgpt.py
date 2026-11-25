# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Loopback-only OAuth controls. Tokens never enter core_config or HTTP responses."""

import asyncio
import ipaddress
import threading

import httpx
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel

from utils.chatgpt_auth import ChatGPTAuthError, get_store
from utils.chatgpt_auth.login import LoginAttempt
from ._shared import logger, router

_attempt = None
_lock = threading.Lock()


def require_local_connection(request: Request, response: Response, message: str) -> None:
    """Refuse anything but a direct loopback request to a loopback host name.

    Cross-site writes are already rejected by the config router's mutation
    guard (same origin, JSON body); this adds the loopback-only rule, which
    also refuses remote instance sessions and DNS-rebound host names. Behind
    a proxy or in a remote deployment the peer address is the proxy's, so
    these controls are refused there, as for the community account routes.
    """
    from main_routers.local_access import is_local_oauth_status_request

    response.headers["Cache-Control"] = "no-store"
    try:
        local = request.client and ipaddress.ip_address(request.client.host).is_loopback
        host = request.url.hostname
        local_host = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local_host = False
        local = False
    if not local or not local_host or not is_local_oauth_status_request(request):
        raise HTTPException(403, message)


def local_settings(request: Request, response: Response):
    """Keep account controls on this machine: the OAuth callback is a loopback port."""
    require_local_connection(request, response, "ChatGPT account settings require a local connection.")


class AccountRequest(BaseModel):
    account_id: str | None = None


def _status():
    # Login state first: "connected" is set only after the account is saved,
    # so the account list read afterwards already includes it.
    attempt = _attempt
    login_status = attempt.status if attempt else "idle"
    error = attempt.error if attempt else ""
    result = get_store().status()
    result.update(login_status=login_status, error=error)
    return result


async def _safe_call(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except ChatGPTAuthError as exc:
        raise HTTPException(400, str(exc)) from None
    except (httpx.HTTPError, OSError):
        raise HTTPException(503, "ChatGPT connection failed. Please retry.") from None


@router.get("/chatgpt/status", dependencies=[Depends(local_settings)])
async def chatgpt_status():
    return await _safe_call(_status)


@router.post("/chatgpt/login", dependencies=[Depends(local_settings)])
async def chatgpt_login(body: AccountRequest):
    def start():
        global _attempt
        with _lock:
            if _attempt and not _attempt.done.is_set():
                raise ChatGPTAuthError(
                    "A ChatGPT sign-in is already pending. Complete it or wait five minutes."
                )
            _attempt = LoginAttempt(get_store(), body.account_id)
            return {"url": _attempt.url}

    return await _safe_call(start)


def _change_account(action, account_id):
    global _attempt
    with _lock:
        if _attempt and not _attempt.done.is_set():
            raise ChatGPTAuthError(
                "Complete the pending ChatGPT sign-in before changing accounts."
            )
        result = getattr(get_store(), action)(account_id)
        # A past failed/expired login must not mask a successful account change.
        _attempt = None
        return result


@router.post("/chatgpt/select", dependencies=[Depends(local_settings)])
async def chatgpt_select(body: AccountRequest):
    await _safe_call(_change_account, "select", body.account_id)
    return {"success": True}


@router.post("/chatgpt/logout", dependencies=[Depends(local_settings)])
async def chatgpt_logout(body: AccountRequest):
    """Sign one account out. The ChatGPT subscription keeps using the account
    that stays active; when none is left it is turned off, so the tiers it took
    go back to the user's own settings."""
    confirmed = await _safe_call(_change_account, "sign_out", body.account_id)
    subscription_off = await _safe_call(_turn_off_subscription_without_accounts)
    if subscription_off:
        # As after any subscription change: the task agent server re-reads the
        # configuration and works out again which tools it can offer.
        from .subscriptions import _notify_agent_server

        await _notify_agent_server()
    return {"success": True, "revocation_confirmed": confirmed, "subscription_turned_off": subscription_off}


def _turn_off_subscription_without_accounts() -> bool:
    """Turn the ChatGPT subscription off once no signed-in account is left."""
    from utils.subscriptions import SubscriptionError, get_store as get_subscription_store

    try:
        if any(entry.get("connected") for entry in get_store().status()["accounts"]):
            return False
        store = get_subscription_store()
        entry = store.read()["plans"].get("chatgpt")
        if not entry or not entry.get("enabled"):
            return False
        store.set_enabled("chatgpt", False)
    except (SubscriptionError, ChatGPTAuthError, OSError):
        # The sign-out itself succeeded; requests then ask to sign in again.
        logger.warning("ChatGPT 已退出登录，但关闭 ChatGPT 订阅失败", exc_info=True)
        return False
    return True
