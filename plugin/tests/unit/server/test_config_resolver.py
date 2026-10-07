from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from plugin.server.infrastructure import config_resolver as module
from plugin.server.infrastructure.config_fingerprint import fingerprint_config


def _assert_warning_shape(items: object) -> None:
    assert isinstance(items, list)
    for item in items:
        assert isinstance(item, dict)
        assert set(item.keys()) == {"code", "field", "message", "severity", "source"}
        assert isinstance(item["code"], str) and item["code"]
        assert item["field"] is None or isinstance(item["field"], str)
        assert isinstance(item["message"], str) and item["message"]
        assert item["severity"] == "warning"
        assert item["source"] in {"schema", "semantic"}


@pytest.mark.plugin_unit
def test_resolve_plugin_config_returns_base_effective_profiles_and_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("/tmp/demo/plugin.toml")
    monkeypatch.setattr(module, "plugin_config_file_lock", lambda path: nullcontext())
    base_config = {"plugin": {"id": "demo", "name": "", "entry": "demo:Plugin"}}

    monkeypatch.setattr(module, "get_plugin_manifest_path", lambda plugin_id: config_path)
    monkeypatch.setattr(
        module,
        "ensure_plugin_runtime_config",
        lambda plugin_id, *, manifest_path: config_path,
    )
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
    assert payload["config_fingerprint"] == fingerprint_config(payload["effective_config"])
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
    _assert_warning_shape(payload["warnings"])
    assert payload["schema_validation_errors"] == [{"field": "plugin.name", "msg": "字段必填"}]


@pytest.mark.plugin_unit
def test_resolve_plugin_config_can_skip_effective_merge_and_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("/tmp/demo/plugin.toml")
    monkeypatch.setattr(module, "plugin_config_file_lock", lambda path: nullcontext())
    base_config = {"plugin": {"id": "demo", "name": "Demo", "entry": "demo:Plugin"}}

    monkeypatch.setattr(module, "get_plugin_manifest_path", lambda plugin_id: config_path)
    monkeypatch.setattr(
        module,
        "ensure_plugin_runtime_config",
        lambda plugin_id, *, manifest_path: config_path,
    )
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


@pytest.mark.plugin_unit
def test_resolve_plugin_config_from_path_reuses_preloaded_manifest_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("/tmp/demo/plugin.toml")
    monkeypatch.setattr(module, "plugin_config_file_lock", lambda path: nullcontext())
    runtime_path = Path("/tmp/runtime/demo/plugin.toml")
    manifest_config = {"plugin": {"id": "demo", "name": "Demo", "entry": "demo:Plugin"}}
    runtime_config = {"runtime": {"enabled": False}}
    captured: list[Path] = []

    def _ensure_plugin_runtime_config(plugin_id: str, *, manifest_path: Path) -> Path:
        assert plugin_id == "demo"
        assert manifest_path == config_path.resolve(strict=False)
        return runtime_path

    monkeypatch.setattr(
        module,
        "ensure_plugin_runtime_config",
        _ensure_plugin_runtime_config,
    )

    monkeypatch.setattr(
        module,
        "apply_user_config_profiles",
        lambda *, plugin_id, base_config, config_path: {"plugin": {"id": plugin_id}, "runtime": {"enabled": True}},
    )
    monkeypatch.setattr(
        module,
        "get_profiles_state",
        lambda *, plugin_id, config_path: {"config_profiles": {"active": None, "files": {}}},
    )
    monkeypatch.setattr(
        module,
        "collect_plugin_toml_semantic_warnings",
        lambda conf, *, toml_path: [],
    )
    monkeypatch.setattr(
        module,
        "_validate_config_schema",
        lambda config_data, plugin_id: [],
    )

    def _load_toml(path: Path) -> dict[str, object]:
        captured.append(path)
        return runtime_config

    monkeypatch.setattr(module, "load_toml_from_file", _load_toml)

    class _Stat:
        st_mtime = 0

    monkeypatch.setattr(Path, "stat", lambda self: _Stat())

    payload = module.resolve_plugin_config_from_path(
        "demo",
        config_path=config_path,
        base_config=manifest_config,
    )

    assert captured == [runtime_path]
    assert payload["base_config"] == runtime_config
    assert payload["effective_config"] == {
        "plugin": {"id": "demo", "name": "Demo", "entry": "demo:Plugin"},
        "runtime": {"enabled": True},
    }
    _assert_warning_shape(payload["warnings"])


