from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugin.server.infrastructure.config_editor_schema import load_config_editor_schema
from plugin.server.infrastructure import config_paths, config_queries

pytestmark = pytest.mark.plugin_unit


@pytest.fixture
def manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "plugins"
    installed = root / "schema_demo"
    installed.mkdir(parents=True)
    path = installed / "plugin.toml"
    path.write_text(
        '[plugin]\nid="schema_demo"\nname="Demo"\nentry="demo:Plugin"\n'
        '[search]\nmax_results=8\n', encoding="utf-8",
    )
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (root,))
    return path


def test_missing_schema_preserves_configuration(manifest: Path) -> None:
    assert load_config_editor_schema(manifest) == (None, [])
    payload = config_queries.load_plugin_effective_base_config("schema_demo")
    assert payload["config_schema"] is None
    assert payload["config"]["search"] == {"max_results": 8}


@pytest.mark.parametrize("query", [
    config_queries.load_plugin_base_config,
    config_queries.load_plugin_effective_base_config,
    config_queries.load_plugin_config,
])
def test_queries_read_schema_from_installed_payload(manifest: Path, query) -> None:
    schema = {
        "type": "object",
        "properties": {"search": {"type": "object", "properties": {
            "max_results": {"title": "Results", "type": "integer", "minimum": 1,
                            "x-title-i18n": {"zh-CN": "结果数量"}},
        }}},
    }
    manifest.with_name("config.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    runtime_path = config_paths.ensure_plugin_runtime_config("schema_demo")
    # A writable profile/runtime sidecar cannot override installed metadata.
    runtime_path.with_name("config.schema.json").write_text('{"type":"object","title":"wrong"}', encoding="utf-8")
    before = runtime_path.read_bytes()
    payload = query("schema_demo")
    assert payload["config_schema"] == schema
    assert "config_schema" not in payload["config"]
    assert runtime_path.read_bytes() == before


@pytest.mark.parametrize("raw", [
    '{"secret":"do-not-echo",', '[]', '{"type":"array"}',
    '{"type":"object","properties":[]}',
    '{"type":"object","properties":{"field":{"title":42}}}',
    '{"type":"object","properties":{"field":{"items":[]}}}',
    '{"type":"object","minimum":false}',
    '{"type":"object","minimum":10,"maximum":1}',
    '{"type":"object","maxLength":-1}',
    '{"type":"object","readOnly":"true"}',
    '{"type":"object","x-title-i18n":{"en":42}}',
    '{"type":"object","enum":{}}',
    '{"type":"object","default":NaN}',
    '{"type":"object","default":1e999}',
    '{"type":"object","properties":{"field":{"type":{}}}}',
    pytest.param(' ' * (256 * 1024 + 1), id='oversized'),
])
def test_invalid_schema_falls_back_without_exposing_content(manifest: Path, raw: str) -> None:
    manifest.with_name("config.schema.json").write_text(raw, encoding="utf-8")
    payload = config_queries.load_plugin_effective_base_config("schema_demo")
    assert payload["config_schema"] is None
    assert payload["config"]["search"]["max_results"] == 8
    warning = next(w for w in payload["warnings"] if w["code"] == "PLUGIN_CONFIG_EDITOR_SCHEMA_INVALID")
    assert "do-not-echo" not in str(warning)
    assert str(manifest.parent) not in str(warning)


def test_nested_schema_depth_is_bounded(manifest: Path) -> None:
    schema = {"type": "object"}
    node = schema
    for _ in range(34):
        child = {"type": "object"}
        node["properties"] = {"child": child}
        node = child
    manifest.with_name("config.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    schema, warnings = load_config_editor_schema(manifest)
    assert schema is None
    assert warnings


def test_schema_cannot_resolve_outside_plugin(manifest: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sidecar = manifest.with_name("config.schema.json")
    sidecar.write_text('{"type":"object"}', encoding="utf-8")
    real_resolve = Path.resolve

    def resolve(path: Path, *args, **kwargs):
        if path == sidecar:
            return manifest.parent.parent / "outside.json"
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    schema, warnings = load_config_editor_schema(manifest)
    assert schema is None
    assert warnings


@pytest.mark.parametrize("field", [
    {"type": ["string", "number"]},
    {"type": ["string", "null"]},
    {"type": ["string"]},
    {"type": "null"},
    {"enum": ["valid", None]},
    {"enum": ["valid", {}]},
    {"enum": ["valid", []]},
    {"enum": []},
])
def test_unsupported_controls_warn_and_preserve_config(manifest: Path, field: dict) -> None:
    schema = {"type": "object", "properties": {"search": {
        "type": "object", "properties": {"max_results": field},
    }}}
    manifest.with_name("config.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    payload = config_queries.load_plugin_effective_base_config("schema_demo")
    assert payload["config_schema"] is None
    assert payload["config"]["search"]["max_results"] == 8
    assert any(w["code"] == "PLUGIN_CONFIG_EDITOR_SCHEMA_INVALID" for w in payload["warnings"])


def test_supported_scalar_enum_values_are_preserved(manifest: Path) -> None:
    schema = {"type": "object", "properties": {"choice": {
        "enum": ["", "value", 0, 1.5, False, True],
    }}}
    manifest.with_name("config.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    assert load_config_editor_schema(manifest) == (schema, [])
