"""Materialize host-follow slots from the main app's model configuration.

Credentials stay in core_config.json; nothing is copied into plugin storage.
Each call resolves a fresh snapshot, so main-app provider/key changes apply to
the next plugin request without any notification hook.
"""
from __future__ import annotations

from typing import Any

from plugin.server.domain.model_config import HOST_API_MODEL_TYPES, ModelSlot
from plugin.server.model_gateway.errors import ModelGatewayError


def _host_model_type(slot: ModelSlot) -> str:
    # The domain validator guarantees host_api is one of the followable tiers.
    return HOST_API_MODEL_TYPES[slot.host_api or "conversation"]


def host_protocol(config: dict[str, Any]) -> str:
    """Pick the wire protocol exactly as the main app's client factory does.

    The main app also recognizes Anthropic / Kimi Code endpoints by URL, so a
    custom API at such an address keeps ``provider_type='openai_compatible'``
    yet is called with the Messages API; diverging here would send OpenAI JSON
    to an endpoint that only speaks Anthropic.
    """
    from utils.llm_client.anthropic_client import _is_anthropic_endpoint

    base_url = str(config.get("base_url") or "").strip()
    if _is_anthropic_endpoint(base_url, config.get("provider_type")):
        return "anthropic_messages"
    return "openai_chat"


def resolve_host_slot(slot: ModelSlot, *, config_manager: Any = None) -> ModelSlot:
    """Return an executable manual-shaped snapshot of a host-follow slot."""
    if slot.source != "host_follow":
        return slot
    if config_manager is None:
        from utils.config_manager import get_config_manager

        config_manager = get_config_manager()
    try:
        config = config_manager.get_model_api_config(_host_model_type(slot))
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
            "The followed main app model is not an HTTP chat endpoint (e.g. a WebSocket address)",
            409, "model",
        )
    protocol = host_protocol(config)
    # Temperature compatibility is judged on the effective request value in
    # prepare_chat_request: an explicit valid override must not be rejected
    # because of an unused (legacy) saved default.
    return slot.model_copy(update={
        "protocol": protocol,
        "base_url": base_url,
        "model": model,
        "api_key": str(config.get("api_key") or ""),
    })


def client_headers(protocol: str, base_url: str) -> dict[str, str]:
    """Extra request headers an endpoint requires of every client.

    Kimi Code serves its Messages API only to the coding client's User-Agent;
    the main app's client factory (utils/llm_client/factory.py) sends the
    same value.
    """
    from utils.llm_client.anthropic_client import _is_kimi_code_anthropic_base_url

    if protocol == "anthropic_messages" and _is_kimi_code_anthropic_base_url(base_url):
        return {"User-Agent": "claude-code/0.1.0"}
    return {}


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
        "protocol": host_protocol(config),
        "free": is_free_host_config(config),
    }