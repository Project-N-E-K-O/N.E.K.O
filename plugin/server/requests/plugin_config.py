from __future__ import annotations

from typing import Any, Dict


def _deny_cross_plugin_access(
    *,
    from_plugin: Any,
    request_id: Any,
    timeout: Any,
    send_response,
) -> None:
    send_response(
        from_plugin,
        request_id,
        None,
        "Permission denied: can only access own config",
        timeout=timeout,
    )


def _get_target_plugin_id(request: Dict[str, Any]) -> tuple[str, str]:
    from_plugin = request.get("from_plugin")
    if not isinstance(from_plugin, str) or not from_plugin:
        raise ValueError("Invalid from_plugin")
    target_plugin_id = request.get("plugin_id")
    if target_plugin_id is None:
        target_plugin_id = from_plugin
    if not isinstance(target_plugin_id, str) or not target_plugin_id:
        raise ValueError("Invalid plugin_id")
    return from_plugin, target_plugin_id


async def handle_plugin_config_get(request: Dict[str, Any], send_response) -> None:
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    try:
        from_plugin, target_plugin_id = _get_target_plugin_id(request)
    except Exception as e:
        send_response(request.get("from_plugin"), request_id, None, str(e), timeout=timeout)
        return

    if target_plugin_id != from_plugin:
        _deny_cross_plugin_access(
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            send_response=send_response,
        )
        return

    try:
        from plugin.server.config_service import load_plugin_config

        data = load_plugin_config(target_plugin_id)
        send_response(from_plugin, request_id, data, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_plugin_config_base_get(request: Dict[str, Any], send_response) -> None:
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    try:
        from_plugin, target_plugin_id = _get_target_plugin_id(request)
    except Exception as e:
        send_response(request.get("from_plugin"), request_id, None, str(e), timeout=timeout)
        return

    if target_plugin_id != from_plugin:
        _deny_cross_plugin_access(
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            send_response=send_response,
        )
        return

    try:
        from plugin.server.config_service import load_plugin_base_config

        data = load_plugin_base_config(target_plugin_id)
        send_response(from_plugin, request_id, data, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_plugin_config_profiles_get(request: Dict[str, Any], send_response) -> None:
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    try:
        from_plugin, target_plugin_id = _get_target_plugin_id(request)
    except Exception as e:
        send_response(request.get("from_plugin"), request_id, None, str(e), timeout=timeout)
        return

    if target_plugin_id != from_plugin:
        _deny_cross_plugin_access(
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            send_response=send_response,
        )
        return

    try:
        from plugin.server.config_service import get_plugin_profiles_state

        data = get_plugin_profiles_state(target_plugin_id)
        send_response(from_plugin, request_id, data, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_plugin_config_profile_get(request: Dict[str, Any], send_response) -> None:
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)
    profile_name = request.get("profile_name")

    try:
        from_plugin, target_plugin_id = _get_target_plugin_id(request)
    except Exception as e:
        send_response(request.get("from_plugin"), request_id, None, str(e), timeout=timeout)
        return

    if target_plugin_id != from_plugin:
        _deny_cross_plugin_access(
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            send_response=send_response,
        )
        return

    if not isinstance(profile_name, str) or not profile_name.strip():
        send_response(from_plugin, request_id, None, "Invalid profile_name", timeout=timeout)
        return

    try:
        from plugin.server.config_service import get_plugin_profile_config

        data = get_plugin_profile_config(target_plugin_id, profile_name.strip())
        send_response(from_plugin, request_id, data, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_plugin_config_effective_get(request: Dict[str, Any], send_response) -> None:
    """Get effective config.

    - If profile_name is omitted: returns the same payload as PLUGIN_CONFIG_GET
      (active profile overlay + env override) for backward compatibility.
    - If profile_name is provided: returns base_config overlaid by that profile
      (ignores env override / active setting).
    """

    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)
    profile_name = request.get("profile_name")

    try:
        from_plugin, target_plugin_id = _get_target_plugin_id(request)
    except Exception as e:
        send_response(request.get("from_plugin"), request_id, None, str(e), timeout=timeout)
        return

    if target_plugin_id != from_plugin:
        _deny_cross_plugin_access(
            from_plugin=from_plugin,
            request_id=request_id,
            timeout=timeout,
            send_response=send_response,
        )
        return

    try:
        from plugin.server.config_service import load_plugin_config, load_plugin_base_config

        if profile_name is None:
            data = load_plugin_config(target_plugin_id)
            send_response(from_plugin, request_id, data, None, timeout=timeout)
            return

        if not isinstance(profile_name, str) or not profile_name.strip():
            send_response(from_plugin, request_id, None, "Invalid profile_name", timeout=timeout)
            return

        from plugin.server.config_service import get_plugin_profile_config

        base = load_plugin_base_config(target_plugin_id)
        overlay = get_plugin_profile_config(target_plugin_id, profile_name.strip())

        base_cfg = base.get("config") if isinstance(base, dict) else None
        overlay_cfg = overlay.get("config") if isinstance(overlay, dict) else None
        if not isinstance(base_cfg, dict):
            base_cfg = {}
        if not isinstance(overlay_cfg, dict):
            overlay_cfg = {}

        # Apply same semantics as config_service._apply_user_config_profiles: forbid overriding [plugin]
        if "plugin" in overlay_cfg:
            send_response(
                from_plugin,
                request_id,
                None,
                "Profile config must not define top-level 'plugin' section.",
                timeout=timeout,
            )
            return

        from plugin.server.config_service import deep_merge

        merged = dict(base_cfg)
        for k, v in overlay_cfg.items():
            if k == "plugin":
                continue
            if isinstance(merged.get(k), dict) and isinstance(v, dict):
                merged[k] = deep_merge(merged[k], v)
            else:
                merged[k] = v

        # Keep payload shape consistent with load_plugin_config
        base["config"] = merged
        base["effective_profile"] = profile_name.strip()
        send_response(from_plugin, request_id, base, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)


async def handle_plugin_config_update(request: Dict[str, Any], send_response) -> None:
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    try:
        from_plugin, target_plugin_id = _get_target_plugin_id(request)
    except Exception as e:
        send_response(request.get("from_plugin"), request_id, None, str(e), timeout=timeout)
        return

    if target_plugin_id != from_plugin:
        send_response(
            from_plugin,
            request_id,
            None,
            "Permission denied: can only update own config",
            timeout=timeout,
        )
        return

    updates = request.get("updates")
    if not isinstance(updates, dict):
        send_response(from_plugin, request_id, None, "Invalid updates: must be a dict", timeout=timeout)
        return

    try:
        from plugin.server.config_service import update_plugin_config

        result = update_plugin_config(target_plugin_id, updates)
        send_response(from_plugin, request_id, result, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
