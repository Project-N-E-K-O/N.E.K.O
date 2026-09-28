"""Keep bundled configuration forms aligned with shipped defaults and locales."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.core.build_rules import load_build_rules
from plugin.neko_plugin_cli.public.pack import PluginPacker
from plugin.server.infrastructure.config_editor_schema import load_config_editor_schema
from plugin.server.infrastructure.packaged_metadata import read_packaged_metadata

_PLUGINS = Path(__file__).resolve().parents[3] / "plugins"
_MIGRATED = tuple(sorted(path.parent.name for path in _PLUGINS.glob("*/plugin.toml")))
_LOCALES = {"zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}

pytestmark = pytest.mark.plugin_unit


def _schema(plugin_id: str) -> dict:
    schema, warnings = load_config_editor_schema(_PLUGINS / plugin_id / "plugin.toml")
    assert not warnings
    assert schema is not None
    return schema


def _check_fields(schema: dict, defaults: dict) -> None:
    assert schema["type"] == "object"
    assert set(schema["properties"]) == set(defaults)
    for key, value in defaults.items():
        field = schema["properties"][key]
        if isinstance(value, dict):
            _check_fields(field, value)
            continue
        assert field["default"] == value
        expected = "boolean" if isinstance(value, bool) else (
            "number" if isinstance(value, (int, float)) else "string"
        )
        assert field["type"] in ({"integer", "number"} if expected == "number" else {expected})
        if field["type"] == "integer":
            assert isinstance(value, int) and not isinstance(value, bool)
        if "minimum" in field:
            assert value >= field["minimum"]
        if "maximum" in field:
            assert value <= field["maximum"]
        if "enum" in field:
            assert value in field["enum"]


@pytest.mark.parametrize("plugin_id", _MIGRATED)
def test_schema_covers_every_editable_manifest_and_example_field(plugin_id: str) -> None:
    plugin_dir = _PLUGINS / plugin_id
    manifest = tomllib.loads((plugin_dir / "plugin.toml").read_text(encoding="utf-8"))
    manifest.pop("plugin")
    schema = _schema(plugin_id)
    # Claude companion consumes an optional section not shipped in plugin.toml.
    # Keep those existing runtime defaults documented without changing the manifest.
    if plugin_id == "claude_companion":
        assert "claude_companion" not in manifest
        manifest["claude_companion"] = {
            "port": 48920,
            "cooldown_seconds": 60,
            "api_token": "",
        }
    _check_fields(schema, manifest)
    example = plugin_dir / "config.example.toml"
    if example.is_file():
        _check_fields(schema, tomllib.loads(example.read_text(encoding="utf-8")))


@pytest.mark.parametrize("plugin_id", _MIGRATED)
def test_schema_labels_and_descriptions_cover_all_locales(plugin_id: str) -> None:
    def walk(node: dict) -> None:
        for child in node.get("properties", {}).values():
            for key in ("title", "description"):
                translations = child[f"x-{key}-i18n"]
                assert set(translations) == _LOCALES
                assert all(isinstance(value, str) and value.strip() for value in translations.values())
                assert child[key] == translations["en"]
                # Plugin-authored strings are plain text, not Vue interpolation.
                assert all(not re.search(r"\{\w+\}", value) for value in translations.values())
            walk(child)
    walk(_schema(plugin_id))


@pytest.mark.parametrize("plugin_id", _MIGRATED)
def test_schema_is_included_in_plugin_packages(plugin_id: str, tmp_path: Path) -> None:
    plugin_dir = _PLUGINS / plugin_id
    project_file = plugin_dir / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8")) if project_file.exists() else None
    rules = load_build_rules(project)
    copied = PluginPacker().copy_plugin_runtime_files(plugin_dir, tmp_path, rules=rules)
    sidecar = tmp_path / "config.schema.json"
    assert sidecar.resolve() in copied
    assert sidecar.read_bytes() == (plugin_dir / "config.schema.json").read_bytes()
    assert load_config_editor_schema(tmp_path / "plugin.toml")[0] == _schema(plugin_id)


@pytest.mark.parametrize("plugin_id", _MIGRATED)
def test_packaged_metadata_stays_valid_after_schema_migration(plugin_id: str) -> None:
    # Unlike the general tracked-file guard, validate new schema files before
    # they are staged too: sidecars participate in the source fingerprint.
    assert read_packaged_metadata(_PLUGINS / plugin_id) is not None


def test_schema_keeps_zero_sentinels_and_runtime_limits() -> None:
    game = _schema("game_agent_minecraft")["properties"]["game_agent"]["properties"]
    for key in ("screenshot_max_bytes", "screenshot_max_edge_px"):
        assert game[key]["minimum"] == 0
    assert game["task_timeout_seconds"]["maximum"] == 295
    assert game["screenshot_jpeg_quality"]["maximum"] == 95
    search = _schema("web_search")["properties"]["search"]["properties"]
    assert search["retry_attempts"]["minimum"] == 1
    assert search["retry_attempts"]["maximum"] == 3
    assert search["queue_wait_seconds"]["minimum"] == 0.1
    assert search["backend"]["enum"] == ["auto", "anysearch", "baidu", "duckduckgo"]
    life = _schema("lifekit")["properties"]["lifekit"]["properties"]
    assert "" in life["locale"]["enum"]
    assert life["forecast_days"]["minimum"] == 1
    assert life["forecast_days"]["maximum"] == 7


def test_remaining_schemas_preserve_open_maps_and_zero_sentinels() -> None:
    mcp = _schema("mcp_adapter")["properties"]
    servers = mcp["mcp_servers"]
    assert servers["properties"] == {}
    assert servers.get("additionalProperties", True) is True
    connection = mcp["mcp_adapter"]["properties"]
    assert connection["reconnect_interval"]["minimum"] == 0
    assert connection["max_reconnect_attempts"]["minimum"] == 0
    # Runtime accepts fractional timeouts and maps non-positive values to defaults.
    for key in ("connect_timeout", "tool_timeout"):
        assert connection[key]["type"] == "number"
        assert "minimum" not in connection[key]
    memo = _schema("memo_reminder")["properties"]["memo"]["properties"]
    assert memo["max_reminders"]["minimum"] == 0
    assert "enum" not in memo["timezone"]  # Any installed IANA zone is supported.
    netease = _schema("netease_music")["properties"]["plugin_runtime"]["properties"]
    assert netease["enabled"]["default"] is False
    claude = _schema("claude_companion")["properties"]["claude_companion"]["properties"]
    assert claude["api_token"]["default"] == ""
    assert claude["api_token"]["writeOnly"] is True
    assert claude["cooldown_seconds"]["minimum"] == 0
    assert (claude["port"]["minimum"], claude["port"]["maximum"]) == (1, 65535)


def test_remaining_schemas_match_runtime_mode_choices() -> None:
    from plugin.plugins.proactive_controller import _VALID_MODES
    from plugin.sdk.shared.storage.state import PluginStatePersistence

    proactive = _schema("proactive_controller")["properties"]["proactive_controller"]["properties"]
    assert proactive["default_mode_on_first_run"]["enum"] == list(_VALID_MODES)
    mcp = _schema("mcp_adapter")["properties"]
    assert "adapter" not in mcp
    assert set(mcp["plugin_state"]["properties"]["backend"]["enum"]) == PluginStatePersistence.VALID_BACKENDS
    assert set(mcp["plugin_state"]["properties"]["persist_mode"]["enum"]) == {"auto", "manual", "off"}