@pytest.mark.plugin_unit
def test_resolve_plugin_config_warnings_keep_schema_before_semantic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = Path("/tmp/demo/plugin.toml")
    monkeypatch.setattr(module, "plugin_config_file_lock", lambda path: nullcontext())
    base_config = {"plugin": {"id": "demo", "name": "", "entry": "demo:Plugin"}}

    monkeypatch.setattr(module, "get_plugin_manifest_path", lambda plugin_id: config_path)
    monkeypatch.setattr(
        module,
        "ensure_plugin_runtime_config",
        lambda plugin_id, *, manifest_path: config_path,
    )
    monkeypatch.setattr(module, "load_toml_from_file", lambda path: base_config)
    monkeypatch.setattr(
        module,
        "apply_user_config_profiles",
        lambda *, plugin_id, base_config, config_path: base_config,
    )
    monkeypatch.setattr(
        module,
        "get_profiles_state",
        lambda *, plugin_id, config_path: {"config_profiles": None},
    )
    monkeypatch.setattr(
        module,
        "_validate_config_schema",
        lambda config_data, plugin_id: [{"field": "plugin.name", "msg": "schema-first"}],
    )
    monkeypatch.setattr(
        module,
        "collect_plugin_toml_semantic_warnings",
        lambda conf, *, toml_path: [
            {
                "code": "PLUGIN_NAME_EMPTY",
                "field": "plugin.name",
                "message": "semantic-second",
                "severity": "warning",
                "source": "semantic",
            }
        ],
    )

    class _Stat:
        st_mtime = 0

    monkeypatch.setattr(Path, "stat", lambda self: _Stat())

    payload = module.resolve_plugin_config("demo")

    assert [item["source"] for item in payload["warnings"]] == ["schema", "semantic"]
    assert [item["message"] for item in payload["warnings"]] == ["schema-first", "semantic-second"]


@pytest.mark.plugin_unit
@pytest.mark.parametrize("seed", ["manifest", "example", "existing"])
def test_discovery_config_matches_initialized_config_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, seed: str
) -> None:
    from plugin.core.plugin_layout import resolve_plugin_layout

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    installed = tmp_path / "demo"
    installed.mkdir()
    manifest = installed / "plugin.toml"
    manifest.write_text(
        "[plugin]\nid='demo'\nname='Demo'\nentry='demo:Plugin'\n"
        "[plugin.config_profiles]\nactive='dev'\n"
        "[plugin.config_profiles.files]\ndev='profiles/dev.toml'\n"
        "[runtime]\nlevel=1\nregion='manifest'\n", encoding="utf-8"
    )
    (installed / "profiles").mkdir()
    (installed / "profiles/dev.toml").write_text("[runtime]\nlevel=9\n", encoding="utf-8")
    layout = resolve_plugin_layout("demo", installed)
    if seed == "example":
        (installed / "config.example.toml").write_text(
            "[runtime]\nregion='example'\n", encoding="utf-8"
        )
    elif seed == "existing":
        layout.config_path.parent.mkdir(parents=True)
        layout.config_path.write_text("[runtime]\nregion='existing'\n", encoding="utf-8")

    discovered = module.read_plugin_config_from_path(
        "demo", config_path=manifest
    )
    assert layout.config_path.exists() == (seed == "existing")
    assert discovered["config_path"] == str(layout.config_path)
    assert discovered["effective_config"]["runtime"] == {"level": 9, "region": seed}

    initialized = module.resolve_plugin_config_from_path("demo", config_path=manifest)
    assert layout.config_path.is_file()
    for key in ("base_config", "effective_config", "profiles_state", "warnings", "schema_validation_errors"):
        assert discovered[key] == initialized[key]

    # Discovery always rereads user edits; defaults are not a persistent cache.
    layout.config_path.write_text("[runtime]\nregion='edited'\n", encoding="utf-8")
    edited = module.read_plugin_config_from_path(
        "demo", config_path=manifest
    )
    assert edited["effective_config"]["runtime"]["region"] == "edited"


@pytest.mark.plugin_unit
def test_discovery_config_rejects_non_file_runtime_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException
    from plugin.core.plugin_layout import resolve_plugin_layout

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    installed = tmp_path / "demo"
    installed.mkdir()
    manifest = installed / "plugin.toml"
    manifest.write_text("[plugin]\nid='demo'\n", encoding="utf-8")
    layout = resolve_plugin_layout("demo", installed)
    layout.config_path.mkdir(parents=True)
    with pytest.raises(HTTPException, match="runtime config path is not a file"):
        module.read_plugin_config_from_path(
            "demo", config_path=manifest
        )


