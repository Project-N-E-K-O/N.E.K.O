from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core import host as host_module
from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.server.application.plugins import upgrade_support
from plugin.server.application.plugins.upgrade_support import (
    ReplacePluginError,
    plugin_is_running,
    replace_plugin,
    remove_directory,
    run_rollback,
)
from plugin.server.infrastructure.config_profiles import load_profiles_cfg_from_file

pytestmark = pytest.mark.plugin_unit


async def _async_none() -> None:
    return None


async def _async_false() -> bool:
    return False


async def _async_true() -> bool:
    return True


async def _record(events: list[str], value: str) -> None:
    events.append(value)


def test_legacy_profile_case_variants_cannot_share_one_canonical_target() -> None:
    with pytest.raises(OSError, match="multiple legacy profile paths map to profiles.toml"):
        upgrade_support._canonical_profile_sources(
            [Path("profiles.toml"), Path("Profiles.toml")]
        )


@pytest.mark.asyncio
async def test_replace_plugin_replaces_only_payload_and_preserves_external_user_state(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    (target / "vendor").mkdir()
    (target / "vendor" / "dependency.txt").write_text("old", encoding="utf-8")

    storage_root = tmp_path / "state"
    state_root = storage_root / "plugins" / "demo"
    expected_state = {
        state_root / "config" / "plugin.toml": "user_config = true\n",
        state_root / "data" / "database.txt": "user data\n",
        state_root / "cache" / "cached.txt": "cache data\n",
    }
    for path, content in expected_state.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        (target / "vendor").mkdir()
        (target / "vendor" / "dependency.txt").write_text("new", encoding="utf-8")
        return {"installed": True}

    result = await replace_plugin(
        layout=resolve_plugin_layout("demo", target, storage_root=storage_root),
        install_new=install_new,
        validate_new=_async_none,
        is_running=lambda _plugin_id: _async_false(),
        stop=lambda _plugin_id: _async_none(),
        start=lambda _plugin_id: _async_none(),
        cleanup_backup=remove_directory,
    )

    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 2\n"
    assert (target / "vendor" / "dependency.txt").read_text(encoding="utf-8") == "new"
    for path, content in expected_state.items():
        assert path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio
async def test_replace_plugin_invalidates_module_cache_before_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    events: list[str] = []

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        return {"installed": True}

    def evict(plugin_id: str) -> None:
        events.append(f"evict:{plugin_id}")

    monkeypatch.setattr(host_module, "evict_cached_plugin_modules", evict)

    await replace_plugin(
        layout=resolve_plugin_layout("demo", target),
        install_new=install_new,
        validate_new=_async_none,
        is_running=lambda _plugin_id: _async_true(),
        stop=lambda plugin_id: _record(events, f"stop:{plugin_id}"),
        start=lambda plugin_id: _record(events, f"start:{plugin_id}"),
        cleanup_backup=remove_directory,
    )

    assert events == ["stop:demo", "evict:demo", "start:demo"]


@pytest.mark.asyncio
async def test_replace_plugin_invalidates_new_cache_before_rollback_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    events: list[str] = []
    start_attempts = 0

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        return {"installed": True}

    async def start(plugin_id: str) -> None:
        nonlocal start_attempts
        start_attempts += 1
        events.append(f"start:{plugin_id}")
        if start_attempts == 1:
            raise RuntimeError("replacement failed to start")

    def evict(plugin_id: str) -> None:
        events.append(f"evict:{plugin_id}")

    monkeypatch.setattr(host_module, "evict_cached_plugin_modules", evict)

    with pytest.raises(ReplacePluginError, match="restart"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=install_new,
            validate_new=_async_none,
            is_running=lambda _plugin_id: _async_true(),
            stop=lambda plugin_id: _record(events, f"stop:{plugin_id}"),
            start=start,
            cleanup_backup=remove_directory,
        )

    assert events == [
        "stop:demo",
        "evict:demo",
        "start:demo",
        "evict:demo",
        "start:demo",
    ]
    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 1\n"


@pytest.mark.asyncio
async def test_replace_plugin_preserves_manifest_adjacent_user_profiles(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    (target / "profiles.toml").write_text(
        "[config_profiles]\nactive = 'dev'\n",
        encoding="utf-8",
    )
    (target / "profiles").mkdir()
    (target / "profiles" / "dev.toml").write_text(
        "[feature]\nenabled = true\n",
        encoding="utf-8",
    )

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        return {"installed": True}

    await replace_plugin(
        layout=resolve_plugin_layout("demo", target),
        install_new=install_new,
        validate_new=_async_none,
        is_running=lambda _plugin_id: _async_false(),
        stop=lambda _plugin_id: _async_none(),
        start=lambda _plugin_id: _async_none(),
        cleanup_backup=remove_directory,
    )

    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 2\n"
    assert (target / "profiles.toml").read_text(encoding="utf-8") == (
        "[config_profiles]\nactive = 'dev'\n"
    )
    assert (target / "profiles" / "dev.toml").read_text(encoding="utf-8") == (
        "[feature]\nenabled = true\n"
    )


@pytest.mark.asyncio
async def test_replace_plugin_canonicalizes_legacy_profile_path_case_for_reader(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    (target / "Profiles.toml").write_text(
        "[config_profiles]\nactive = 'dev'\n[config_profiles.files]\ndev = 'profiles/dev.toml'\n",
        encoding="utf-8",
    )
    (target / "Profiles").mkdir()
    (target / "Profiles" / "dev.toml").write_text(
        "[feature]\nenabled = true\n",
        encoding="utf-8",
    )

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        return {"installed": True}

    await replace_plugin(
        layout=resolve_plugin_layout("demo", target),
        install_new=install_new,
        validate_new=_async_none,
        is_running=lambda _plugin_id: _async_false(),
        stop=lambda _plugin_id: _async_none(),
        start=lambda _plugin_id: _async_none(),
        cleanup_backup=remove_directory,
    )

    restored_names = {path.name for path in target.iterdir()}
    assert "profiles.toml" in restored_names
    assert "profiles" in restored_names
    assert load_profiles_cfg_from_file("demo", target / "plugin.toml") == {
        "active": "dev",
        "files": {"dev": "profiles/dev.toml"},
    }


@pytest.mark.asyncio
async def test_replace_plugin_rejects_duplicate_casefolded_profile_paths(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    (target / "profiles.toml").write_text("canonical\n", encoding="utf-8")
    (target / "Profiles.toml").write_text("variant\n", encoding="utf-8")
    variants = [
        path
        for path in target.iterdir()
        if path.name.casefold() == "profiles.toml"
    ]
    if len(variants) < 2:
        pytest.skip("filesystem does not support case-distinct profile paths")

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        return {"installed": True}

    with pytest.raises(ReplacePluginError) as exc_info:
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=install_new,
            validate_new=_async_none,
            is_running=lambda _plugin_id: _async_false(),
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
        )

    assert exc_info.value.stage == "preserve"
    assert exc_info.value.rollback_status == "completed"
    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 1\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("relative_path", "link_target_exists"),
    (("profiles.toml", True), ("profiles", True), ("profiles.toml", False)),
)
async def test_replace_plugin_rejects_manifest_adjacent_profile_symlinks(
    tmp_path: Path,
    relative_path: str,
    link_target_exists: bool,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    link_target = tmp_path / f"external-{relative_path.replace('.', '-')}"
    if link_target_exists:
        if relative_path == "profiles":
            link_target.mkdir()
            (link_target / "dev.toml").write_text("external\n", encoding="utf-8")
        else:
            link_target.write_text("external\n", encoding="utf-8")
    profile_path = target / relative_path
    try:
        profile_path.symlink_to(link_target, target_is_directory=relative_path == "profiles")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("version = 2\n", encoding="utf-8")
        return {"installed": True}

    with pytest.raises(ReplacePluginError) as exc_info:
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=install_new,
            validate_new=_async_none,
            is_running=lambda _plugin_id: _async_false(),
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
        )

    assert exc_info.value.stage == "preserve"
    assert exc_info.value.rollback_status == "completed"
    assert (target / "plugin.toml").read_text(encoding="utf-8") == "version = 1\n"
    assert profile_path.is_symlink()


@pytest.mark.asyncio
async def test_replace_plugin_initializes_runtime_config_from_old_payload_before_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "state"
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(storage_root))
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    old_manifest = (
        "[plugin]\n"
        'id = "demo"\n'
        'version = "1.0.0"\n'
        'entry = "plugins.demo:Demo"\n'
        "\n[demo]\n"
        'message = "user value"\n'
    )
    (target / "plugin.toml").write_text(old_manifest, encoding="utf-8")

    async def install_new() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text(
            "[plugin]\n"
            'id = "demo"\n'
            'version = "2.0.0"\n'
            'entry = "plugins.demo:Demo"\n',
            encoding="utf-8",
        )
        return {"installed": True}

    await replace_plugin(
        layout=resolve_plugin_layout("demo", target, storage_root=storage_root),
        install_new=install_new,
        validate_new=_async_none,
        is_running=lambda _plugin_id: _async_false(),
        stop=lambda _plugin_id: _async_none(),
        start=lambda _plugin_id: _async_none(),
        cleanup_backup=remove_directory,
    )

    runtime_config = storage_root / "plugins" / "demo" / "config" / "plugin.toml"
    assert runtime_config.read_text(encoding="utf-8") == old_manifest


@pytest.mark.asyncio
async def test_replace_plugin_rejects_invalid_preserve_target_before_side_effects(
    tmp_path: Path,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    storage_root = tmp_path / "state"
    events: list[str] = []

    async def is_running(plugin_id: str) -> bool:
        events.append(f"running:{plugin_id}")
        return True

    async def stop(plugin_id: str) -> None:
        events.append(f"stop:{plugin_id}")

    with pytest.raises(ValueError, match="preserve targets"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target, storage_root=storage_root),
            install_new=lambda: _async_none(),  # type: ignore[arg-type]
            validate_new=_async_none,
            is_running=is_running,
            stop=stop,
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
            preserve_targets=(tmp_path / "not-a-replacement-target",),
        )

    assert events == []
    assert not storage_root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["duplicate", "nested"])
