from __future__ import annotations

from typing import Literal

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


def test_int_exclusive_bounds_are_exposed_as_closed_ui_bounds() -> None:
    class _Settings(PluginSettings):
        count: int = SettingsField(1, hot=True, gt=0, lt=10)

    descriptor = module._build_descriptor_for_field(
        plugin_id="demo",
        plugin_name="Demo",
        field_name="count",
        field_info=_Settings.model_fields["count"],
        annotation=_Settings.model_fields["count"].annotation,
        current_value=1,
    )

    assert descriptor is not None
    assert descriptor.min == 1
    assert descriptor.max == 9


def test_optional_literal_fields_build_dropdown() -> None:
    class _Settings(PluginSettings):
        mode: Literal["auto", "manual"] | None = SettingsField(None, hot=True)

    descriptor = module._build_descriptor_for_field(
        plugin_id="demo",
        plugin_name="Demo",
        field_name="mode",
        field_info=_Settings.model_fields["mode"],
        annotation=_Settings.model_fields["mode"].annotation,
        current_value="auto",
    )

    assert descriptor is not None
    assert descriptor.control == "dropdown"
    assert descriptor.options == ["auto", "manual"]
