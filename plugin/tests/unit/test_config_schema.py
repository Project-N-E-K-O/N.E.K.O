from __future__ import annotations

import pytest
from pydantic import ValidationError

from plugin._types.models import PluginMeta
from plugin.config.schema import (
    ConfigValidationError,
    validate_plugin_config,
    validate_plugin_config_partial,
)

pytestmark = pytest.mark.plugin_unit


def _base_config() -> dict[str, object]:
    return {
        "plugin": {
            "id": "schema_demo",
            "name": "Schema Demo",
            "entry": "plugins.schema_demo:SchemaDemoPlugin",
            "type": "plugin",
        },
    }


def test_plugin_runtime_startup_failure_accepts_known_policy() -> None:
    config = _base_config()
    config["plugin_runtime"] = {
        "timeout": 1.5,
        "startup_failure": "warn",
    }

    validated = validate_plugin_config(config)

    assert validated.plugin_runtime is not None
    assert validated.plugin_runtime.timeout == 1.5
    assert validated.plugin_runtime.startup_failure == "warn"


@pytest.mark.parametrize("timeout", [True, 0, -1, 300.1, "bad", float("nan"), float("inf"), float("-inf")])
def test_plugin_runtime_timeout_rejects_invalid_values(timeout: object) -> None:
    config = _base_config()
    config["plugin_runtime"] = {"timeout": timeout}

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_plugin_config(config)

    assert "timeout" in str(exc_info.value)


def test_plugin_runtime_startup_failure_rejects_unknown_policy() -> None:
    config = _base_config()
    config["plugin_runtime"] = {"startup_failure": "strict"}

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_plugin_config(config)

    assert "startup_failure" in str(exc_info.value)


@pytest.mark.parametrize("plugin_type", ["plugin", "adapter"])
def test_plugin_models_accept_active_plugin_types(plugin_type: str) -> None:
    meta = PluginMeta(id=f"{plugin_type}_demo", name="Demo", type=plugin_type)
    config = _base_config()
    plugin = config["plugin"]
    assert isinstance(plugin, dict)
    plugin["type"] = plugin_type

    validated = validate_plugin_config(config)

    assert meta.type == plugin_type
    assert validated.plugin.type == plugin_type


def test_plugin_meta_rejects_removed_script_type() -> None:
    with pytest.raises(ValidationError) as exc_info:
        PluginMeta(id="legacy_script", name="Legacy Script", type="script")

    message = str(exc_info.value)
    assert "type" in message
    assert "必须" in message and "must be one of" in message and "必要があります" in message


def test_plugin_config_schema_rejects_removed_script_type() -> None:
    config = _base_config()
    plugin = config["plugin"]
    assert isinstance(plugin, dict)
    plugin["type"] = "script"

    with pytest.raises(ConfigValidationError) as exc_info:
        validate_plugin_config(config)

    assert exc_info.value.field == "plugin.type"
    assert "必须" in exc_info.value.message
    assert "must be one of" in exc_info.value.message
    assert "必要があります" in exc_info.value.message


def test_partial_plugin_config_rejects_removed_script_type() -> None:
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_plugin_config_partial({"plugin": {"type": "script"}})

    assert exc_info.value.field == "plugin.type"


@pytest.mark.parametrize("plugin_type", ["plugin", "adapter", "extension"])
def test_partial_plugin_config_accepts_loadable_types(plugin_type: str) -> None:
    config = {"plugin": {"type": plugin_type}}

    assert validate_plugin_config_partial(config) is config


def test_plugin_config_schema_keeps_deprecated_extension_loadable() -> None:
    config = _base_config()
    plugin = config["plugin"]
    assert isinstance(plugin, dict)
    plugin["type"] = "extension"
    plugin["host"] = {"plugin_id": "host_plugin"}

    validated = validate_plugin_config(config)

    assert validated.plugin.type == "extension"
    assert validated.plugin.host is not None
    assert validated.plugin.host.plugin_id == "host_plugin"
