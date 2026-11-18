"""Transportable entry controls shared by config registration and metadata IPC.

Source selection/precedence belongs to the caller. Only present fields are
selected: an explicit false, null, empty list or empty mapping is not a fallback.
Python validation models and callables stay in the plugin process.
"""

from __future__ import annotations

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
    and list values are shallow-copied so projections never alias metadata.
    """
    carried: dict[str, object] = {}
    for name, value in entry_contract_fields(source).items():
        if name in handled or value is None or value is False:
            continue
        if isinstance(value, Mapping):
            value = dict(value)
        elif isinstance(value, list):
            value = list(value)
        carried[name] = value
    return carried
