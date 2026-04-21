"""SettingsActionProvider — auto-generate instant actions from PluginSettings.

For every running plugin that defines a ``PluginSettings`` subclass, this
provider inspects each ``hot=True`` field and emits an ``ActionDescriptor``
with the appropriate control type (toggle / slider / number / dropdown).
"""

from __future__ import annotations

import asyncio
import enum
import typing
from collections.abc import Mapping
from typing import Any, get_args, get_origin

from plugin.logging_config import get_logger
from plugin.server.domain.action_models import ActionDescriptor
from plugin.server.infrastructure.plugin_settings_resolver import resolve_settings_class

logger = get_logger("server.application.actions.settings_provider")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_hot(field_info: Any) -> bool:
    extra = field_info.json_schema_extra
    return isinstance(extra, dict) and extra.get("hot") is True


def _get_constraint(field_info: Any, name: str) -> float | None:
    """Read a Pydantic v2 numeric constraint from field metadata."""
    # Pydantic v2 stores constraints in field_info.metadata as annotated types
    for item in getattr(field_info, "metadata", []):
        val = getattr(item, name, None)
        if val is not None:
            return float(val)
    return None


def _get_enum_options(field_info: Any, annotation: Any) -> list[str] | None:
    """Extract enum / Literal string options from a field."""
    # 1. Check json_schema_extra for explicit enum list
    extra = field_info.json_schema_extra
    if isinstance(extra, dict):
        enum_vals = extra.get("enum")
        if isinstance(enum_vals, (list, tuple)) and enum_vals:
            return [str(v) for v in enum_vals]

    # 2. Check if annotation is a Literal type
    origin = get_origin(annotation)
    if origin is typing.Literal:
        args = get_args(annotation)
        if args:
            return [str(a) for a in args]

    # 3. Check if annotation is an enum.Enum subclass
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return [str(m.value) for m in annotation]

    return None


