from __future__ import annotations

import pytest

from plugin.config.schema import ConfigValidationError, validate_plugin_config

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
