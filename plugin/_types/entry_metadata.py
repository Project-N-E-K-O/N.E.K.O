"""Transportable entry controls shared by config registration and metadata IPC.

Source selection/precedence belongs to the caller. Only present fields are
selected: an explicit false, null, empty list or empty mapping is not a fallback.
Python validation models and callables stay in the plugin process.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Mapping


ENTRY_CONTRACT_FIELDS = (
    "kind",
    "auto_start",
    "enabled",
    "dynamic",
    "persist",
    "model_validate",
    "timeout",
    "llm_result_fields",
    "llm_result_schema",
    "return_message",
    "metadata",
    "extra",
    "quick_action",
    "quick_action_config",
    "output_schema",
    "strict_output",
)

# Contract fields added after the packaged metadata format was fixed. They
# are opt-in: an entry that does not use one must produce the same bytes as
# before, so writers leave them out while unset (None / False), and readers
# treat a missing field as unknown.
ENTRY_OPT_IN_CONTRACT_FIELDS = (
    "output_schema",
    "strict_output",
)

# Contract fields every hand-built entry projection (IPC registration,
# list_entries, registry previews, GET /plugins entries) writes with its own
# shaping or leaves out on purpose. One shared set so the projections cannot
# drift apart; any field not listed is carried by
# unhandled_entry_contract_fields, so a new contract field reaches all of them.
ENTRY_PROJECTION_SHAPED_FIELDS = frozenset({
    "kind", "auto_start", "enabled", "dynamic", "persist", "model_validate",
    "timeout", "llm_result_fields", "llm_result_schema", "return_message",
    "metadata", "extra", "quick_action", "quick_action_config",
})

# Wire shapes of the contract fields, for code that coerces declared values
# (registry config overlay, ``neko-plugin validate``). Fields in neither tuple
# have their own rules at each consumer: ``kind`` (text), ``persist``
# (tri-state), ``timeout`` (number), ``llm_result_fields`` (string list) and
# ``return_message`` (text or i18n mapping).
ENTRY_CONTRACT_BOOL_FIELDS = (
    "auto_start",
    "enabled",
    "dynamic",
    "model_validate",
    "quick_action",
    "strict_output",
)
ENTRY_CONTRACT_MAPPING_FIELDS = (
    "llm_result_schema",
    "metadata",
    "extra",
    "quick_action_config",
    "output_schema",
)


def entry_contract_fields(source: object) -> dict[str, object]:
    """Select controls; callers copy or JSON-normalize values at their boundary."""
    missing = object()
    fields: dict[str, object] = {}
    for name in ENTRY_CONTRACT_FIELDS:
        value = (
            source.get(name, missing)
            if isinstance(source, Mapping)
            else getattr(source, name, missing)
        )
        if value is not missing:
            fields[name] = value
    return fields


def drop_unset_opt_in_fields(payload: dict[str, object]) -> dict[str, object]:
    """Remove opt-in contract fields that are unset, in place; returns ``payload``."""
    for name in ENTRY_OPT_IN_CONTRACT_FIELDS:
        if name in payload and (payload[name] is None or payload[name] is False):
            del payload[name]
    return payload


def unhandled_entry_contract_fields(
    source: object,
    handled: Collection[str],
) -> dict[str, object]:
    """Contract fields a hand-built entry projection does not write itself.

    Entry projections (``/plugins`` entries, previews, IPC registration
    payloads, ``list_entries``) build explicit dicts with per-field shaping.
    Each one names the contract fields it writes or deliberately leaves out;
    every other field in ``ENTRY_CONTRACT_FIELDS`` is returned here so that a
    field added to the contract reaches every projection instead of being
    dropped silently by the ones nobody remembered to edit.

    ``None`` and ``False`` count as "not set": contract fields are opt-in, and
    an entry that does not use one keeps a byte-identical projection. Mapping
    and list values are deep-copied (schemas nest) so a caller editing a
    projection can never reach the entry's metadata.
    """
    carried: dict[str, object] = {}
    for name, value in entry_contract_fields(source).items():
        if name in handled or value is None or value is False:
            continue
        if isinstance(value, (Mapping, list)):
            value = detached_copy(value)
        carried[name] = value
    return carried


def detached_copy(value: object) -> object:
    """A deep copy, so a projection or a registration never aliases the caller's containers.

    When something inside cannot be deep-copied (a ``MappingProxyType``
    default, a lock in metadata), every mapping and list level is still
    copied and only the uncopyable leaves are shared.
    """
    try:
        return copy.deepcopy(value)
    except Exception:
        return _copy_containers(value, {})


def _copy_containers(value: object, memo: dict[int, object]) -> object:
    if not isinstance(value, (Mapping, list)):
        try:
            return copy.deepcopy(value)
        except Exception:
            return value
    if id(value) in memo:
        return memo[id(value)]
    if isinstance(value, Mapping):
        copied: dict[object, object] = {}
        memo[id(value)] = copied
        for key, item in value.items():
            copied[key] = _copy_containers(item, memo)
        return copied
    copied_list: list[object] = []
    memo[id(value)] = copied_list
    copied_list.extend(_copy_containers(item, memo) for item in value)
    return copied_list
