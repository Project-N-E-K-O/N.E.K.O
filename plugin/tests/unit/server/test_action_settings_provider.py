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


def test_collect_resolves_plugin_name_i18n_ref(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``$i18n`` refs in plugin meta ``name`` must be resolved before the
    plugin_name is used as descriptor category — otherwise the command palette
    surfaces ``{'$i18n': '...'}`` reprs."""
    import threading
    from unittest.mock import patch

    # Plugin i18n bundle.
    plugin_dir = tmp_path / "i18n_demo"
    plugin_dir.mkdir()
    translations = plugin_dir / "i18n"
    translations.mkdir()
    (translations / "en.json").write_text(
        '{"plugin.name": "Resolved Demo"}', encoding="utf-8"
    )
    config_path = plugin_dir / "config.toml"
    config_path.write_text("", encoding="utf-8")

    class _Settings(PluginSettings):
        enabled: bool = SettingsField(default=False, hot=True)

    class _FakeState:
        def __init__(self) -> None:
            self.plugin_hosts = {"demo": object()}
            self._lock = threading.RLock()

        def get_plugins_snapshot_cached(self, **_kw):
            return {
                "demo": {
                    "name": {"$i18n": "plugin.name", "default": "Resolved Demo"},
                    "config_path": str(config_path),
                }
            }

        def acquire_plugin_hosts_read_lock(self):
            return self._lock

    fake_state = _FakeState()
    monkeypatch.setattr(module, "resolve_settings_class", lambda pid, host=None: _Settings)

    with patch("plugin.core.state.state", fake_state):
        actions = module._collect_settings_actions_sync()

    assert len(actions) == 1
    assert actions[0].category == "Resolved Demo"
    assert "$i18n" not in actions[0].category
