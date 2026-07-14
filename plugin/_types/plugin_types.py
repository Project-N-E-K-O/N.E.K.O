"""Canonical plugin type policy shared by models, runtime, and tooling."""

from __future__ import annotations

from typing import Final, Literal, TypeAlias, get_args


PluginType: TypeAlias = Literal["plugin", "adapter"]

SUPPORTED_PLUGIN_TYPES: Final[frozenset[str]] = frozenset(get_args(PluginType))
SCAFFOLDABLE_PLUGIN_TYPES: Final[frozenset[str]] = SUPPORTED_PLUGIN_TYPES


def format_plugin_type_choice_error(
    label: str,
    *,
    allowed: frozenset[str] = SUPPORTED_PLUGIN_TYPES,
) -> str:
    """Return one stable Chinese/English/Japanese invalid-type diagnostic."""

    choices = ", ".join(sorted(allowed))
    return (
        f"{label} 必须是以下类型之一：{choices}"
        f" / {label} must be one of: {choices}"
        f" / {label} は次のいずれかである必要があります: {choices}"
    )


def require_supported_plugin_type(value: object, *, label: str = "plugin.type") -> str:
    """Validate an untrusted plugin type before model literal validation."""

    if not isinstance(value, str) or value not in SUPPORTED_PLUGIN_TYPES:
        raise ValueError(format_plugin_type_choice_error(label))
    return value


def format_unsupported_plugin_type(plugin_type: object, *, plugin_id: object) -> str:
    """Return the runtime rejection used at every plugin registration boundary."""

    choices = ", ".join(sorted(SUPPORTED_PLUGIN_TYPES))
    return (
        f"插件 {plugin_id} 使用了不支持的类型 {plugin_type!r}；支持的类型：{choices}。"
        f" / Plugin {plugin_id} has unsupported type={plugin_type!r}; "
        f"supported types are {choices}."
        f" / プラグイン {plugin_id} の型 {plugin_type!r} は未対応です。"
        f"対応している型: {choices}。"
    )


def format_removed_plugin_host() -> str:
    """Return the shared diagnostic for the removed Extension host table."""

    return (
        "[plugin.host] 已随 Extension 插件类型移除。"
        " / [plugin.host] was removed with the Extension plugin type."
        " / [plugin.host] は Extension プラグイン型とともに削除されました。"
    )


def format_unsupported_scaffold_type(plugin_type: object) -> str:
    """Return the shared rejection for types that cannot start new projects."""

    choices = ", ".join(sorted(SCAFFOLDABLE_PLUGIN_TYPES))
    return (
        f"插件类型 {plugin_type!r} 不支持创建新脚手架；请选择：{choices}。"
        f" / Plugin type {plugin_type!r} is not supported for new scaffolds; "
        f"choose one of: {choices}."
        f" / プラグイン型 {plugin_type!r} は新規スキャフォールドではサポートされていません。"
        f"次のいずれかを選択してください: {choices}。"
    )


__all__ = [
    "PluginType",
    "SCAFFOLDABLE_PLUGIN_TYPES",
    "SUPPORTED_PLUGIN_TYPES",
    "format_plugin_type_choice_error",
    "format_removed_plugin_host",
    "format_unsupported_plugin_type",
    "format_unsupported_scaffold_type",
    "require_supported_plugin_type",
]
