"""Managed installs leave package profile exports independent of runtime state."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from plugin import settings
from plugin.core.plugin_layout import resolve_plugin_layout
from plugin.neko_plugin_cli.public import build_bundle, build_plugin, inspect_package, unpack_package
from plugin.server.application.install_source import (
    InstallSourceManager, PluginDirectoryScanner, get_install_source_manager, set_global_manager,
)
from plugin.server.application.plugin_cli import service as cli
from plugin.server.application.plugins import development
from plugin.server.application.plugins.installation_transactions import replace, uninstall
from plugin.server.infrastructure import autostart_approvals, config_paths, config_resolver
from plugin.server.domain.errors import ServerDomainError

pytestmark = pytest.mark.plugin_unit
PLUGIN_ID = "profile_independence_demo"


def _source(root: Path, plugin_id: str = PLUGIN_ID, version: str = "1.0.0") -> Path:
    source = root / plugin_id
    source.mkdir(parents=True)
    (source / "plugin.toml").write_text(
        f'[plugin]\nid="{plugin_id}"\nname="Profile independence demo"\n'
        f'version="{version}"\ntype="plugin"\n', encoding="utf-8",
    )
    (source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "config.example.toml").write_text('[settings]\nvalue="default"\n', encoding="utf-8")
    (source / "profiles.toml").write_text(
        '[config_profiles]\nactive="custom"\n[config_profiles.files]\ncustom="profiles/custom.toml"\n',
        encoding="utf-8",
    )
    (source / "profiles").mkdir()
    (source / "profiles/custom.toml").write_text('[settings]\nvalue="custom"\n', encoding="utf-8")
    return source


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    user, builtin, packages, profiles, storage = [
        tmp_path / name for name in ("installed", "builtin", "packages", "package-profiles", "runtime")
    ]
    for key, path in (
        ("USER_PLUGIN_CONFIG_ROOT", user), ("BUILTIN_PLUGIN_CONFIG_ROOT", builtin),
        ("USER_PLUGIN_PACKAGES_ROOT", packages), ("USER_PACKAGE_PROFILES_ROOT", profiles),
        ("PLUGIN_STATE_ROOT", storage / "plugins"),
    ):
        monkeypatch.setattr(settings, key, path)
    packages.mkdir()
    monkeypatch.setenv("NEKO_PLUGIN_OPERATION_LOCK_PATH", str(tmp_path / "operation.lock"))
    monkeypatch.setattr(development, "list_registration_records_sync", lambda: [])
    monkeypatch.setattr(autostart_approvals, "_cache", set())
    monkeypatch.setattr(autostart_approvals, "_load_failed", False)
    monkeypatch.setattr(autostart_approvals, "_save_locked", lambda _value: True)

    def layout(plugin_id, installed_dir, **_kwargs):
        return resolve_plugin_layout(plugin_id, installed_dir, storage_root=storage)

    for module in (cli, config_paths, config_resolver):
        monkeypatch.setattr(module, "resolve_plugin_layout", layout)

    async def no_runtime(*_args, **_kwargs):
        return False

    async def no_refresh(*_args, **_kwargs):
        return None

    monkeypatch.setattr(replace, "_plugin_is_running", no_runtime)
    monkeypatch.setattr(replace, "_evict_replaced_plugin_modules", lambda _id: None)
    monkeypatch.setattr(replace, "_revoke_hot_reload_recovery", lambda _id: None)
    monkeypatch.setattr(cli, "_refresh_committed_market_install", no_refresh)
    manager = InstallSourceManager(
        lock_path=tmp_path / "plugins.lock.json", builtin_root=builtin, user_root=user,
        scanner=PluginDirectoryScanner(builtin, user),
    )
    previous = get_install_source_manager()
    manager.load()
    set_global_manager(manager)
    monkeypatch.setattr(uninstall, "get_user_plugin_exec_root", lambda: user)
    monkeypatch.setattr(uninstall, "get_plugin_state_root", lambda: storage / "plugins")
    monkeypatch.setattr(uninstall, "BUILTIN_PLUGIN_CONFIG_ROOT", builtin)
    monkeypatch.setattr(uninstall, "PLUGIN_CONFIG_ROOTS", (user, builtin))
    monkeypatch.setattr(uninstall, "ensure_plugin_exec_state_roots_separated", lambda: None)
    monkeypatch.setattr(uninstall, "_plugin_is_running_sync", lambda _id: False)
    monkeypatch.setattr(uninstall, "_remove_runtime_metadata_sync", lambda _id: None)
    monkeypatch.setattr(uninstall, "_refresh_registry", no_refresh)
    monkeypatch.setattr(
        uninstall, "_get_plugin_meta_sync",
        lambda plugin_id: {"id": plugin_id, "config_path": str(user / plugin_id / "plugin.toml")}
        if (user / plugin_id / "plugin.toml").is_file() else None,
    )
    monkeypatch.setattr(
        uninstall, "_resolve_plugin_config_path_sync", lambda _id, meta: Path(meta["config_path"]),
    )
    try:
        yield SimpleNamespace(
            root=tmp_path, user=user, builtin=builtin, packages=packages, profiles=profiles,
            storage=storage, layout=layout, manager=manager, service=cli.PluginCliService(),
        )
    finally:
        set_global_manager(previous)


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["imported", "market"])
@pytest.mark.parametrize("history", ["absent", "orphan", "legacy", "owned"])
async def test_install_uninstall_reinstall_preserves_state_and_ignores_package_profiles(
    environment, channel, history,
):
    env = environment
    package = env.packages / f"{PLUGIN_ID}.neko-plugin"
    build_plugin(_source(env.root / "source"), package)
    assert inspect_package(package).profile_names == ["default.toml"]
    profile = env.profiles / PLUGIN_ID
    if history != "absent":
        profile.mkdir(parents=True)
        (profile / "default.toml").write_bytes(b"legacy package configuration\r\n")
        if history in {"legacy", "owned"}:
            old = env.user / PLUGIN_ID
            old.mkdir(parents=True)
            env.manager.record_import(
                directory_path=old, package_filename=package.name, package_sha256="a" * 64,
                package_id=PLUGIN_ID, profile_dir=str(profile),
            )
            env.manager.mark_removed(directory_path=old)
            old.rmdir()
            if history == "legacy":
                doc = json.loads(env.manager.lock_path.read_text(encoding="utf-8"))
                for row in doc["entries"]:
                    row.pop("profile_installed", None)
                    row.pop("profile_dir", None)
                env.manager.lock_path.write_text(json.dumps(doc), encoding="utf-8")
                env.manager.load()
    before = {p.name: p.read_bytes() for p in profile.iterdir()} if profile.exists() else None

    async def install():
        if channel == "imported":
            return await env.service.install(package=str(package), install_source="imported")
        uploaded = await env.service.upload_and_install(
            filename=package.name, package_path=str(package),
            install_source_override={
                "channel": "market", "mode": "install",
                "market_detail": {
                    "plugin_market_id": PLUGIN_ID, "version": "1.0.0",
                    "package_url": "https://example.invalid/demo.neko-plugin",
                    "expected_plugin_toml_id": PLUGIN_ID,
                },
            },
        )
        return uploaded["unpack"]

    first = await install()
    assert first["profile_dir"] is None
    layout = env.layout(PLUGIN_ID, env.user / PLUGIN_ID)
    config = config_resolver.resolve_plugin_config_from_path(
        PLUGIN_ID, config_path=layout.manifest_path, validate_schema=False,
    )
    assert config["effective_config"]["settings"]["value"] == "custom"
    layout.config_path.write_text('[settings]\nvalue="user edit"\n', encoding="utf-8")
    for path in (layout.data_dir / "journal.txt", layout.cache_dir / "derived.bin"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"user state")
    state_before = {p: p.read_bytes() for p in (layout.config_path, layout.data_dir / "journal.txt", layout.cache_dir / "derived.bin")}
    removed = await uninstall.uninstall_plugin(PLUGIN_ID)
    assert removed.deleted_from_disk and removed.deleted_profile_dir is None
    assert not layout.installed_dir.exists()
    second = await install()
    assert second["profile_dir"] is None
    assert (layout.installed_dir / "profiles/custom.toml").is_file()
    assert {p: p.read_bytes() for p in state_before} == state_before
    assert ({p.name: p.read_bytes() for p in profile.iterdir()} if profile.exists() else None) == before
    if history == "absent":
        assert not env.profiles.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_staging", [False, True])
async def test_unused_package_profile_root_can_be_a_file(environment, use_staging):
    env = environment
    env.profiles.write_bytes(b"reserved path")
    package = env.packages / f"{PLUGIN_ID}.neko-plugin"
    build_plugin(_source(env.root / "source"), package)
    result = await env.service.install(
        package=str(package), use_staging=use_staging, install_source="imported",
    )
    assert result["profile_dir"] is None
    assert result["profiles_root"] is None
    assert env.profiles.read_bytes() == b"reserved path"
    assert (env.user / PLUGIN_ID / "plugin.toml").is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("next_version", ["1.0.0", "2.0.0"], ids=["reinstall", "upgrade"])
@pytest.mark.parametrize("fail_validation", [False, True], ids=["success", "rollback"])
async def test_local_replacement_keeps_runtime_and_named_profiles(
    environment, monkeypatch, next_version, fail_validation,
):
    env = environment
    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    target = env.user / PLUGIN_ID
    named_profile = target / "profiles/custom.toml"
    named_profile.write_bytes(b'[settings]\nvalue="edited named profile"\n')
    layout = env.layout(PLUGIN_ID, target)
    config_resolver.resolve_plugin_config_from_path(
        PLUGIN_ID, config_path=layout.manifest_path, validate_schema=False,
    )
    layout.config_path.write_bytes(b'[settings]\nvalue="runtime edit"\n')
    sentinel = env.profiles / PLUGIN_ID / "default.toml"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"old package export")
    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version=next_version), package)
    plan = await env.service.plan_install(package=str(package))
    assert plan["action"] == ("reinstall" if next_version == "1.0.0" else "upgrade")
    if fail_validation:
        def reject_new_code(_layout):
            raise RuntimeError("injected post-install validation failure")

        monkeypatch.setattr(replace, "_validate_installed_identity", reject_new_code)
    arguments = dict(
        package=str(package), install_source="imported", confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )
    if fail_validation:
        with pytest.raises(ServerDomainError) as captured:
            await env.service.install(**arguments)
        assert captured.value.code == "PLUGIN_UPGRADE_ROLLED_BACK"
        assert captured.value.details["stage"] == "validate"
        assert captured.value.details["rollback_status"] == "completed"
    else:
        result = await env.service.install(**arguments)
        assert result["profile_dir"] is None
    assert named_profile.read_bytes() == b'[settings]\nvalue="edited named profile"\n'
    assert layout.config_path.read_bytes() == b'[settings]\nvalue="runtime edit"\n'
    assert sentinel.read_bytes() == b"old package export"
    assert layout.manifest_path.is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("retained", [False, True])
async def test_degraded_lock_creates_no_package_profile_and_reimport_succeeds(environment, retained):
    env = environment
    package = env.packages / f"{PLUGIN_ID}.neko-plugin"
    build_plugin(_source(env.root / "source"), package)
    sentinel = env.profiles / PLUGIN_ID / "default.toml"
    if retained:
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"unowned legacy export")
    # A directory in place of the lock reproduces issue #3180's read-only degrade.
    env.manager.lock_path.mkdir()
    env.manager.load()
    result = await env.service.install(package=str(package), install_source="imported")
    assert result["install_source_warning"]
    assert result["profile_dir"] is None
    # Simulate code-only removal without touching the user's real filesystem.
    (env.user / PLUGIN_ID).rename(env.root / "removed-code")
    env.manager.lock_path.rmdir()
    env.manager.load()
    reinstalled = await env.service.install(package=str(package), install_source="imported")
    assert reinstalled["profile_dir"] is None
    assert (env.user / PLUGIN_ID / "plugin.toml").is_file()
    if retained:
        assert sentinel.read_bytes() == b"unowned legacy export"
    else:
        assert not env.profiles.exists()


@pytest.mark.asyncio
async def test_bundle_installs_without_claiming_shared_package_profile(environment):
    env = environment
    package = env.packages / "demo.neko-bundle"
    build_bundle(
        [_source(env.root / "source-a", "profile_member_a"), _source(env.root / "source-b", "profile_member_b")],
        package, bundle_id="shared_profile_bundle",
    )
    sentinel = env.profiles / "shared_profile_bundle/default.toml"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_bytes(b"shared legacy export")
    result = await env.service.install(package=str(package), install_source="imported")
    assert result["installed_plugin_count"] == 2
    assert result["profile_dir"] is None
    assert sentinel.read_bytes() == b"shared legacy export"
    assert all((env.user / pid / "plugin.toml").is_file() for pid in ("profile_member_a", "profile_member_b"))


@pytest.mark.asyncio
async def test_skipped_package_profile_is_still_hash_verified(environment):
    env = environment
    package = env.packages / f"{PLUGIN_ID}.neko-plugin"
    build_plugin(_source(env.root / "source"), package)
    # Corrupt the profile's existing member without duplicate ZIP entries.
    with zipfile.ZipFile(package) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    with zipfile.ZipFile(package, "w") as archive:
        for info, content in members:
            archive.writestr(info, b"tampered" if info.filename == "payload/profiles/default.toml" else content)
    with pytest.raises(ServerDomainError) as captured:
        await env.service.install(package=str(package))
    assert captured.value.code == "PLUGIN_PACKAGE_HASH_MISMATCH"
    assert not (env.user / PLUGIN_ID).exists()
    assert not env.profiles.exists()


def test_complete_unpack_still_exports_package_profiles(environment):
    env = environment
    package = env.packages / f"{PLUGIN_ID}.neko-plugin"
    build_plugin(_source(env.root / "source"), package)
    result = unpack_package(
        package, plugins_root=env.root / "unpacked-code", profiles_root=env.root / "unpacked-profiles",
    )
    assert result.profile_dir is not None
    assert (result.profile_dir / "default.toml").is_file()
    assert (env.root / "unpacked-code" / PLUGIN_ID / "profiles/custom.toml").is_file()
