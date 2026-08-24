"""Best-effort config persistence with bounded runtime impact."""

from __future__ import annotations

import asyncio
import time
from typing import Any


async def persist_config_best_effort(runtime: Any, clean: dict[str, Any]) -> None:
    try:
        await asyncio.wait_for(
            persist_config_update(runtime, clean),
            timeout=runtime._CONFIG_PERSIST_BUDGET_SECONDS,
        )
        runtime._config_last_persist_at = time.time()
        runtime._config_last_error = ""
    except asyncio.TimeoutError:
        runtime._config_last_error = "config_persist_timeout"
        runtime.audit.record(
            "config_persist_timeout",
            f"config persistence exceeded {runtime._CONFIG_PERSIST_BUDGET_SECONDS}s budget; "
            "runtime config already applied in memory",
            level="warning",
        )
    except Exception as exc:
        runtime._config_last_error = f"config_persist_failed:{type(exc).__name__}"
        runtime.audit.record(
            "config_persist_failed",
            f"config persistence failed, using runtime config: {type(exc).__name__}",
            level="warning",
        )


async def persist_config_update(runtime: Any, clean: dict[str, Any]) -> None:
    update_own_config = getattr(
        getattr(runtime.plugin, "ctx", None),
        "update_own_config",
        None,
    )
    if callable(update_own_config):
        await update_own_config({"neko_live": clean}, timeout=10.0)
        return

    config_api = getattr(runtime.plugin, "config", None)
    ensure_active = getattr(config_api, "profile_ensure_active", None)
    if callable(ensure_active):
        await ensure_active("default", {"neko_live": clean}, timeout=10.0)
    update = getattr(config_api, "update", None)
    if not callable(update):
        raise RuntimeError("plugin config update API is unavailable")
    try:
        await update({"neko_live": clean})
    except ValueError as exc:
        if "no active profile" not in str(exc):
            raise
        raise RuntimeError("plugin config update requires an active profile") from exc
