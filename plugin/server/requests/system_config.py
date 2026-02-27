from __future__ import annotations

from typing import Any, Dict


def _jsonify_value(v: Any) -> Any:
    try:
        from pathlib import Path

        if isinstance(v, Path):
            return str(v)
    except Exception:
        pass

    if isinstance(v, (str, int, float, bool)) or v is None:
        return v

    if isinstance(v, (list, tuple)):
        return [_jsonify_value(x) for x in v]

    if isinstance(v, dict):
        return {str(k): _jsonify_value(val) for k, val in v.items()}

    return str(v)


async def handle_plugin_system_config_get(request: Dict[str, Any], send_response) -> None:
    from_plugin = request.get("from_plugin")
    request_id = request.get("request_id")
    timeout = request.get("timeout", 5.0)

    try:
        import plugin.settings as settings

        payload: Dict[str, Any] = {
            "config": {},
        }

        keys = getattr(settings, "__all__", None)
        if not isinstance(keys, (list, tuple)):
            keys = [k for k in dir(settings) if isinstance(k, str) and k.isupper()]

        for k in keys:
            if not isinstance(k, str):
                continue
            try:
                payload["config"][k] = _jsonify_value(getattr(settings, k))
            except Exception:
                continue

        send_response(from_plugin, request_id, payload, None, timeout=timeout)
    except Exception as e:
        send_response(from_plugin, request_id, None, str(e), timeout=timeout)