async def test_replace_plugin_rejects_overlapping_targets_before_side_effects(
    tmp_path: Path,
    target_kind: str,
) -> None:
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    additional_target = target if target_kind == "duplicate" else target / "profiles"
    events: list[str] = []

    async def is_running(plugin_id: str) -> bool:
        events.append(f"running:{plugin_id}")
        return False

    with pytest.raises(ValueError, match="distinct and non-overlapping"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target, storage_root=tmp_path / "state"),
            install_new=lambda: _async_none(),  # type: ignore[arg-type]
            validate_new=_async_none,
            is_running=is_running,
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
            additional_targets=(additional_target,),
        )

    assert events == []
    assert (target / "plugin.toml").is_file()


@pytest.mark.asyncio
async def test_replace_plugin_rejects_persistent_state_target_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "plugins"
    target = state_root / "demo"
    target.mkdir(parents=True)
    state_db = target / "data" / "study.db"
    state_db.parent.mkdir()
    state_db.write_bytes(b"state")
    events: list[str] = []
    monkeypatch.setattr(upgrade_support, "get_plugin_state_root", lambda: state_root)

    async def is_running(plugin_id: str) -> bool:
        events.append(f"running:{plugin_id}")
        return False

    with pytest.raises(ValueError, match="persistent state paths"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target, storage_root=tmp_path),
            install_new=lambda: _async_none(),  # type: ignore[arg-type]
            validate_new=_async_none,
            is_running=is_running,
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
        )

    assert events == []
    assert state_db.read_bytes() == b"state"


