# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Resolve the subscription overlay into per-tier model configurations.

``build_overlay`` runs once per ``get_core_config`` snapshot and yields, for
every tier an enabled subscription takes, what ``get_model_api_config`` returns
for it. Precedence is applied there: a tier the user set up in full under
per-feature models keeps that setting; otherwise the subscription wins over
following and the general providers.
"""

from __future__ import annotations

import logging
from typing import Any

from .plans import plan_base_url

logger = logging.getLogger(__name__)

# get_model_api_config model type -> core_config field prefix.
TIER_PREFIX = {
    "conversation": "conversation",
    "memory": "memory",
    "recognition": "recognition",
    "agent": "agent",
    "agent_small": "agentSmall",
    "agent_advanced": "agentAdvanced",
    "game_main": "gameMain",
    "game_summary": "gameSummary",
    "game_recognition": "gameRecognition",
}


def plan_key(plan: dict[str, Any], entry: dict[str, Any], snapshot: dict[str, Any]) -> str:
    """The key a plan's requests carry: its own, else the general-provider one it reuses."""
    own = str(entry.get("api_key") or "").strip()
    if own or not plan["key_fallback"]:
        return own
    return str(snapshot.get(plan["key_fallback"]) or "").strip()


def build_overlay(state: dict[str, Any], plans: dict[str, dict[str, Any]], snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Tier -> model configuration for every tier an enabled, usable plan takes."""
    overlay: dict[str, dict[str, Any]] = {}
    for plan_id, plan in plans.items():
        entry = state.get("plans", {}).get(plan_id)
        if not entry or not entry.get("enabled"):
            continue
        if plan["auth"] == "api_key":
            api_key = plan_key(plan, entry, snapshot)
            if not api_key:
                continue
        else:
            api_key = ""
        base_url = plan_base_url(plan, entry.get("region"))
        for tier, model in entry.get("tiers", {}).items():
            if tier in plan["tiers"] and model and tier not in overlay:
                overlay[tier] = {
                    "model": model,
                    "api_key": api_key,
                    "base_url": base_url,
                    "provider_type": plan["provider_type"],
                    "subscription": plan_id,
                }
    return overlay


def load_overlay(config_manager: Any, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The overlay for a snapshot; any failure means no overlay (and one log line)."""
    try:
        from .plans import get_plans
        from .store import SubscriptionStore

        store = SubscriptionStore(config_manager.local_state_dir / "subscriptions.json")
        state = store.read()
        if not state["plans"]:
            return {}
        return build_overlay(state, get_plans(), snapshot)
    except Exception:  # noqa: BLE001 - never break model resolution over the overlay
        logger.warning("订阅覆盖读取失败，本次按未开启订阅处理", exc_info=True)
        return {}


# get_model_api_config model type -> snapshot key of the tier's own model (its URL key adds "_URL").
TIER_MODEL_KEY = {
    "conversation": "CONVERSATION_MODEL",
    "memory": "MEMORY_MODEL",
    "recognition": "RECOGNITION_MODEL",
    "agent": "AGENT_MODEL",
    "agent_small": "AGENT_SMALL_MODEL",
    "agent_advanced": "AGENT_ADVANCED_MODEL",
    "game_main": "GAME_MAIN_MODEL",
    "game_summary": "GAME_SUMMARY_MODEL",
    "game_recognition": "GAME_RECOGNITION_MODEL",
}


# Tiers whose resolver reads an empty provider (a setting saved before the field
# existed) as the tier's own address and model. For the other tiers an empty
# provider means following their default tier.
EMPTY_PROVIDER_IS_OWN_ENDPOINT = frozenset({"conversation", "memory", "recognition", "agent"})


def tier_setting_in_effect(model_type: str, core_config: dict[str, Any], treat_as_custom: bool) -> bool:
    """Whether the tier's own per-feature setting is what the resolver would use.

    Set up in full: per-feature models on, a named provider or custom endpoint
    chosen (not one of the follow options), and both address and model entered
    by the user (``TIER_SAVED_FIELDS``; the snapshot's own values may have been
    filled in from the general provider) and present in the snapshot. A tier
    saved without a provider counts too where the resolver uses its own address
    and model for it, provided both were entered. Such a tier keeps its setting
    over any subscription.
    """
    if not treat_as_custom:
        return False
    prefix = TIER_PREFIX.get(model_type)
    model_key = TIER_MODEL_KEY.get(model_type)
    if prefix is None or model_key is None:
        return False
    provider = str(core_config.get(f"{prefix}ModelProvider") or "").strip()
    if provider.startswith("follow_"):
        return False
    saved = core_config.get("TIER_SAVED_FIELDS")
    if not provider and (model_type not in EMPTY_PROVIDER_IS_OWN_ENDPOINT or not isinstance(saved, dict)):
        # Without the saved fields an empty provider cannot be told from defaults.
        return False
    if isinstance(saved, dict):
        url_saved, model_saved = saved.get(prefix, (False, False))
        if not (url_saved and model_saved):
            return False
    return bool(
        str(core_config.get(model_key) or "").strip()
        and str(core_config.get(f"{model_key}_URL") or "").strip()
    )
