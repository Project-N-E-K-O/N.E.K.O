"""Materialize host-follow slots from the main app's model configuration.

Credentials stay in core_config.json; nothing is copied into plugin storage.
Each call resolves a fresh snapshot, so main-app provider/key changes apply to
the next plugin request without any notification hook.
"""
from __future__ import annotations

import copy
from typing import Any

from plugin.server.domain.model_config import HOST_API_MODEL_TYPES, ModelSlot
from plugin.server.model_gateway.errors import ModelGatewayError


def _host_model_type(slot: ModelSlot) -> str:
    # The domain validator guarantees host_api is one of the followable tiers.
    return HOST_API_MODEL_TYPES[slot.host_api or "agent"]


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


# Request fields a provider extra_body must never inject into plugin calls:
# tool use and the streaming shape are the plugin's (and gateway's) decision.
_PLUGIN_OWNED_FIELDS = frozenset({
    "tools", "tool_choice", "parallel_tool_calls", "functions", "function_call",
    "stream", "stream_options", "n",
})
# Never merged from provider settings either: plugin model requests carry no
# temperature (scripts/check_no_temperature.py).
_NEVER_MERGED_FIELDS = frozenset({"temperature"})


def adapt_host_follow_request(slot: ModelSlot, request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Shape a host-follow OpenAI request the way the main app sends it.

    The main app adapts every task agent call per provider/model; a slot that
    follows the same model must send the same request or plugin calls run with
    thinking on, ignore their output cap and miss the prompt cache. Mirrors
    ``nekopower/dsh/forward.py:prepare_request`` (itself a mirror of
    ``utils.llm_client.ChatOpenAI``):

    - the output budget moves to the field the endpoint honours
      (``get_token_limit_field``), value unchanged;
    - the task agent extra_body (``get_agent_extra_body`` +
      ``rewrite_router_reasoning``) is merged in, never overriding a field the
      request already carries and never adding tools;
    - provider cache headers (``get_cache_kwargs``) are returned for the send.

    Manual slots and the Messages protocol pass through unchanged. The result
    is deterministic, so the stream_options retry re-derives the same request.
    """
    if slot.source != "host_follow" or slot.protocol != "openai_chat":
        return request, {}
    from config.providers import (
        get_agent_extra_body,
        get_cache_kwargs,
        get_token_limit_field,
        rewrite_router_reasoning,
    )

    base_url = slot.base_url
    adapted = dict(request)
    budget_field = get_token_limit_field(base_url)
    for field in ("max_completion_tokens", "max_tokens"):
        # prepare_chat_request guarantees at most one budget field is present.
        if field in adapted and field != budget_field:
            adapted[budget_field] = adapted.pop(field)
    extra = get_agent_extra_body(slot.model, base_url) or None
    extra, effort = rewrite_router_reasoning(base_url, slot.model, extra)
    if effort is not None:
        adapted.setdefault("reasoning_effort", effort)
    for key, value in (extra or {}).items():
        # Only model settings (e.g. thinking controls) are merged. Tools stay
        # under the plugin's control: a provider default such as step-2-mini's
        # built-in web_search would otherwise bypass the tool_calling
        # capability check done in prepare_chat_request. Explicit plugin
        # parameters (and validated core fields) win.
        if key in _PLUGIN_OWNED_FIELDS or key in _NEVER_MERGED_FIELDS:
            continue
        # Deliberately unlike forward.py's body.update(extra) (provider wins):
        # here the plugin's explicit request wins over provider defaults.
        if key not in adapted:
            adapted[key] = copy.deepcopy(value)
    headers = {
        str(name): str(value)
        for name, value in (get_cache_kwargs(base_url).get("default_headers") or {}).items()
    }
    return adapted, headers


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