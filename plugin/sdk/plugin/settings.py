"""PluginSettings base class and helpers for typed plugin configuration.

Provides a Pydantic v2 BaseModel subclass that plugin developers inherit
to declare their business configuration schema with type validation,
defaults, and hot-update field markers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from plugin.logging_config import get_logger

logger = get_logger("sdk.plugin.settings")


class PluginSettings(BaseModel):
    """插件业务配置基类。

    插件开发者继承此类声明配置字段，框架自动从 effective_config 中
    提取对应 toml_section 的数据并创建实例。

    Example::

        class MySettings(PluginSettings):
            model_config = ConfigDict(toml_section="settings")

            timeout: int = SettingsField(default=30, hot=True, description="请求超时(秒)")
            api_key: str = SettingsField(default="", description="API 密钥")
    """

    model_config = ConfigDict(
        extra="ignore",
        validate_default=True,
        toml_section="settings",  # type: ignore[typeddict-unknown-key]
    )


def SettingsField(
    default: Any = PydanticUndefined,
    *,
    hot: bool = False,
    description: str = "",
    **kwargs: Any,
) -> FieldInfo:
    """Wrap ``pydantic.Field`` with an additional ``hot`` marker.

    The *hot* flag is stored in ``json_schema_extra`` so it appears in the
    generated JSON Schema and can be inspected at runtime.

    Args:
        default: Default value for the field.
        hot: Whether this field supports runtime hot-update.
        description: Human-readable description.
        **kwargs: Forwarded to ``pydantic.Field``.
    """
    json_schema_extra = kwargs.pop("json_schema_extra", None) or {}
    json_schema_extra["hot"] = hot
    return Field(
        default=default,
        description=description,
        json_schema_extra=json_schema_extra,
        **kwargs,
    )


def get_hot_fields(settings_cls: type[PluginSettings]) -> set[str]:
    """Return the set of field names marked ``hot=True`` in *settings_cls*.

    Fields without an explicit ``hot`` marker are treated as non-hot.
    """
    hot_names: set[str] = set()
    for name, field_info in settings_cls.model_fields.items():
        extra = field_info.json_schema_extra
        if isinstance(extra, dict) and extra.get("hot") is True:
            hot_names.add(name)
    return hot_names


def create_settings_safe(
    settings_cls: type[PluginSettings],
    config_section: dict[str, Any] | None,
) -> PluginSettings:
    """Create a *settings_cls* instance with per-field fallback on errors.

    If the entire *config_section* validates cleanly, return the instance
    directly.  Otherwise, for each field that fails validation, fall back
    to its declared default and emit a logger warning.

    Args:
        settings_cls: A ``PluginSettings`` subclass.
        config_section: The raw config dict (e.g. from ``[settings]`` in
            plugin.toml).  ``None`` or empty dict means "use all defaults".

    Returns:
        A validated *settings_cls* instance.
    """
    data: dict[str, Any] = dict(config_section) if config_section else {}

    # Fast path: try full validation first.
    try:
        return settings_cls.model_validate(data)
    except ValidationError:
        pass

    # Slow path: validate field-by-field, falling back to defaults.
    safe_data: dict[str, Any] = {}
    for name, field_info in settings_cls.model_fields.items():
        if name not in data:
            # Field not in config — let pydantic use its default.
            continue

        value = data[name]
        # Try validating just this single field by constructing a minimal dict.
        probe: dict[str, Any] = {name: value}
        try:
            settings_cls.model_validate(
                {**_defaults_dict(settings_cls), **probe},
            )
            safe_data[name] = value
        except ValidationError as exc:
            _field_default = field_info.default
            has_default_factory = field_info.default_factory is not None
            if _field_default is PydanticUndefined and not has_default_factory:
                # Required field with no default — we must include the bad
                # value and let the final validation decide.
                safe_data[name] = value
            else:
                default_label = "<factory>" if has_default_factory else _field_default
                logger.warning(
                    "PluginSettings field '{}' validation failed, using default ({}): {}",
                    name,
                    default_label,
                    exc,
                )
                # Omit from safe_data so pydantic uses the declared default.

    try:
        return settings_cls.model_validate(safe_data)
    except ValidationError as exc:
        # Last resort: all defaults.
        logger.warning(
            "PluginSettings full fallback to defaults after per-field recovery failed: {}",
            exc,
        )
        return settings_cls.model_validate({})


def _defaults_dict(settings_cls: type[PluginSettings]) -> dict[str, Any]:
    """Build a dict of field-name → default for fields that have defaults."""
    defaults: dict[str, Any] = {}
    for name, field_info in settings_cls.model_fields.items():
        if field_info.default is not PydanticUndefined:
            defaults[name] = field_info.default
        elif field_info.default_factory is not None:
            defaults[name] = field_info.default_factory()
    return defaults


__all__ = [
    "PluginSettings",
    "SettingsField",
    "get_hot_fields",
    "create_settings_safe",
]
