from __future__ import annotations

from collections.abc import Mapping

from plugin.server.domain.errors import ServerDomainError


def _raise_invalid_config(message: str) -> None:
    raise ServerDomainError(
        code="INVALID_CONFIG_UPDATE",
        message=message,
        status_code=400,
        details={},
    )


def _ensure_mapping(value: object, *, field: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        _raise_invalid_config(f"{field} must be an object")
    return value


def _ensure_string(value: object, *, field: str, max_length: int | None = None) -> str:
    if not isinstance(value, str):
        _raise_invalid_config(f"{field} must be a string")
    if max_length is not None and len(value) > max_length:
        _raise_invalid_config(f"{field} is too long (max {max_length} characters)")
    return value


def _check_forbidden_paths(value: object, *, path: str = "") -> None:
    if isinstance(value, Mapping):
        for key_obj, nested_value in value.items():
            if not isinstance(key_obj, str):
                continue
            current_path = f"{path}.{key_obj}" if path else key_obj
            if current_path in {"plugin.id", "plugin.entry"}:
                _raise_invalid_config(
                    f"Cannot modify critical field '{current_path}'. This field is protected."
                )
            _check_forbidden_paths(nested_value, path=current_path)
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            indexed_path = f"{path}[{index}]" if path else f"[{index}]"
            _check_forbidden_paths(item, path=indexed_path)


def _validate_plugin_author(plugin_section: Mapping[object, object]) -> None:
    author_obj = plugin_section.get("author")
    if author_obj is None:
        return
    if not isinstance(author_obj, Mapping):
        _raise_invalid_config("plugin.author must be an object")

    name_obj = author_obj.get("name")
    if name_obj is not None:
        _ensure_string(name_obj, field="plugin.author.name")

    email_obj = author_obj.get("email")
    if email_obj is None:
        return
    email_value = _ensure_string(email_obj, field="plugin.author.email")
    if "@" not in email_value or len(email_value) > 200:
        _raise_invalid_config("plugin.author.email format is invalid")


def _validate_plugin_sdk(plugin_section: Mapping[object, object]) -> None:
    sdk_obj = plugin_section.get("sdk")
    if sdk_obj is None:
        return
    if not isinstance(sdk_obj, Mapping):
        _raise_invalid_config("plugin.sdk must be an object")

    for key in ("recommended", "supported", "untested"):
        value = sdk_obj.get(key)
        if value is not None:
            _ensure_string(value, field=f"plugin.sdk.{key}", max_length=200)

    conflicts_obj = sdk_obj.get("conflicts")
    if conflicts_obj is None or isinstance(conflicts_obj, bool):
        return

    if not isinstance(conflicts_obj, list):
        _raise_invalid_config("plugin.sdk.conflicts must be a list of strings or a boolean")

    for item in conflicts_obj:
        _ensure_string(
            item,
            field="plugin.sdk.conflicts item",
            max_length=200,
        )


def _validate_plugin_dependency(plugin_section: Mapping[object, object]) -> None:
    dependency_obj = plugin_section.get("dependency")
    if dependency_obj is None:
        return
    if not isinstance(dependency_obj, list):
        _raise_invalid_config("plugin.dependency must be a list")

    for item in dependency_obj:
        dependency_item = _ensure_mapping(item, field="plugin.dependency items")
        for key in ("id", "entry", "custom_event"):
            value = dependency_item.get(key)
            if value is not None:
                _ensure_string(value, field=f"plugin.dependency.{key}")

        providers_obj = dependency_item.get("providers")
        if providers_obj is None:
            continue
        if not isinstance(providers_obj, list):
            _raise_invalid_config("plugin.dependency.providers must be a list")
        for provider_item in providers_obj:
            _ensure_string(provider_item, field="plugin.dependency.providers item")


def validate_config_updates(*, updates: object) -> dict[str, object]:
    updates_mapping = _ensure_mapping(updates, field="config")
    normalized_updates: dict[str, object] = {}
    for key_obj, value in updates_mapping.items():
        if not isinstance(key_obj, str):
            _raise_invalid_config("config keys must be strings")
        normalized_updates[key_obj] = value

    _check_forbidden_paths(normalized_updates)

    plugin_obj = normalized_updates.get("plugin")
    if plugin_obj is None:
        return normalized_updates
    if not isinstance(plugin_obj, Mapping):
        _raise_invalid_config("plugin must be an object")

    name_obj = plugin_obj.get("name")
    if name_obj is not None:
        _ensure_string(name_obj, field="plugin.name", max_length=200)

    version_obj = plugin_obj.get("version")
    if version_obj is not None:
        _ensure_string(version_obj, field="plugin.version", max_length=50)

    description_obj = plugin_obj.get("description")
    if description_obj is not None:
        _ensure_string(description_obj, field="plugin.description", max_length=5000)

    _validate_plugin_author(plugin_obj)
    _validate_plugin_sdk(plugin_obj)
    _validate_plugin_dependency(plugin_obj)
    return normalized_updates
