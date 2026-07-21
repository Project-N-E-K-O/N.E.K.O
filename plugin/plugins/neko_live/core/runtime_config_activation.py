"""Runtime config activation helpers."""

from __future__ import annotations

from collections import deque
from typing import Any

from .co_stream_capabilities import normalize_activation_mode
from .contracts import LiveConfig, normalize_live_platform, parse_room_id


def clean_config_updates(updates: dict[str, Any]) -> dict[str, Any]:
    allowed = set(LiveConfig.__dataclass_fields__.keys())
    clean = {key: value for key, value in updates.items() if key in allowed}
    if "live_room_id" in clean:
        clean["live_room_id"] = parse_room_id(clean["live_room_id"])
    if "live_platform" in clean:
        clean["live_platform"] = normalize_live_platform(clean["live_platform"])
    if "live_room_ref" in clean:
        clean["live_room_ref"] = str(clean["live_room_ref"] or "").strip()
    if "co_stream_host_pause_fill_activation" in clean:
        clean["co_stream_host_pause_fill_activation"] = normalize_activation_mode(
            clean["co_stream_host_pause_fill_activation"]
        )
    # Automatic speech consent is only writable by a future dedicated,
    # user-confirmed action. Generic settings updates may never arm it.
    clean.pop("co_stream_host_pause_fill_auto_consent_version", None)
    return clean


def _has_configured_live_target(config: LiveConfig) -> bool:
    room_ref = str(getattr(config, "live_room_ref", "") or "").strip()
    if room_ref:
        return True
    platform = normalize_live_platform(getattr(config, "live_platform", "bilibili"))
    return platform == "bilibili" and int(getattr(config, "live_room_id", 0) or 0) > 0


def activate_config(runtime: Any, config: LiveConfig) -> LiveConfig:
    runtime.config = config
    runtime.audit.set_limit(max(50, runtime.config.recent_limit * 4))
    runtime.recent_results = deque(
        runtime.recent_results,
        maxlen=runtime.config.recent_limit,
    )
    runtime.recent_sandbox_results = deque(
        runtime.recent_sandbox_results,
        maxlen=runtime.config.recent_limit,
    )
    runtime.permission_gate.update(runtime.config)
    runtime.safety_guard.update(runtime.config)
    if not _has_configured_live_target(runtime.config):
        runtime.live_connection_state = "disconnected"
        runtime.live_connection_auth_mode = "unknown"
    runtime.safety_guard.set_connected(runtime.live_connection_state == "connected")
    return runtime.config
