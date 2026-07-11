"""Configuration lifecycle and live-listener reconciliation for the runtime."""

from __future__ import annotations

import asyncio
from typing import Any

from .contracts import RoastConfig, normalize_live_platform
from .live_provider_router import normalize_room_ref_for_platform
from .runtime_config_activation import (
    activate_config,
    clean_config_updates,
)
from .runtime_config_persistence import (
    persist_config_best_effort,
    persist_config_update,
)
from .runtime_live_listener import (
    reconcile_live_listener_after_config,
    start_live_listener,
    stop_live_listener,
)


async def reload_config(runtime: Any) -> RoastConfig:
    data: dict[str, Any] = {}
    try:
        dumped = await runtime.plugin.config.dump(timeout=5.0)
        if isinstance(dumped, dict):
            data = (
                dumped.get("neko_roast", {})
                if isinstance(dumped.get("neko_roast"), dict)
                else {}
            )
    except Exception as exc:
        runtime.audit.record(
            "config_load_failed",
            f"config load failed: {type(exc).__name__}",
            level="warning",
        )
    return activate_config(runtime, RoastConfig.from_mapping(data))


def get_config_lock(runtime: Any) -> asyncio.Lock:
    if runtime._config_lock is None:
        runtime._config_lock = asyncio.Lock()
    return runtime._config_lock


async def update_config(runtime: Any, updates: dict[str, Any]) -> RoastConfig:
    clean = clean_config_updates(updates)
    if not clean:
        return runtime.config
    _normalize_live_target_update(runtime, clean)

    old_room_id = int(runtime.config.live_room_id or 0)
    old_platform = normalize_live_platform(getattr(runtime.config, "live_platform", "bilibili"))
    old_room_ref = str(getattr(runtime.config, "live_room_ref", "") or "").strip()
    if old_platform == "bilibili" and not old_room_ref and old_room_id > 0:
        old_room_ref = str(old_room_id)
    live_provider = getattr(runtime, "live_provider", None)
    was_listening = bool(live_provider.is_listening()) if live_provider is not None else False
    async with get_config_lock(runtime):
        data = runtime.config.to_dict()
        data.update(clean)
        activate_config(runtime, RoastConfig.from_mapping(data))
        if "live_enabled" in clean:
            await runtime.sync_live_instructions(force=True)
        if "developer_tools_enabled" in clean:
            await runtime.sync_developer_mode(announce=False, force=True)
        await persist_config_best_effort(runtime, clean)
    await reconcile_live_listener_after_config(
        runtime,
        clean,
        old_room_id=old_room_id,
        old_platform=old_platform,
        old_room_ref=old_room_ref,
        was_listening=was_listening,
    )
    return runtime.config


def _normalize_live_target_update(runtime: Any, clean: dict[str, Any]) -> None:
    current_platform = normalize_live_platform(getattr(runtime.config, "live_platform", "bilibili"))
    target_platform = normalize_live_platform(
        clean.get("live_platform", current_platform)
    )
    if target_platform != "douyin":
        return
    if not {"live_platform", "live_room_ref"} & set(clean):
        return
    if "live_room_ref" not in clean and current_platform != target_platform:
        clean["live_room_ref"] = ""
        clean["live_room_id"] = 0
        return
    raw_room_ref = clean.get("live_room_ref", getattr(runtime.config, "live_room_ref", ""))
    normalized = normalize_room_ref_for_platform(target_platform, raw_room_ref)
    clean["live_room_ref"] = str(normalized.get("room_ref") or "")
    clean["live_room_id"] = 0
