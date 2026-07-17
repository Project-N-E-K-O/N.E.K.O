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
                dumped.get("neko_live", {})
                if isinstance(dumped.get("neko_live"), dict)
                else {}
            )
    except Exception as exc:
        runtime.audit.record(
            "config_load_failed",
            f"config load failed: {type(exc).__name__}",
            level="warning",
        )
    loaded = RoastConfig.from_mapping(data)
    async with get_config_lock(runtime):
        old_config = runtime.config
        old_platform = normalize_live_platform(getattr(old_config, "live_platform", "bilibili"))
        old_room_id = int(getattr(old_config, "live_room_id", 0) or 0)
        old_room_ref = _configured_room_ref(old_config, old_platform)
        old_provider = _captured_provider(runtime, old_platform)
        was_listening = _is_listening(old_provider)
        activate_config(runtime, loaded)
        runtime._config_revision += 1
        clean = _live_config_diff(old_config, runtime.config)
        await reconcile_live_listener_after_config(
            runtime,
            clean,
            old_room_id=old_room_id,
            old_platform=old_platform,
            old_room_ref=old_room_ref,
            was_listening=was_listening,
            old_provider=old_provider,
        )
        return runtime.config


def get_config_lock(runtime: Any) -> asyncio.Lock:
    if runtime._config_lock is None:
        runtime._config_lock = asyncio.Lock()
    return runtime._config_lock


async def update_config(runtime: Any, updates: dict[str, Any]) -> RoastConfig:
    clean = clean_config_updates(updates)
    if not clean:
        return runtime.config
    async with get_config_lock(runtime):
        _normalize_live_target_update(runtime, clean)
        old_room_id = int(runtime.config.live_room_id or 0)
        old_platform = normalize_live_platform(getattr(runtime.config, "live_platform", "bilibili"))
        old_room_ref = _configured_room_ref(runtime.config, old_platform)
        old_provider = _captured_provider(runtime, old_platform)
        was_listening = _is_listening(old_provider)
        developer_mode_changed = (
            "developer_tools_enabled" in clean
            and bool(clean["developer_tools_enabled"])
            != bool(runtime.config.developer_tools_enabled)
        )
        data = runtime.config.to_dict()
        data.update(clean)
        candidate = RoastConfig.from_mapping(data)
        live_diff = _live_config_diff(runtime.config, candidate)
        if was_listening and live_diff:
            runtime._accepting_live_events = False
        activate_config(runtime, candidate)
        runtime._config_revision += 1
        defer_instruction_sync = was_listening and bool(
            {"live_platform", "live_room_ref", "live_room_id"} & set(live_diff)
        )
        if not defer_instruction_sync and {
            "live_enabled",
            "avatar_roast_enabled",
            "avatar_analysis_enabled",
            "danmaku_response_enabled",
            "live_support_events_enabled",
            "warmup_hosting_enabled",
            "idle_hosting_enabled",
            "active_engagement_enabled",
        } & set(clean):
            await runtime.sync_live_instructions(force=True)
        if developer_mode_changed:
            await runtime.sync_developer_mode(announce=False, force=True)
        await persist_config_best_effort(runtime, clean)
        await reconcile_live_listener_after_config(
            runtime,
            clean,
            old_room_id=old_room_id,
            old_platform=old_platform,
            old_room_ref=old_room_ref,
            was_listening=was_listening,
            old_provider=old_provider,
        )
        return runtime.config


def _captured_provider(runtime: Any, platform: str) -> Any:
    router = getattr(runtime, "live_provider", None)
    provider_for = getattr(router, "provider_for", None)
    if callable(provider_for):
        return provider_for(platform)
    return router


def _is_listening(provider: Any) -> bool:
    checker = getattr(provider, "is_listening", None)
    if not callable(checker):
        return False
    try:
        return checker() is True
    except Exception:
        return False


def _configured_room_ref(config: Any, platform: str) -> str:
    room_ref = str(getattr(config, "live_room_ref", "") or "").strip()
    room_id = int(getattr(config, "live_room_id", 0) or 0)
    if platform == "bilibili" and not room_ref and room_id > 0:
        return str(room_id)
    return room_ref


def _live_config_diff(old: Any, new: Any) -> dict[str, Any]:
    keys = ("live_platform", "live_room_ref", "live_room_id", "live_enabled")
    return {key: getattr(new, key) for key in keys if getattr(old, key) != getattr(new, key)}


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