@pytest.mark.asyncio
async def test_replace_plugin_uses_layout_state_root_for_custom_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "installed" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    storage_root = tmp_path / "custom-state"
    plugin_state = storage_root / "plugins" / "demo"
    state_db = plugin_state / "data" / "study.db"
    state_db.parent.mkdir(parents=True)
    state_db.write_bytes(b"state")
    events: list[str] = []
    monkeypatch.setattr(
        upgrade_support,
        "get_plugin_state_root",
        lambda: tmp_path / "unrelated-global-state",
    )

    async def is_running(plugin_id: str) -> bool:
        events.append(f"running:{plugin_id}")
        return False

    with pytest.raises(ValueError, match="persistent state paths"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target, storage_root=storage_root),
            install_new=lambda: _async_none(),  # type: ignore[arg-type]
            validate_new=_async_none,
            is_running=is_running,
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
            additional_targets=(plugin_state,),
        )

    assert events == []
    assert state_db.read_bytes() == b"state"
    assert target.is_dir()


@pytest.mark.asyncio
async def test_replace_plugin_rejects_target_containing_persistent_state_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "exec" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    profile_ancestor = tmp_path / "managed"
    state_root = profile_ancestor / "plugins"
    state_db = state_root / "study_companion" / "data" / "study.db"
    state_db.parent.mkdir(parents=True)
    state_db.write_bytes(b"state")
    events: list[str] = []
    monkeypatch.setattr(upgrade_support, "get_plugin_state_root", lambda: state_root)

    async def is_running(plugin_id: str) -> bool:
        events.append(f"running:{plugin_id}")
        return False

    with pytest.raises(ValueError, match="persistent state paths"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=lambda: _async_none(),  # type: ignore[arg-type]
            validate_new=_async_none,
            is_running=is_running,
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
            additional_targets=(profile_ancestor,),
        )

    assert events == []
    assert state_db.read_bytes() == b"state"
    assert target.is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("overlap", ["root", "child", "ancestor"])
