# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Loopback-only OAuth controls. Tokens never enter core_config or HTTP responses."""

import asyncio
import ipaddress
import threading

import httpx
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel, StrictBool

from utils.chatgpt_auth import ChatGPTAuthError, get_store
from utils.chatgpt_auth.login import LoginAttempt
from ._shared import router

_attempt = None
_lock = threading.Lock()
# A restored configuration whose reload failed: the next account or
# subscription change reapplies it even when nothing is left to restore.
_reload_pending = False


def local_settings(request: Request, response: Response):
    """Keep account controls on this machine: the OAuth callback is a loopback port.

    Cross-site writes are already rejected by the config router's mutation
    guard (same origin, JSON body); this adds the loopback-only rule, which
    also refuses remote instance sessions and DNS-rebound host names.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        local = request.client and ipaddress.ip_address(request.client.host).is_loopback
        host = request.url.hostname
        local_host = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local_host = False
        local = False
    if not local or not local_host:
        raise HTTPException(403, "ChatGPT account settings require a local connection.")


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


async def _reload(config):
    """Apply a persisted configuration, remembering a failure for the next change."""
    global _reload_pending
    from .core_config import reload_api_configuration

    result = await reload_api_configuration(config)
    _reload_pending = not result.get('success')
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

    from .core_config import _core_api_save_lock
    async with _core_api_save_lock:
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
    from .core_config import _core_api_save_lock
    async with _core_api_save_lock:
        await _safe_call(_change_account, "select", body.account_id)
    return {"success": True}


@router.post("/chatgpt/logout", dependencies=[Depends(local_settings)])
async def chatgpt_logout(body: AccountRequest):
    from copy import deepcopy
    from .core_config import _core_api_save_lock
    from utils.config_manager import get_config_manager
    from utils.chatgpt_subscription import disable
    restored = False
    config = None
    sign_out_error = None
    async with _core_api_save_lock:
        if _attempt and not _attempt.done.is_set():
            raise HTTPException(400, 'Complete the pending ChatGPT sign-in before changing accounts.')
        accounts = await _safe_call(get_store().status)
        if not any(a['id'] == body.account_id for a in accounts['accounts']):
            raise HTTPException(400, 'signInRequired')
        remaining = [a for a in accounts['accounts']
                     if a['id'] != body.account_id and a.get('connected')]
        if not remaining:
            def restore(value):
                nonlocal restored
                restored = disable(value)
                return deepcopy(value)
            config = await get_config_manager().aupdate_json_config('core_config.json', restore)
        elif _reload_pending:
            from utils.config_manager.json_update import load_json_config_snapshot
            config = await asyncio.to_thread(load_json_config_snapshot, get_config_manager(), 'core_config.json')
        reload_needed = restored or _reload_pending
        try:
            confirmed = await _safe_call(_change_account, "sign_out", body.account_id)
        except HTTPException as exc:
            # The restored configuration is already committed. Reload it even
            # when credential cleanup fails, after releasing the save lock.
            sign_out_error = exc
    result = await _reload(config) if reload_needed else {'success': True}
    if sign_out_error is not None:
        raise sign_out_error
    return {**result, "revocation_confirmed": confirmed, "subscription_restored": restored}


class SubscriptionRequest(BaseModel):
    enabled: StrictBool
    revision: str = ''


@router.get('/chatgpt/subscription', dependencies=[Depends(local_settings)])
async def chatgpt_subscription_preview(verify_authorization: bool = False):
    from utils.config_manager import get_config_manager
    from utils.config_manager.json_update import load_json_config_snapshot
    from utils.chatgpt_subscription import preview, supported_slots
    manager = get_config_manager()
    config, slots = await asyncio.gather(
        asyncio.to_thread(load_json_config_snapshot, manager, 'core_config.json'),
        asyncio.to_thread(supported_slots),
    )
    result = preview(config, slots)
    result['authorization_required'] = False
    if result['enabled'] and verify_authorization:
        try:
            await asyncio.to_thread(get_store().access_token)
        except (ChatGPTAuthError, httpx.HTTPError, OSError):
            result['authorization_required'] = True
    return result


@router.post('/chatgpt/subscription', dependencies=[Depends(local_settings)])
async def chatgpt_subscription_update(body: SubscriptionRequest):
    from utils.config_manager import get_config_manager
    from utils.chatgpt_subscription import enable, disable, preview, supported_slots
    from .core_config import _core_api_save_lock
    from utils.llm_client.chatgpt_client import ChatGPTResponseError, acreate_chatgpt_client
    from copy import deepcopy

    manager = get_config_manager()
    async with _core_api_save_lock:
        # Turning off restores from the saved snapshot alone, so it must not
        # depend on the provider metadata that enabling reads.
        slots = await asyncio.to_thread(supported_slots) if body.enabled else None
        model = ''
        if body.enabled:
            if _attempt and not _attempt.done.is_set():
                raise HTTPException(400, 'pending')
            before = await _safe_call(get_store().status)
            if not before['active']:
                raise HTTPException(400, 'signInRequired')
            try:
                client = await acreate_chatgpt_client(timeout=20)  # noqa: LLM_OUTPUT_BUDGET # Model catalog only.
            except ChatGPTAuthError:
                raise HTTPException(400, 'signInRequired') from None
            except (httpx.HTTPError, OSError):
                raise HTTPException(503, 'failed') from None
            try:
                models = await client.alist_models()
            except ChatGPTAuthError:
                raise HTTPException(400, 'signInRequired') from None
            except ChatGPTResponseError as exc:
                detail = 'usageLimit' if exc.status_code == 429 else 'failed'
                raise HTTPException(exc.status_code or 502, detail) from None
            except httpx.HTTPError:
                raise HTTPException(503, 'failed') from None
            except Exception:
                # e.g. a catalog body that is not the expected JSON
                raise HTTPException(502, 'failed') from None
            finally:
                await client.aclose()
            ids = sorted({item['id'] for item in models if isinstance(item.get('id'), str) and item['id']})
            if not ids:
                raise HTTPException(400, 'noModels')
            # Stable selection independent of upstream catalog ordering; no guessed model IDs.
            model = ids[0]
            if (await _safe_call(get_store().status))['active'] != before['active']:
                raise HTTPException(409, 'accountChanged')

        def apply(config):
            if body.enabled:
                enable(config, slots, model, body.revision)
            else:
                disable(config)
            return deepcopy(config)
        try:
            config = await manager.aupdate_json_config('core_config.json', apply)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
    result = await _reload(config)
    if slots is None:
        try:
            slots = await asyncio.to_thread(supported_slots)
        except Exception:
            slots = []
    return {**result, 'subscription': preview(config, slots)}
