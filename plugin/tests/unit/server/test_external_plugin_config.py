from pathlib import Path
import tomllib

from plugin.server.infrastructure import config_paths
from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path
from plugin.server.infrastructure.config_updates import update_plugin_config


def test_resolve_plugin_config_initializes_external_config_from_example(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n",
        encoding="utf-8",
    )
    example_text = "[plugin_runtime]\nenabled = false\n\n[demo]\nmessage = 'hello'\n"
    (installed_dir / "config.example.toml").write_text(example_text, encoding="utf-8")

    resolved = resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert external_config.read_text(encoding="utf-8") == example_text
    assert resolved["config_path"] == str(external_config)
    assert resolved["base_config"] == {
        "plugin_runtime": {"enabled": False},
        "demo": {"message": "hello"},
    }
    assert resolved["effective_config"] == {
        "plugin_runtime": {"enabled": False},
        "demo": {"message": "hello"},
        "plugin": {
            "id": "demo",
            "version": "2.0.0",
            "entry": "plugins.demo:Demo",
        },
    }


def test_update_plugin_config_writes_only_external_runtime_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    plugin_root = tmp_path / "plugins"
    installed_dir = plugin_root / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_text = (
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:Demo'\n"
        "\n[plugin_runtime]\nenabled = true\n\n[demo]\nmessage = 'original'\n"
    )
    manifest_path.write_text(manifest_text, encoding="utf-8")
    monkeypatch.setattr(config_paths, "PLUGIN_CONFIG_ROOTS", (plugin_root,))

    resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )
    result = update_plugin_config("demo", {"demo": {"message": "changed"}})

    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert manifest_path.read_text(encoding="utf-8") == manifest_text
    with external_config.open("rb") as stream:
        external_data = tomllib.load(stream)
    assert external_data["demo"]["message"] == "changed"
    assert result["config"]["plugin"]["version"] == "2.0.0"


def test_legacy_manifest_is_copied_without_rewriting(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    legacy_text = (
        "# Keep this user-facing comment.\n"
        "[plugin]\nid = 'demo'\nversion = '1.0.0'\nentry = 'plugins.demo:Demo'\n"
        "\n[demo]\nmessage = 'legacy value'\n"
    )
    manifest_path.write_text(legacy_text, encoding="utf-8")

    resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert external_config.read_text(encoding="utf-8") == legacy_text


def test_existing_runtime_config_is_preserved_and_manifest_identity_wins(
    monkeypatch,
    tmp_path: Path,
) -> None:
    storage_root = tmp_path / "runtime-storage"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    installed_dir = tmp_path / "plugins" / "demo"
    installed_dir.mkdir(parents=True)
    manifest_path = installed_dir / "plugin.toml"
    manifest_path.write_text(
        "[plugin]\nid = 'demo'\nversion = '2.0.0'\nentry = 'plugins.demo:NewDemo'\n",
        encoding="utf-8",
    )
    external_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    external_config.parent.mkdir(parents=True)
    runtime_text = (
        "# Existing user config must remain byte-for-byte unchanged.\n"
        "[plugin]\nid = 'demo'\nversion = '1.0.0'\nentry = 'plugins.demo:OldDemo'\n"
        "\n[demo]\nmessage = 'user value'\n"
    )
    external_config.write_text(runtime_text, encoding="utf-8")

    resolved = resolve_plugin_config_from_path(
        "demo",
        config_path=manifest_path,
        include_effective_config=True,
        validate_schema=False,
    )

    assert external_config.read_text(encoding="utf-8") == runtime_text
    assert resolved["effective_config"]["demo"] == {"message": "user value"}
    assert resolved["effective_config"]["plugin"] == {
        "id": "demo",
        "version": "2.0.0",
        "entry": "plugins.demo:NewDemo",
    }
