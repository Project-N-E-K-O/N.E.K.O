# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Subscriptions & plans panel: loopback-only controls over ``<state>/subscriptions.json``.

Every write changes exactly one thing (one switch, one tier, one key), so a
stale page can only change what the user clicked. Nothing here touches
``core_config.json`` or the settings save flow; turning a subscription off
leaves the user's own tier settings exactly as they were. Ongoing sessions keep
their clients until they end; new requests resolve the overlay at once.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ._shared import logger, router
from .chatgpt import require_local_connection


def local_only(request: Request, response: Response):
    require_local_connection(request, response, "Subscription settings require a local connection.")


class EnableRequest(BaseModel):
    enabled: bool


class TierRequest(BaseModel):
    tier: str
    model: str | None = None


class KeyRequest(BaseModel):
    api_key: str = ""
    region: str | None = None


class RegionRequest(BaseModel):
    region: str


class TestRequest(BaseModel):
    tier: str | None = None
    model: str | None = None


def _refuse(code: str, message: str, status: int = 400):
    raise HTTPException(status, {"code": code, "message": message})


def _plan_or_404(plan_id: str) -> dict[str, Any]:
    from utils.subscriptions import get_plan

    plan = get_plan(plan_id)
    if plan is None:
        _refuse("unknown_plan", "Unknown subscription.", 404)
    return plan


def _chatgpt_connected() -> bool:
    from utils.chatgpt_auth import ChatGPTAuthError, get_store as get_chatgpt_store

    try:
        status = get_chatgpt_store().status()
    except (ChatGPTAuthError, OSError):
        return False
    return any(
        entry.get("id") == status.get("active") and entry.get("connected")
        for entry in status.get("accounts", [])
    )


def _status() -> dict[str, Any]:
    """Everything the panel and the per-feature badges show; keys are never returned."""
    from utils.config_manager import get_config_manager
    from utils.subscriptions import TEXT_TIERS, get_plans, get_store
    from utils.subscriptions.overlay import plan_key, tier_setting_in_effect

    config_manager = get_config_manager()
    plans = get_plans()
    store = get_store()
    state = store.read()
    masked = store.masked()
    snapshot = config_manager.get_core_config()
    overlay = snapshot.get("SUBSCRIPTION_OVERLAY") or {}
    per_feature_on = bool(snapshot.get("ENABLE_CUSTOM_API"))
    chatgpt_connected = _chatgpt_connected()

    plan_rows = []
    for plan_id, plan in plans.items():
        entry = state["plans"].get(plan_id, {})
        if plan["auth"] == "chatgpt_oauth":
            ready = chatgpt_connected
            fallback_key = False
        else:
            ready = bool(plan_key(plan, entry, snapshot))
            fallback_key = bool(plan_key(plan, {}, snapshot))
        plan_rows.append({
            "id": plan_id,
            "name": plan["name"],
            "auth": plan["auth"],
            "regions": list(plan["regions"]),
            "default_region": plan["default_region"],
            "tiers": plan["tiers"],
            "default_models": plan["default_models"],
            "models": plan["models"],
            "fetch_models": plan["fetch_models"],
            "key_prefix": plan["key_prefix"],
            "verified": plan["verified"],
            "info_url": plan["info_url"],
            "ready": ready,
            "uses_general_provider_key": fallback_key,
        })

    # Which plan each tier is ticked under, on or off (a tier belongs to one plan).
    assigned = {}
    for plan_id, entry in state["plans"].items():
        for tier in entry.get("tiers", {}):
            assigned.setdefault(tier, plan_id)

    tiers = {}
    for tier in TEXT_TIERS:
        resolved = config_manager.get_model_api_config(tier, _core_config=snapshot)
        claimed_by = (overlay.get(tier) or {}).get("subscription")
        tiers[tier] = {
            # The plan the tier is ticked under, even while that plan is off.
            "assigned_to": assigned.get(tier),
            # The subscription the tier's requests actually go to (directly or by following).
            "effective": resolved.get("subscription"),
            # The subscription that took this tier in the panel.
            "claimed_by": claimed_by,
            # Taken, but the tier's own per-feature setting wins.
            "own_setting": bool(claimed_by) and tier_setting_in_effect(tier, snapshot, per_feature_on),
        }
    return {"plans": plan_rows, "state": masked, "tiers": tiers}


async def _notify_agent_server() -> None:
    """Let the task agent server drop clients it built from the previous configuration."""
    from config import TOOL_SERVER_PORT
    from utils.internal_http_client import internal_service_event_hooks

    try:
        async with httpx.AsyncClient(
            timeout=5,
            proxy=None,
            trust_env=False,
            event_hooks=internal_service_event_hooks(),
        ) as client:
            notify_resp = await client.post(f"http://127.0.0.1:{TOOL_SERVER_PORT}/notify_config_changed")
            if notify_resp is not None and not notify_resp.is_success:
                raise RuntimeError(f"HTTP {notify_resp.status_code}")
    except Exception as exc:  # noqa: BLE001 - best effort, as after a settings save
        logger.warning("通知 agent_server 刷新订阅配置失败（非致命）: %s", exc)


async def _run(fn, *args) -> Any:
    from filelock import Timeout as LockTimeout

    from utils.subscriptions import SubscriptionError

    try:
        return await asyncio.to_thread(fn, *args)
    except SubscriptionError as exc:
        _refuse(exc.code, str(exc))
    except LockTimeout:
        _refuse("busy", "Subscription settings are busy. Please retry.", 503)
    except OSError:
        # The file is held by another program (antivirus on os.replace) or the disk is full.
        logger.warning("订阅设置读写失败", exc_info=True)
        _refuse("save_failed", "Could not save the subscription settings. Please retry.", 500)