def _resolve_annotation(annotation: Any) -> type | None:
    """Unwrap Optional / Union to get the core type.

    Handles both ``typing.Union[X, None]`` (Python 3.9) and
    ``X | None`` (PEP 604, Python 3.10+).
    """
    import types as _types

    origin = get_origin(annotation)
    if origin is typing.Union or isinstance(annotation, _types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return args[0] if args else None
    return annotation


def _build_descriptor_for_field(
    plugin_id: str,
    plugin_name: str,
    field_name: str,
    field_info: Any,
    annotation: Any,
    current_value: Any,
) -> ActionDescriptor | None:
    """Map a single PluginSettings field to an ActionDescriptor (or None)."""
    core_type = _resolve_annotation(annotation)

    label = field_info.description or field_name
    base = dict(
        action_id=f"{plugin_id}:settings:{field_name}",
        type="instant",
        label=label,
        description=field_info.description or "",
        category=plugin_name,
        plugin_id=plugin_id,
        keywords=[plugin_id, plugin_name, field_name, label],
    )

    # --- bool → toggle ---
    if core_type is bool:
        return ActionDescriptor(
            **base,
            control="toggle",
            current_value=bool(current_value) if current_value is not None else False,
            icon="🔘",
        )

    # --- int / float → slider or number ---
    if core_type in (int, float):
        ge = _get_constraint(field_info, "ge")
        le = _get_constraint(field_info, "le")
        gt = _get_constraint(field_info, "gt")
        lt = _get_constraint(field_info, "lt")

        # Resolve effective min/max from ge/gt and le/lt
        eff_min = ge if ge is not None else gt
        eff_max = le if le is not None else lt

        if eff_min is not None and eff_max is not None:
            step: float = 1.0 if core_type is int else 0.1
            return ActionDescriptor(
                **base,
                control="slider",
                current_value=current_value,
                min=eff_min,
                max=eff_max,
                step=step,
                icon="🎚",
            )

        # number fallback — one or both bounds missing
        return ActionDescriptor(
            **base,
            control="number",
            current_value=current_value,
            min=eff_min,
            max=eff_max,
            icon="🔢",
        )

    # --- str with enum / Literal → dropdown ---
    if core_type is str:
        options = _get_enum_options(field_info, annotation)
        if options:
            return ActionDescriptor(
                **base,
                control="dropdown",
                current_value=current_value,
                options=options,
                icon="📋",
            )
        # str without enum → skip (no sensible control)
        return None

    # --- Enum subclass → dropdown ---
    if isinstance(core_type, type) and issubclass(core_type, enum.Enum):
        options = [str(m.value) for m in core_type]
        if isinstance(current_value, enum.Enum):
            display_value = str(current_value.value)
        elif current_value is not None:
            display_value = str(current_value)
        else:
            display_value = None
        return ActionDescriptor(
            **base,
            control="dropdown",
            current_value=display_value,
            options=options,
            icon="📋",
        )

    # --- Literal (non-str) → dropdown ---
    origin = get_origin(annotation)
    if origin is typing.Literal:
        args = get_args(annotation)
        if args:
            return ActionDescriptor(
                **base,
                control="dropdown",
                current_value=str(current_value) if current_value is not None else None,
                options=[str(a) for a in args],
                icon="📋",
            )

    # Unsupported type → skip
    return None


# ---------------------------------------------------------------------------
# Synchronous core (runs in thread)
# ---------------------------------------------------------------------------

def _collect_settings_actions_sync(
    plugin_id_filter: str | None = None,
) -> list[ActionDescriptor]:
    """Collect settings-derived actions (called from a worker thread)."""
    from plugin.core.state import state

    plugins_snapshot = state.get_plugins_snapshot_cached()
    hosts_snapshot: dict[str, Any] = {}
    with state.acquire_plugin_hosts_read_lock():
        hosts_snapshot = dict(state.plugin_hosts)

    actions: list[ActionDescriptor] = []

    for pid, meta_raw in plugins_snapshot.items():
        if plugin_id_filter is not None and pid != plugin_id_filter:
            continue

        # Only running plugins (must have a host)
        host = hosts_snapshot.get(pid)
        if host is None:
            continue

        if not isinstance(meta_raw, Mapping):
            continue
        meta: dict[str, Any] = dict(meta_raw)

        plugin_name = str(meta.get("name") or pid)

        # Resolve the PluginSettings class
        settings_cls = resolve_settings_class(pid, host=host)
        if settings_cls is None:
            continue

        # Read current effective config for this plugin's toml_section
        toml_section = settings_cls.model_config.get("toml_section", "settings")

        # Read current config from the plugin's TOML file
        current_section: dict[str, Any] = {}
        try:
            from plugin.config.service import load_plugin_config

            config_data = load_plugin_config(pid, validate=False)
            if isinstance(config_data, dict):
                section = config_data.get(toml_section)
                if isinstance(section, Mapping):
                    current_section = dict(section)
        except Exception as exc:
            logger.debug(
                "Failed to load config for plugin {} section {}: {}",
                pid, toml_section, repr(exc),
            )

        # Iterate fields
        for field_name, field_info in settings_cls.model_fields.items():
            if not _is_hot(field_info):
                continue

            annotation = field_info.annotation
            current_value = current_section.get(field_name, field_info.default)

            descriptor = _build_descriptor_for_field(
                plugin_id=pid,
                plugin_name=plugin_name,
                field_name=field_name,
                field_info=field_info,
                annotation=annotation,
                current_value=current_value,
            )
            if descriptor is not None:
                actions.append(descriptor)

    return actions


# ---------------------------------------------------------------------------
# Public provider
# ---------------------------------------------------------------------------

class SettingsActionProvider:
    """Generate ``ActionDescriptor`` items from ``PluginSettings`` hot fields."""

    async def get_actions(
        self,
        plugin_id: str | None = None,
    ) -> list[ActionDescriptor]:
        return await asyncio.to_thread(_collect_settings_actions_sync, plugin_id)