@pytest.mark.plugin_unit
@pytest.mark.parametrize("existing_runtime", [False, True])
def test_discovery_snapshot_finishes_before_profile_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_runtime: bool,
) -> None:
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager

    from plugin.core.plugin_layout import resolve_plugin_layout
    from plugin.server.infrastructure import config_profiles_write

    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    installed = tmp_path / "demo"
    installed.mkdir()
    manifest = installed / "plugin.toml"
    manifest.write_text(
        "[plugin]\nid='demo'\nname='Demo'\nentry='demo:Plugin'\n"
        "[runtime]\nlevel=1\n", encoding="utf-8",
    )
    layout = resolve_plugin_layout("demo", installed)
    if existing_runtime:
        module.resolve_plugin_config_from_path("demo", config_path=manifest)
    monkeypatch.setattr(config_profiles_write, "get_plugin_config_path", lambda _: manifest)

    reader_paused = threading.Event()
    release_reader = threading.Event()
    writer_attempted = threading.Event()
    writer_finished = threading.Event()
    apply_profiles = module.apply_user_config_profiles
    get_write_lock = config_profiles_write._get_plugin_lock

    def paused_profiles(**kwargs):
        reader_paused.set()
        assert release_reader.wait(10)
        return apply_profiles(**kwargs)

    @contextmanager
    def writer_lock(plugin_id):
        writer_attempted.set()
        with get_write_lock(plugin_id):
            yield

    def write_profile():
        try:
            return config_profiles_write.upsert_profile_config(
                plugin_id="demo", profile_name="dev",
                config={"runtime": {"level": 2}}, make_active=True,
            )
        finally:
            writer_finished.set()

    monkeypatch.setattr(module, "apply_user_config_profiles", paused_profiles)
    monkeypatch.setattr(config_profiles_write, "_get_plugin_lock", writer_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(module.read_plugin_config_from_path, "demo", config_path=manifest)
        try:
            assert reader_paused.wait(10)
            writer = pool.submit(write_profile)
            assert writer_attempted.wait(10)
            assert not writer_finished.wait(0.1), "profile write overtook the discovery snapshot"
            assert layout.config_path.exists() == existing_runtime
        finally:
            release_reader.set()
        snapshot = reader.result(timeout=10)
        writer.result(timeout=10)

    assert snapshot["effective_config"]["runtime"]["level"] == 1
    assert snapshot["config_fingerprint"] == fingerprint_config(snapshot["effective_config"])
    updated = module.read_plugin_config_from_path("demo", config_path=manifest)
    assert updated["effective_config"]["runtime"]["level"] == 2


@pytest.mark.plugin_unit
def test_readonly_resolver_uses_discovery_path_cache(tmp_path, monkeypatch):
    from plugin.utils.path_resolution import PathResolutionCache
    from plugin.core import plugin_layout

    storage = tmp_path / "data"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage))
    real_resolve_root = plugin_layout.resolve_runtime_data_root
    roots = []
    def resolve_root():
        resolved = real_resolve_root()
        roots.append(resolved)
        return resolved
    monkeypatch.setattr(plugin_layout, "resolve_runtime_data_root", resolve_root)
    cache = PathResolutionCache()
    configs = []
    layouts = []
    for plugin_id in ("first", "second"):
        config = tmp_path / plugin_id / "plugin.toml"
        config.parent.mkdir()
        config.write_text(f'[plugin]\nid="{plugin_id}"\n', encoding="utf-8")
        cache.resolve(config)
        layouts.append(plugin_layout.resolve_plugin_layout(plugin_id, config.parent, read_cache=cache))
        configs.append(config)
    assert roots == [storage]
    assert all(not layout.config_path.exists() for layout in layouts)
    protected = {storage, *(config.parent for config in configs), *configs}
    original = Path.resolve
    def avoid_repeated_resolve(path, *args, **kwargs):
        if path in protected:
            pytest.fail(f"already cached path resolved again: {path}")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", avoid_repeated_resolve)
    for config, layout in zip(configs, layouts):
        result = module.read_plugin_config_from_path(
            layout.plugin_id, config_path=config, read_cache=cache
        )
        assert result["manifest_path"] == str(layout.manifest_path)
        assert result["config_path"] == str(layout.config_path)
        assert not layout.config_path.exists()
    assert roots == [storage]


@pytest.mark.plugin_unit
@pytest.mark.parametrize("operation", ["materialize", "ensure", "lookup"])
def test_runtime_paths_use_current_storage_after_migration(tmp_path, monkeypatch, operation):
    from plugin.core.plugin_layout import resolve_plugin_layout
    from plugin.server.infrastructure import config_paths
    from plugin.utils.path_resolution import PathResolutionCache

    manifest = tmp_path / "installed" / "plugin.toml"
    manifest.parent.mkdir()
    manifest.write_text('[plugin]\nid="demo"\n', encoding="utf-8")
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(old_root))
    cache = PathResolutionCache()
    cache.resolve(manifest)
    old_layout = resolve_plugin_layout("demo", manifest.parent, read_cache=cache)
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(new_root))
    new_layout = resolve_plugin_layout("demo", manifest.parent)
    assert config_paths.get_plugin_runtime_config_path(
        "demo", manifest_path=manifest
    ) == new_layout.config_path
    if operation == "materialize":
        result = module.resolve_plugin_config_from_path(
            "demo", config_path=manifest
        )
        assert result["config_path"] == str(new_layout.config_path)
    elif operation == "ensure":
        assert config_paths.ensure_plugin_runtime_config(
            "demo", manifest_path=manifest
        ) == new_layout.config_path
    else:
        monkeypatch.setattr(config_paths, "get_plugin_manifest_path", lambda _: manifest)
        assert config_paths.get_plugin_runtime_config_path(
            "demo"
        ) == new_layout.config_path
        assert not new_layout.config_path.exists()
    assert not old_layout.config_path.exists()
    if operation != "lookup":
        assert new_layout.config_path.read_bytes() == manifest.read_bytes()
