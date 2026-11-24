"""In-process lookup of model slots for callers outside the plugin server.

The agent process (the NekoPower host) runs task agents on slots of the model
panel. It reads the same ``plugin_models.json`` and resolves host-follow slots
with the same code as the plugin gateway, so it works whether or not the
plugin server is running. Nothing here writes: seeding, bindings and edits
stay with the plugin server.

Credentials live only in the returned ``ResolvedModel`` (kept out of its
repr) and are never logged or persisted. Both functions read files
synchronously; async callers run them in a worker thread.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from plugin.server.domain.errors import ServerDomainError
from plugin.server.domain.model_config import ModelSlot, PluginModelsConfig
from plugin.server.infrastructure.host_model_slots import (
    client_headers,
    is_free_host_key,
    resolve_host_slot,
)
from plugin.server.infrastructure.model_config_store import ModelConfigStore
from plugin.server.model_gateway.errors import ModelGatewayError

SLOT_NOT_FOUND = "slot_not_found"
MODEL_CONFIG_UNREADABLE = "model_config_unreadable"
HOST_API_NOT_CONFIGURED = "host_api_not_configured"
HOST_API_UNSUPPORTED = "host_api_unsupported"
CAPABILITY_MISSING = "capability_missing"


class ModelSlotUnavailable(Exception):
    """A slot cannot serve a request; ``code`` says why (never a credential)."""

    def __init__(self, code: str, slot_id: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.slot_id = slot_id
        self.message = message


@dataclass(frozen=True)
class SlotSummary:
    """One slot as a chooser shows it: no endpoint, no credential."""

    slot_id: str
    name: str
    source: str
    host_api: str | None
    capabilities: tuple[str, ...]
    configured: bool
    model: str
    protocol: str | None
    free: bool
    error_code: str | None


@dataclass(frozen=True)
class ResolvedModel:
    """Everything needed to call a slot's model, resolved right now."""

    slot_id: str
    name: str
    source: str
    host_api: str | None
    base_url: str
    model: str
    protocol: str
    capabilities: tuple[str, ...]
    timeout_seconds: float
    free: bool
    headers: dict[str, str] = field(default_factory=dict)
    api_key: str = field(default="", repr=False)


def _read_config(config_manager: Any = None) -> PluginModelsConfig:
    try:
        return ModelConfigStore(config_manager).read()
    except ServerDomainError as exc:
        raise ModelSlotUnavailable(MODEL_CONFIG_UNREADABLE, "", exc.message) from None


def _summary(slot_id: str, slot: ModelSlot, config_manager: Any) -> SlotSummary:
    common = {
        "slot_id": slot_id, "name": slot.name, "source": slot.source,
        "host_api": slot.host_api, "capabilities": tuple(slot.capabilities),
    }
    if slot.source != "host_follow":
        return SlotSummary(
            **common, configured=True, model=slot.model, protocol=slot.protocol,
            free=is_free_host_key(slot.api_key), error_code=None,
        )
    try:
        resolved = resolve_host_slot(slot, config_manager=config_manager, for_task_agents=True)
    except ModelGatewayError as exc:
        return SlotSummary(
            **common, configured=False, model="", protocol=None, free=False, error_code=exc.code,
        )
    return SlotSummary(
        **common, configured=True, model=resolved.model, protocol=resolved.protocol,
        free=is_free_host_key(resolved.api_key), error_code=None,
    )


def list_model_slots(*, config_manager: Any = None) -> list[SlotSummary]:
    """All slots in panel order, with what each resolves to right now.

    Raises ``ModelSlotUnavailable`` with ``model_config_unreadable`` when the
    document cannot be read; a host-follow slot whose main-app tier is not
    usable is listed with ``configured=False`` and its ``error_code``.
    """
    config = _read_config(config_manager)
    return [_summary(slot_id, slot, config_manager) for slot_id, slot in config.slots.items()]


def resolve_model_slot(
    slot_id: str,
    *,
    require: Iterable[str] = (),
    config_manager: Any = None,
) -> ResolvedModel:
    """Resolve one slot to an endpoint, model, protocol and credential.

    Failure codes (``ModelSlotUnavailable.code``):

    - ``slot_not_found``: no slot has this id (deleted, or never existed).
    - ``model_config_unreadable``: the slot document is unreadable or invalid.
    - ``host_api_not_configured``: a host-follow slot's main-app tier has no
      endpoint or model, or the main-app configuration cannot be read.
    - ``host_api_unsupported``: the followed tier is not an HTTP chat endpoint.
    - ``capability_missing``: the slot does not declare every ``require``d
      capability (``text``, ``image_input``, ``tool_calling``, ``streaming``).

    The fallback slot is not followed: a caller that needs one picks it.
    """
    config = _read_config(config_manager)
    slot = config.slots.get(slot_id)
    if slot is None:
        raise ModelSlotUnavailable(SLOT_NOT_FOUND, slot_id, "No model slot has this id")
    missing = sorted(set(require) - set(slot.capabilities))
    if missing:
        raise ModelSlotUnavailable(
            CAPABILITY_MISSING, slot_id, "The slot does not declare: " + ", ".join(missing),
        )
    try:
        resolved = (
            resolve_host_slot(slot, config_manager=config_manager, for_task_agents=True)
            if slot.source == "host_follow" else slot
        )
    except ModelGatewayError as exc:
        raise ModelSlotUnavailable(exc.code, slot_id, exc.message) from None
    protocol = resolved.protocol
    return ResolvedModel(
        slot_id=slot_id,
        name=slot.name,
        source=slot.source,
        host_api=slot.host_api,
        base_url=resolved.base_url,
        model=resolved.model,
        protocol=protocol,
        capabilities=tuple(slot.capabilities),
        timeout_seconds=slot.timeout_seconds,
        free=is_free_host_key(resolved.api_key),
        headers=client_headers(protocol, resolved.base_url),
        api_key=resolved.api_key,
    )


__all__ = [
    "CAPABILITY_MISSING",
    "HOST_API_NOT_CONFIGURED",
    "HOST_API_UNSUPPORTED",
    "MODEL_CONFIG_UNREADABLE",
    "SLOT_NOT_FOUND",
    "ModelSlotUnavailable",
    "ResolvedModel",
    "SlotSummary",
    "list_model_slots",
    "resolve_model_slot",
]