async def test_replace_plugin_rejects_builtin_root_overlap_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overlap: str,
) -> None:
    target = tmp_path / "user-plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.toml").write_text("version = 1\n", encoding="utf-8")
    builtin_root = tmp_path / "runtime" / "plugin" / "plugins"
    builtin_plugin = builtin_root / "demo"
    builtin_plugin.mkdir(parents=True)
    builtin_file = builtin_plugin / "plugin.toml"
    builtin_file.write_text("version = builtin\n", encoding="utf-8")
    forbidden_target = {
        "root": builtin_root,
        "child": builtin_plugin,
        "ancestor": builtin_root.parent,
    }[overlap]
    events: list[str] = []
    monkeypatch.setattr(upgrade_support.settings, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin_root)

    async def is_running(plugin_id: str) -> bool:
        events.append(f"running:{plugin_id}")
        return False

    with pytest.raises(ValueError, match="immutable builtin plugin paths"):
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=lambda: _async_none(),  # type: ignore[arg-type]
            validate_new=_async_none,
            is_running=is_running,
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
            additional_targets=(forbidden_target,),
        )

    assert events == []
    assert builtin_file.read_text(encoding="utf-8") == "version = builtin\n"
    assert target.is_dir()


@pytest.mark.asyncio
async def test_run_rollback_removes_new_directory_restores_backup_and_restarts(tmp_path: Path) -> None:
    target = tmp_path / "demo"
    backup = tmp_path / "demo.bak"
    target.mkdir()
    (target / "new.txt").write_text("new", encoding="utf-8")
    backup.mkdir()
    (backup / "old.txt").write_text("old", encoding="utf-8")
    restarted: list[str] = []

    async def start(plugin_id: str) -> None:
        restarted.append(plugin_id)

    restored = await run_rollback(
        plugin_id="demo",
        target_dir=target,
        backup_dir=backup,
        restart=True,
        start=start,
    )

    assert restored is True
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert restarted == ["demo"]


