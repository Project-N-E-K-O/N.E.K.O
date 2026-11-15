"""Materialize host-follow slots from the main app's model configuration.

Credentials stay in core_config.json; nothing is copied into plugin storage.
Each call resolves a fresh snapshot, so main-app provider/key changes apply to
the next plugin request without any notification hook.
"""
from __future__ import annotations

from typing import Any

from plugin.server.domain.model_config import ModelSlot
from plugin.server.model_gateway.errors import ModelGatewayError

_HOST_API_MODEL_TYPES = {"assist": "conversation", "core": "realtime"}


def _host_model_type(slot: ModelSlot) -> str:
    # The domain validator guarantees host_api is set for host-follow slots.
    return _HOST_API_MODEL_TYPES[slot.host_api or "assist"]


def resolve_host_slot(slot: ModelSlot) -> ModelSlot:
    """Return an executable manual-shaped snapshot of a host-follow slot."""
    if slot.source != "host_follow":
        return slot
    from utils.config_manager import get_config_manager

    try:
        config = get_config_manager().get_model_api_config(_host_model_type(slot))
    except Exception as exc:
        raise ModelGatewayError(
            "host_api_not_configured", "The main app API configuration could not be read", 409, "model",
        ) from exc
    return materialize_host_slot(slot, config)


def materialize_host_slot(slot: ModelSlot, config: dict[str, Any]) -> ModelSlot:
    base_url = str(config.get("base_url") or "").strip()
    model = str(config.get("model") or "").strip()
    if not base_url or not model:
        raise ModelGatewayError(
            "host_api_not_configured", "Configure the main app API before using this slot", 409, "model",
        )
    if not base_url.lower().startswith(("http://", "https://")):
        raise ModelGatewayError(
            "host_api_unsupported",
            "The selected main app API is not an HTTP chat endpoint (realtime providers use WebSocket)",
            409, "model",
        )
    protocol = "anthropic_messages" if config.get("provider_type") == "anthropic" else "openai_chat"
    temperature = slot.defaults.temperature
    if protocol == "anthropic_messages" and temperature is not None and temperature > 1:
        # Saving already rejects this intersection; only a legacy document can
        # reach here. Fail before any upstream request (and before quota).
        raise ModelGatewayError(
            "model_defaults_incompatible",
            "The main app API resolved to Anthropic, which allows temperature up to 1; "
            "lower this slot's default temperature",
            409, "model",
        )
    return slot.model_copy(update={
        "protocol": protocol,
        "base_url": base_url,
        "model": model,
        "api_key": str(config.get("api_key") or ""),
    })


def is_free_host_key(api_key: str) -> bool:
    """Built-in free-tier credentials are metered; user-supplied keys are not."""
    return str(api_key or "").strip() == "free-access"


def is_free_host_config(config: dict[str, Any]) -> bool:
    return is_free_host_key(str(config.get("api_key") or ""))


def host_slot_status(slot: ModelSlot) -> dict[str, Any]:
    """Read-only display summary for the management UI; never includes credentials."""
    if slot.source != "host_follow":
        return {}
    from utils.config_manager import get_config_manager

    try:
        config = get_config_manager().get_model_api_config(_host_model_type(slot))
        materialize_host_slot(slot, config)
    except ModelGatewayError as exc:
        return {"configured": False, "error_code": exc.code, "message": exc.message}
    except Exception:
        return {"configured": False, "error_code": "host_api_not_configured"}
    return {
        "configured": True,
        "model": str(config.get("model") or ""),
        "protocol": "anthropic_messages" if config.get("provider_type") == "anthropic" else "openai_chat",
        "free": is_free_host_config(config),
    }