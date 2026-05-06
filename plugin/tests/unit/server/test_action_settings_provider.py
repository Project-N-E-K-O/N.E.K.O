from __future__ import annotations

import pytest

from plugin.sdk.plugin.settings import PluginSettings, SettingsField
from plugin.server.application.actions import settings_provider as module

pytestmark = pytest.mark.plugin_unit


def test_is_hot_reads_settings_field_metadata_for_callable_schema_extra() -> None:
    def add_marker(schema: dict[str, object]) -> None:
        schema["x-marker"] = "ok"

    class _Settings(PluginSettings):
        value: int = SettingsField(1, hot=True, json_schema_extra=add_marker)

    assert module._is_hot(_Settings.model_fields["value"]) is True