@pytest.mark.asyncio
async def test_backup_failure_restarts_running_plugin_without_installing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text(
        '[plugin]\nid = "demo"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    events: list[str] = []

    async def is_running(plugin_id: str) -> bool:
        return True

    async def stop(plugin_id: str) -> None:
        events.append(f"stop:{plugin_id}")

    async def start(plugin_id: str) -> None:
        events.append(f"start:{plugin_id}")

    async def install_new() -> dict[str, object]:
        events.append("install")
        return {}

    async def validate_new() -> None:
        events.append("validate")

    async def cleanup_backup(path: Path) -> None:
        events.append(f"cleanup:{path.name}")

    def fail_rename(self: Path, destination: Path) -> Path:
        raise PermissionError(destination)

    monkeypatch.setattr(Path, "rename", fail_rename)

    with pytest.raises(ReplacePluginError) as exc_info:
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=install_new,
            validate_new=validate_new,
            is_running=is_running,
            stop=stop,
            start=start,
            cleanup_backup=cleanup_backup,
        )

    assert exc_info.value.stage == "backup"
    assert exc_info.value.rollback_status == "completed"
    assert events == ["stop:demo", "start:demo"]


@pytest.mark.asyncio
async def test_backup_failure_rolls_back_when_rollback_observer_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text("old", encoding="utf-8")
    additional_target = tmp_path / "profile"
    additional_target.mkdir()
    (additional_target / "default.toml").write_text("old profile", encoding="utf-8")
    original_rename = Path.rename

    def fail_second_backup(source: Path, destination: Path) -> Path:
        if source == additional_target:
            raise PermissionError("profile backup denied")
        return original_rename(source, destination)

    monkeypatch.setattr(Path, "rename", fail_second_backup)

    def fail_observer() -> None:
        raise RuntimeError("observer failed")

    with pytest.raises(ReplacePluginError) as exc_info:
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=lambda: _async_none(),  # type: ignore[arg-type]
            validate_new=_async_none,
            is_running=lambda _plugin_id: _async_false(),
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
            additional_targets=(additional_target,),
            on_rollback_start=fail_observer,
        )

    assert exc_info.value.stage == "backup"
    assert isinstance(exc_info.value.cause, PermissionError)
    assert (target / "plugin.toml").read_text(encoding="utf-8") == "old"
    assert (additional_target / "default.toml").read_text(encoding="utf-8") == "old profile"


@pytest.mark.asyncio
async def test_install_failure_rolls_back_when_rollback_observer_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "demo"
    target.mkdir()
    (target / "plugin.toml").write_text("old", encoding="utf-8")

    async def fail_install() -> dict[str, object]:
        target.mkdir()
        (target / "plugin.toml").write_text("new", encoding="utf-8")
        raise RuntimeError("install failed")

    def fail_observer() -> None:
        raise RuntimeError("observer failed")

    with pytest.raises(ReplacePluginError) as exc_info:
        await replace_plugin(
            layout=resolve_plugin_layout("demo", target),
            install_new=fail_install,
            validate_new=_async_none,
            is_running=lambda _plugin_id: _async_false(),
            stop=lambda _plugin_id: _async_none(),
            start=lambda _plugin_id: _async_none(),
            cleanup_backup=remove_directory,
            on_rollback_start=fail_observer,
        )

    assert exc_info.value.stage == "install"
    assert str(exc_info.value.cause) == "install failed"
    assert (target / "plugin.toml").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_plugin_is_running_propagates_registry_probe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import lifecycle_service

    def fail_probe(plugin_id: str) -> bool:
        raise RuntimeError(f"registry unavailable for {plugin_id}")

    monkeypatch.setattr(lifecycle_service, "_plugin_is_running_sync", fail_probe)

    with pytest.raises(RuntimeError, match="registry unavailable"):
        await plugin_is_running("demo")


@pytest.mark.asyncio
async def test_remove_directory_propagates_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from plugin.server.application.plugins import upgrade_support

    target = tmp_path / "demo"
    target.mkdir()
    ignore_values: list[bool] = []

    def fail_unless_errors_are_suppressed(path: Path, ignore_errors: bool = False) -> None:
        assert path == target
        ignore_values.append(ignore_errors)
        if not ignore_errors:
            raise PermissionError("cleanup denied")

    monkeypatch.setattr(upgrade_support.shutil, "rmtree", fail_unless_errors_are_suppressed)

    with pytest.raises(PermissionError, match="cleanup denied"):
        await remove_directory(target)

    assert ignore_values == [False]
