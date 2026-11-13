# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Loopback-only OAuth controls. Tokens never enter core_config or HTTP responses."""

import asyncio
import ipaddress
import threading
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel

from utils.chatgpt_auth import ChatGPTAuthError, get_store
from utils.chatgpt_auth.login import LoginAttempt
from ._shared import router

_attempt = None
_lock = threading.Lock()


def local_settings(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        local = request.client and ipaddress.ip_address(request.client.host).is_loopback
        host = request.url.hostname
        local_host = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local_host = False
        local = False
    origin = request.headers.get("origin")
    if (
        not local
        or not local_host
        or request.headers.get("x-neko-chatgpt") != "1"
        or (
            origin
            and (
                urlsplit(origin).netloc != request.url.netloc
                or urlsplit(origin).scheme != request.url.scheme
            )
        )
    ):
        raise HTTPException(
            403, "ChatGPT account settings require a same-origin local connection."
        )


class AccountRequest(BaseModel):
    account_id: str | None = None


def _status():
    result = get_store().status()
    result.update(
        login_status=_attempt.status if _attempt else "idle",
        error=_attempt.error if _attempt else "",
    )
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
    confirmed = await _safe_call(_change_account, "sign_out", body.account_id)
    return {"success": True, "revocation_confirmed": confirmed}
