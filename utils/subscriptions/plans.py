# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Subscription definitions, read from ``subscription_plans`` in config/api_providers.json.

Each plan says how it signs in, where requests go and which text tiers it may
take over. Which tiers a plan offers follows what the plan actually includes
(a tier that must read images is only offered when the plan has such a model),
decided plan by plan.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

# Text tiers a subscription can take over, in the order the panel lists them.
# Names are get_model_api_config model types.
TEXT_TIERS = (
    "conversation",
    "memory",
    "recognition",
    "agent",
    "agent_small",
    "agent_advanced",
    "game_main",
    "game_summary",
    "game_recognition",
)

AUTH_KINDS = ("chatgpt_oauth", "api_key")
PROVIDER_TYPES = ("chatgpt", "openai_compatible", "anthropic")


def _https(url: Any) -> str:
    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    return text if parts.scheme == "https" and parts.hostname else ""


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()]


def _normalize(plan_id: str, raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    auth = str(raw.get("auth") or "")
    provider_type = str(raw.get("provider_type") or "openai_compatible")
    if auth not in AUTH_KINDS or provider_type not in PROVIDER_TYPES:
        return None
    regions = {
        str(key): _https(url)
        for key, url in (raw.get("regions") or {}).items()
        if isinstance(key, str) and _https(url)
    } if isinstance(raw.get("regions"), dict) else {}
    base_url = _https(raw.get("base_url"))
    if not base_url and not regions:
        return None
    default_region = str(raw.get("default_region") or "")
    if regions and default_region not in regions:
        default_region = next(iter(regions))
    tiers = [tier for tier in _strings(raw.get("tiers")) if tier in TEXT_TIERS]
    if not tiers:
        return None
    defaults = raw.get("default_models") if isinstance(raw.get("default_models"), dict) else {}
    return {
        "id": plan_id,
        "name": str(raw.get("name") or plan_id),
        "auth": auth,
        "provider_type": provider_type,
        "base_url": base_url,
        "regions": regions,
        "default_region": default_region,
        "tiers": tiers,
        "default_models": {
            tier: str(model).strip()
            for tier, model in defaults.items()
            if tier in tiers and isinstance(model, str) and model.strip()
        },
        # "account": listed from the signed-in account; otherwise a fixed list.
        "models": "account" if raw.get("models") == "account" else _strings(raw.get("models")),
        # The plan's endpoint also lists its models (GET /models with the plan key).
        "fetch_models": raw.get("models") == "account" or bool(raw.get("fetch_models", False)),
        "key_prefix": str(raw.get("key_prefix") or ""),
        # Snapshot key whose value stands in when the plan has no key of its own
        # (a plan key the user already entered under General Model Provider).
        "key_fallback": str(raw.get("key_fallback") or ""),
        "verified": bool(raw.get("verified", False)),
        "info_url": _https(raw.get("info_url")),
    }


def get_plans() -> dict[str, dict[str, Any]]:
    """All valid plan definitions, in file order."""
    from utils.api_config_loader import get_config

    raw = get_config().get("subscription_plans") or {}
    if not isinstance(raw, dict):
        return {}
    plans = {}
    for plan_id, entry in raw.items():
        if isinstance(plan_id, str) and not plan_id.startswith("_"):
            plan = _normalize(plan_id, entry)
            if plan is not None:
                plans[plan_id] = plan
    return plans


def get_plan(plan_id: str) -> dict[str, Any] | None:
    return get_plans().get(str(plan_id or ""))


def plan_base_url(plan: dict[str, Any], region: str | None) -> str:
    """Endpoint for the chosen region (the plan's default when unset or unknown)."""
    if plan["regions"]:
        return plan["regions"].get(str(region or ""), plan["regions"][plan["default_region"]])
    return plan["base_url"]