async def _changed() -> dict[str, Any]:
    await _notify_agent_server()
    return await _run(_status)


@router.get("/subscriptions", dependencies=[Depends(local_only)])
async def subscriptions_status():
    return await _run(_status)


def _set_enabled(plan_id: str, plan: dict[str, Any], enabled: bool) -> None:
    """Switch a plan; turning it on needs its sign-in or key and at least one tier."""
    from utils.config_manager import get_config_manager
    from utils.subscriptions import get_store
    from utils.subscriptions.overlay import plan_key

    if not enabled:
        get_store().set_enabled(plan_id, False)
        return
    if plan["auth"] == "chatgpt_oauth":
        if not _chatgpt_connected():
            _refuse("not_signed_in", "Continue with ChatGPT before turning this on.")
        snapshot = None
    else:
        snapshot = get_config_manager().get_core_config()

    def precheck(entry: dict[str, Any]) -> None:
        # Checked on the entry as it is inside the write, so a key removed or the last
        # tier taken away by another tab meanwhile cannot leave the plan on.
        if snapshot is not None and not plan_key(plan, entry, snapshot):
            _refuse("missing_key", "Enter this plan's key before turning it on.")
        if not any(tier in plan["tiers"] for tier in entry.get("tiers", {})):
            _refuse("no_tiers", "Choose at least one feature for this subscription.")

    get_store().set_enabled(plan_id, True, precheck=precheck)


@router.post("/subscriptions/{plan_id}/enable", dependencies=[Depends(local_only)])
async def subscription_enable(plan_id: str, body: EnableRequest):
    plan = _plan_or_404(plan_id)
    await _run(_set_enabled, plan_id, plan, body.enabled)
    return await _changed()


@router.post("/subscriptions/{plan_id}/tier", dependencies=[Depends(local_only)])
async def subscription_tier(plan_id: str, body: TierRequest):
    plan = _plan_or_404(plan_id)
    if body.tier not in plan["tiers"]:
        _refuse("unsupported_tier", "This subscription cannot take this feature.")

    def apply():
        from utils.subscriptions import get_store

        # A plan left with nothing to take over is turned off in the same write.
        return get_store().set_tier(plan_id, body.tier, body.model)

    moved_from = await _run(apply)
    result = await _changed()
    result["moved_from"] = moved_from
    return result


@router.post("/subscriptions/{plan_id}/key", dependencies=[Depends(local_only)])
async def subscription_key(plan_id: str, body: KeyRequest):
    plan = _plan_or_404(plan_id)
    if plan["auth"] != "api_key":
        _refuse("no_key", "This subscription signs in instead of using a key.")
    if body.region is not None and body.region not in plan["regions"]:
        _refuse("unknown_region", "Unknown region for this subscription.")

    def apply():
        from utils.subscriptions import get_store

        get_store().set_key(plan_id, body.api_key, body.region)

    await _run(apply)
    return await _changed()


@router.post("/subscriptions/{plan_id}/region", dependencies=[Depends(local_only)])
async def subscription_region(plan_id: str, body: RegionRequest):
    plan = _plan_or_404(plan_id)
    if body.region not in plan["regions"]:
        _refuse("unknown_region", "Unknown region for this subscription.")

    def apply():
        from utils.subscriptions import get_store

        get_store().set_region(plan_id, body.region)

    await _run(apply)
    return await _changed()


def _endpoint(plan: dict[str, Any], plan_id: str) -> tuple[str, str]:
    from utils.config_manager import get_config_manager
    from utils.subscriptions import get_store
    from utils.subscriptions.overlay import plan_key
    from utils.subscriptions.plans import plan_base_url

    entry = get_store().read()["plans"].get(plan_id, {})
    key = "" if plan["auth"] == "chatgpt_oauth" else plan_key(plan, entry, get_config_manager().get_core_config())
    return plan_base_url(plan, entry.get("region")), key


@router.post("/subscriptions/{plan_id}/models", dependencies=[Depends(local_only)])
async def subscription_models(plan_id: str):
    """Models to choose from: listed by the plan's endpoint when it can, else the plan's own list."""
    from .model_catalog import _fetch_models

    plan = _plan_or_404(plan_id)
    if not plan["fetch_models"]:
        return {"success": True, "models": [{"id": model} for model in plan["models"]]}
    if plan["auth"] == "chatgpt_oauth" and not await asyncio.to_thread(_chatgpt_connected):
        _refuse("not_signed_in", "Continue with ChatGPT before listing models.")
    url, key = await asyncio.to_thread(_endpoint, plan, plan_id)
    if plan["auth"] == "api_key" and not key:
        _refuse("missing_key", "Enter this plan's key before listing models.")
    return await _fetch_models(url, key, plan["provider_type"])


@router.post("/subscriptions/{plan_id}/test", dependencies=[Depends(local_only)])
async def subscription_test(plan_id: str, body: TestRequest):
    """One small request through the subscription (only when the user clicks)."""
    from utils.subscriptions import get_store

    from .connectivity import _test_connectivity_candidates

    plan = _plan_or_404(plan_id)
    entry = (await asyncio.to_thread(get_store().read))["plans"].get(plan_id, {})
    tier = body.tier if body.tier in plan["tiers"] else None
    model = (
        (body.model or "").strip()
        or (entry.get("tiers", {}).get(tier) if tier else "")
        or next(iter(entry.get("tiers", {}).values()), "")
        or next(iter(plan["default_models"].values()), "")
    )
    if not model:
        _refuse("no_model", "Choose a model before testing.")
    url, key = await asyncio.to_thread(_endpoint, plan, plan_id)
    if plan["auth"] == "api_key" and not key:
        _refuse("missing_key", "Enter this plan's key before testing.")
    return await _test_connectivity_candidates([url], key, model, plan["provider_type"], is_free=False)
