from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.infrastructure import config_resolver as module


@pytest.mark.plugin_unit
def test_resolve_plugin_config_returns_base_effective_profiles_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("/tmp/demo/plugin.toml")
    base_config = {"plugin": {"id": "demo", "name": "", "entry": "demo:Plugin"}}

    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "load_toml_from_file", lambda path: base_config)
    monkeypatch.setattr(
        module,
        "apply_user_config_profiles",
        lambda *, plugin_id, base_config, config_path: {**base_config, "runtime": {"enabled": True}},
    )
    monkeypatch.setattr(
        module,
        "get_profiles_state",
        lambda *, plugin_id, config_path: {"config_profiles": {"active": "dev", "files": {}}},
    )
    monkeypatch.setattr(
        module,
        "collect_plugin_toml_semantic_warnings",
        lambda conf, *, toml_path: [
            {
                "code": "PLUGIN_NAME_EMPTY",
                "field": "plugin.name",
                "message": "[plugin].name should be a non-empty string",
                "severity": "warning",
                "source": "semantic",
            }
        ],
    )
    monkeypatch.setattr(
        module,
        "_validate_config_schema",
        lambda config_data, plugin_id: [{"field": "plugin.name", "msg": "字段必填"}],
    )

    class _Stat:
        st_mtime = 0

    monkeypatch.setattr(Path, "stat", lambda self: _Stat())

    payload = module.resolve_plugin_config("demo")

    assert payload["base_config"] == base_config
    assert payload["effective_config"] == {
        "plugin": {"id": "demo", "name": "", "entry": "demo:Plugin"},
        "runtime": {"enabled": True},
    }
    assert payload["profiles_state"] == {"config_profiles": {"active": "dev", "files": {}}}
    assert payload["warnings"] == [
        {
            "code": "PLUGIN_SCHEMA_VALIDATION",
            "field": "plugin.name",
            "message": "字段必填",
            "severity": "warning",
            "source": "schema",
        },
        {
            "code": "PLUGIN_NAME_EMPTY",
            "field": "plugin.name",
            "message": "[plugin].name should be a non-empty string",
            "severity": "warning",
            "source": "semantic",
        },
    ]


@pytest.mark.plugin_unit
def test_resolve_plugin_config_can_skip_effective_merge_and_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("/tmp/demo/plugin.toml")
    base_config = {"plugin": {"id": "demo", "name": "Demo", "entry": "demo:Plugin"}}

    monkeypatch.setattr(module, "get_plugin_config_path", lambda plugin_id: config_path)
    monkeypatch.setattr(module, "load_toml_from_file", lambda path: base_config)
    monkeypatch.setattr(
        module,
        "get_profiles_state",
        lambda *, plugin_id, config_path: {"config_profiles": None},
    )
    monkeypatch.setattr(
        module,
        "collect_plugin_toml_semantic_warnings",
        lambda conf, *, toml_path: [],
    )

    called = {"apply": 0, "validate": 0}

    def _apply(**kwargs):
        called["apply"] += 1
        return {"bad": True}

    def _validate(config_data, plugin_id):
        called["validate"] += 1
        return [{"field": "x", "msg": "bad"}]

    monkeypatch.setattr(module, "apply_user_config_profiles", _apply)
    monkeypatch.setattr(module, "_validate_config_schema", _validate)

    class _Stat:
        st_mtime = 0

    monkeypatch.setattr(Path, "stat", lambda self: _Stat())

    payload = module.resolve_plugin_config(
        "demo",
        include_effective_config=False,
        validate_schema=False,
    )

    assert payload["effective_config"] == base_config
    assert payload["warnings"] == []
    assert called == {"apply": 0, "validate": 0}
