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
from plugin.server.application.plugins.installation_transactions import replace, uninstall
from plugin.server.infrastructure import autostart_approvals, config_paths, config_resolver
from plugin.server.domain.errors import ServerDomainError
from plugin.server.infrastructure.plugin_permissions import PluginSource
from plugin.server.infrastructure import authoring_project_store

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
    # Keep the real user workbench out of discovery and install planning.
    monkeypatch.setattr(authoring_project_store, "list_records", lambda: [])
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
async def test_degraded_lock_creates_no_package_profile_and_reimport_succeeds(
    environment, retained, monkeypatch
):
    from plugin.server.application.plugins import capability_analysis_service

    # This test moves the directory directly instead of using the uninstall transaction.
    monkeypatch.setattr(capability_analysis_service.analysis_service, "installed", lambda _root: None)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("record_fails", [False, True], ids=["recorded", "record_failed"])
async def test_local_upgrade_does_not_keep_grants_when_its_source_is_not_recorded(
    environment, monkeypatch, record_fails,
):
    """Grants follow the recorded package source. When the upgraded package
    cannot be recorded, the previous package's grants are dropped instead of
    staying with the new code."""
    from plugin.server.application.plugins import permission_service

    env = environment
    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    permission = "plugin_sdk.main_agent.memory:read"
    permission_service.decide_permission(PLUGIN_ID, permission, "granted")
    assert permission_service.permission_store.read(PLUGIN_ID)["source"]["channel"] == "imported"

    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version="2.0.0"), package)
    plan = await env.service.plan_install(package=str(package))
    assert plan["action"] == "upgrade"
    if record_fails:
        def cannot_record(*_args, **_kwargs):
            raise OSError("lock file is busy")

        monkeypatch.setattr(cli, "_record_install_source_for_install_result", cannot_record)

    result = await env.service.install(
        package=str(package), install_source="imported", confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )

    assert 'version="2.0.0"' in (env.user / PLUGIN_ID / "plugin.toml").read_text(encoding="utf-8")
    assert bool(result.get("install_source_warning")) is record_fails
    # Either way the new package holds no grant of the previous one: a
    # recorded source is a different source, an unrecorded one drops them.
    assert permission not in permission_service.permission_store.read(PLUGIN_ID).get("permissions", {})


async def _installed_with_grant(env):
    """Install the first package as imported and grant it one permission."""
    from plugin.server.application.plugins import permission_service

    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    permission = "plugin_sdk.main_agent.memory:read"
    permission_service.decide_permission(PLUGIN_ID, permission, "granted")
    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version="2.0.0"), package)
    plan = await env.service.plan_install(package=str(package))
    return permission, package, plan


@pytest.mark.asyncio
@pytest.mark.parametrize("record_fails", [False, True], ids=["recorded", "record_failed"])
async def test_running_local_upgrade_restarts_without_the_previous_grants(
    environment, monkeypatch, record_fails,
):
    """The source is settled before the restart, so the new code never starts
    under the previous package's source or grants."""
    from plugin.server.application.plugins import permission_service

    env = environment
    permission, package, plan = await _installed_with_grant(env)
    first_source = permission_service.current_source(PLUGIN_ID)
    if record_fails:
        def cannot_record(*_args, **_kwargs):
            raise OSError("lock file is busy")

        monkeypatch.setattr(cli, "_record_install_source_for_install_result", cannot_record)

    seen_at_start: list[tuple[object, dict]] = []

    async def running(_plugin_id):
        return True

    async def stop(_plugin_id):
        return None

    async def start(plugin_id):
        seen_at_start.append(
            (permission_service.current_source(plugin_id), permission_service.permission_record(plugin_id))
        )

    monkeypatch.setattr(replace, "_plugin_is_running", running)
    monkeypatch.setattr(replace, "_stop_plugin", stop)
    monkeypatch.setattr(replace, "_start_plugin", start)

    result = await env.service.install(
        package=str(package), install_source="imported", confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )

    assert result["restarted"] is True
    assert bool(result.get("install_source_warning")) is record_fails
    [(source_at_start, record_at_start)] = seen_at_start
    if record_fails:
        assert source_at_start == first_source
    else:
        assert source_at_start != first_source
        assert source_at_start.fingerprint == cli.PluginCliService._sha256_file(package)
    assert permission not in record_at_start.get("permissions", {})


@pytest.mark.asyncio
async def test_failed_local_upgrade_puts_the_previous_source_and_grants_back(
    environment, monkeypatch,
):
    """A replacement that records its source before the restart and then
    rolls back restores the lock entry and the grants of the previous package."""
    from plugin.server.application.plugins import permission_service

    env = environment
    permission, package, plan = await _installed_with_grant(env)
    first_source = permission_service.current_source(PLUGIN_ID)
    starts = 0

    async def running(_plugin_id):
        return True

    async def stop(_plugin_id):
        return None

    async def start(plugin_id):
        nonlocal starts
        starts += 1
        if starts == 1:
            raise RuntimeError("new package does not start")
        # The rollback restart, as start_plugin does it.
        permission_service.reconcile_permission_source(plugin_id)

    pushed: list[dict] = []

    async def push(plugin_id):
        pushed.append(permission_service.permission_record(plugin_id))
        return True

    monkeypatch.setattr(replace, "_plugin_is_running", running)
    monkeypatch.setattr(replace, "_stop_plugin", stop)
    monkeypatch.setattr(replace, "_start_plugin", start)
    monkeypatch.setattr(cli, "push_permissions", push)

    with pytest.raises(ServerDomainError) as captured:
        await env.service.install(
            package=str(package), install_source="imported", confirm_upgrade=True,
            confirmation_token=plan["confirmation_token"],
        )

    # The restarted previous plugin is sent its grants as they apply under
    # the restored source.
    [pushed_record] = pushed
    assert pushed_record["permissions"][permission]["state"] == "granted"
    assert captured.value.code == "PLUGIN_UPGRADE_ROLLED_BACK"
    assert captured.value.details.get("rollback_status") == "completed"
    assert 'version="1.0.0"' in (env.user / PLUGIN_ID / "plugin.toml").read_text(encoding="utf-8")
    assert permission_service.current_source(PLUGIN_ID) == first_source
    record = permission_service.permission_store.read(PLUGIN_ID)
    assert record["source"] == first_source.to_dict()
    assert record["permissions"][permission]["state"] == "granted"


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", ["taken", "unavailable"])
async def test_stopped_local_upgrade_rollback_accounts_for_dropped_grants(
    environment, monkeypatch, snapshot,
):
    """Without a restart the grants still change when the source cannot be
    recorded (they are dropped inside the transaction). A rollback puts them
    back, and reports itself incomplete when it has no snapshot to do so."""
    from plugin.server.application.plugins import permission_service

    env = environment
    permission, package, plan = await _installed_with_grant(env)

    def cannot_record(*_args, **_kwargs):
        raise OSError("lock file is busy")

    def invalid_identity(_layout):
        raise RuntimeError("installed identity does not match")

    monkeypatch.setattr(cli, "_record_install_source_for_install_result", cannot_record)
    monkeypatch.setattr(replace, "_validate_installed_identity", invalid_identity)
    if snapshot == "unavailable":
        monkeypatch.setattr(
            replace, "_snapshot_permissions",
            lambda _plugin_id: permission_service.PermissionSnapshot("unavailable"),
        )

    with pytest.raises(ServerDomainError) as captured:
        await env.service.install(
            package=str(package), install_source="imported", confirm_upgrade=True,
            confirmation_token=plan["confirmation_token"],
        )

    record = permission_service.permission_store.read(PLUGIN_ID)
    if snapshot == "taken":
        assert captured.value.details.get("rollback_status") == "completed"
        assert record["permissions"][permission]["state"] == "granted"
    else:
        assert captured.value.details.get("rollback_status") == "incomplete"
        assert permission not in record.get("permissions", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("record_fails", [False, True], ids=["recorded", "record_failed"])
async def test_local_upgrade_rollback_without_a_previous_lock_entry(
    environment, monkeypatch, record_fails,
):
    """With no previous lock entry to put back, only a source that was
    actually recorded makes the rollback incomplete."""
    env = environment
    _permission, package, plan = await _installed_with_grant(env)
    monkeypatch.setattr(cli, "_lock_entry_for_plugin", lambda *_args: None)
    if record_fails:
        def cannot_record(*_args, **_kwargs):
            raise OSError("lock file is busy")

        monkeypatch.setattr(cli, "_record_install_source_for_install_result", cannot_record)

    def invalid_identity(_layout):
        raise RuntimeError("installed identity does not match")

    monkeypatch.setattr(replace, "_validate_installed_identity", invalid_identity)

    with pytest.raises(ServerDomainError) as captured:
        await env.service.install(
            package=str(package), install_source="imported", confirm_upgrade=True,
            confirmation_token=plan["confirmation_token"],
        )

    expected = "completed" if record_fails else "incomplete"
    assert captured.value.details.get("rollback_status") == expected
    assert ("source_rollback" in captured.value.details) is not record_fails


@pytest.mark.asyncio
@pytest.mark.parametrize("market_detail", ["complete", "incomplete"])
async def test_running_market_replacement_restarts_under_the_new_source(
    environment, monkeypatch, market_detail,
):
    """A Market package records its source inside the replacement, before
    the restart (an incomplete Market record falls back to the imported
    channel there too), so the new code starts without the previous grants."""
    from plugin.server.application.plugins import permission_service

    env = environment
    permission, package, _plan = await _installed_with_grant(env)
    first_source = permission_service.current_source(PLUGIN_ID)
    seen_at_start: list[tuple[object, dict]] = []

    async def running(_plugin_id):
        return True

    async def stop(_plugin_id):
        return None

    async def start(plugin_id):
        seen_at_start.append(
            (permission_service.current_source(plugin_id), permission_service.permission_record(plugin_id))
        )

    monkeypatch.setattr(replace, "_plugin_is_running", running)
    monkeypatch.setattr(replace, "_stop_plugin", stop)
    monkeypatch.setattr(replace, "_start_plugin", start)
    detail = {"plugin_market_id": "market-demo", "expected_plugin_toml_id": PLUGIN_ID}
    if market_detail == "complete":
        detail.update(version="2.0.0", package_url="https://example.invalid/demo.neko-plugin")

    async def install_new():
        return await env.service.upload_and_install(
            filename=package.name, package_path=str(package), on_conflict="fail",
            install_source_override={
                "channel": "market", "mode": "upgrade", "directory_name": PLUGIN_ID,
                "market_detail": detail,
            },
            _analyze_after_commit=False,
        )

    await replace.replace_plugin(
        layout=env.layout(PLUGIN_ID, env.user / PLUGIN_ID), install_new=install_new,
    )

    [(source_at_start, record_at_start)] = seen_at_start
    expected = (
        PluginSource("market", "market-demo")
        if market_detail == "complete"
        else PluginSource("imported", cli.PluginCliService._sha256_file(package))
    )
    assert source_at_start == expected != first_source
    assert permission not in record_at_start.get("permissions", {})


@pytest.mark.asyncio
async def test_local_upgrade_reports_grants_it_could_not_drop(environment, monkeypatch):
    from plugin.server.application.plugins import permission_service

    env = environment
    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version="2.0.0"), package)
    plan = await env.service.plan_install(package=str(package))

    def cannot_record(*_args, **_kwargs):
        raise OSError("lock file is busy")

    def cannot_forget(_plugin_id):
        raise OSError("store is busy")

    monkeypatch.setattr(cli, "_record_install_source_for_install_result", cannot_record)
    monkeypatch.setattr(permission_service, "forget_permissions", cannot_forget)

    result = await env.service.install(
        package=str(package), install_source="imported", confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )

    assert "permission grants of the previous package could not be cleared" in result[
        "install_source_warning"
    ]


@pytest.mark.asyncio
async def test_local_upgrade_binds_a_source_less_record_before_replacing(environment):
    """A grant record without a source is bound to the installed package
    first, so the upgraded package is a source change, not a first binding."""
    from plugin.server.application.plugins import permission_service

    env = environment
    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    permission = "plugin_sdk.main_agent.memory:read"
    permission_service.permission_store.decide(PLUGIN_ID, permission, "granted", source=None)
    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version="2.0.0"), package)
    plan = await env.service.plan_install(package=str(package))

    result = await env.service.install(
        package=str(package), install_source="imported", confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )

    assert not result.get("install_source_warning")
    record = permission_service.permission_store.read(PLUGIN_ID)
    assert record["source"] == permission_service.current_source(PLUGIN_ID).to_dict()
    assert permission not in record["permissions"]


@pytest.mark.asyncio
@pytest.mark.parametrize("install_source", ["imported", None])
async def test_local_upgrade_stops_unchanged_when_the_binding_cannot_be_saved(
    environment, monkeypatch, install_source,
):
    """Only a replacement that records a new source binds first; one that
    records none keeps the current source and is not stopped by the store."""
    from plugin.server.application.plugins import permission_service

    env = environment
    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version="2.0.0"), package)
    plan = await env.service.plan_install(package=str(package))

    def cannot_bind(_plugin_id, _source):
        raise ServerDomainError("PLUGIN_PERMISSIONS_WRITE_FAILED", "store unavailable", 503)

    monkeypatch.setattr(permission_service.permission_store, "bind_unbound_source", cannot_bind)

    arguments = dict(
        package=str(package), install_source=install_source, confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )
    manifest = env.user / PLUGIN_ID / "plugin.toml"
    if install_source is None:
        await env.service.install(**arguments)
        assert 'version="2.0.0"' in manifest.read_text(encoding="utf-8")
        return
    with pytest.raises(ServerDomainError) as captured:
        await env.service.install(**arguments)

    assert captured.value.code == "PLUGIN_PERMISSIONS_WRITE_FAILED"
    assert 'version="1.0.0"' in manifest.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", ["PLUGIN_PERMISSIONS_CORRUPT", "PLUGIN_PERMISSIONS_UNSUPPORTED_VERSION"]
)
async def test_local_upgrade_does_not_report_grants_of_an_unusable_store(
    environment, monkeypatch, code,
):
    """A damaged or newer store holds no usable grant: nothing is left to drop."""
    from plugin.server.application.plugins import permission_service

    env = environment
    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version="2.0.0"), package)
    plan = await env.service.plan_install(package=str(package))

    def cannot_record(*_args, **_kwargs):
        raise OSError("lock file is busy")

    def unusable(*_args, **_kwargs):
        raise ServerDomainError(code, "permission store unusable", 500)

    monkeypatch.setattr(cli, "_record_install_source_for_install_result", cannot_record)
    monkeypatch.setattr(permission_service.permission_store, "bind_unbound_source", unusable)
    monkeypatch.setattr(permission_service, "forget_permissions", unusable)

    result = await env.service.install(
        package=str(package), install_source="imported", confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )

    assert result["install_source_warning"]
    assert "could not be cleared" not in result["install_source_warning"]


@pytest.mark.asyncio
async def test_local_upgrade_binds_from_the_lock_entry_while_the_registry_is_busy(
    environment, monkeypatch,
):
    """The binding before a local upgrade reads the target directory's lock
    entry, so a registry lookup that times out does not let the upgraded
    package take over a source-less grant."""
    from plugin.server.application.plugins import permission_service

    env = environment
    first_package = env.packages / "original.neko-plugin"
    build_plugin(_source(env.root / "original"), first_package)
    await env.service.install(package=str(first_package), install_source="imported")
    previous = env.manager.entry_for_directory(env.user / PLUGIN_ID)
    permission = "plugin_sdk.main_agent.memory:read"
    permission_service.permission_store.decide(PLUGIN_ID, permission, "granted", source=None)
    package = env.packages / "replacement.neko-plugin"
    build_plugin(_source(env.root / "replacement", version="2.0.0"), package)
    plan = await env.service.plan_install(package=str(package))

    def busy(_plugin_id):
        raise TimeoutError("registry read lock busy")

    monkeypatch.setattr(config_paths, "_resolve_registered_plugin_config_path", busy)
    assert permission_service.current_source(PLUGIN_ID) is None

    result = await env.service.install(
        package=str(package), install_source="imported", confirm_upgrade=True,
        confirmation_token=plan["confirmation_token"],
    )

    assert not result.get("install_source_warning")
    record = permission_service.permission_store.read(PLUGIN_ID)
    assert record["source"] == {
        "channel": "imported",
        "fingerprint": previous.source_detail.package_sha256,
    }
    current = env.manager.entry_for_directory(env.user / PLUGIN_ID)
    assert current.source_detail.package_sha256 != previous.source_detail.package_sha256
    synced = permission_service.permission_store.sync_source(
        PLUGIN_ID,
        PluginSource("imported", current.source_detail.package_sha256),
    )
    assert permission not in synced.get("permissions", {})


def test_lock_entry_lookup_does_not_trust_a_degraded_lock(environment, monkeypatch):
    env = environment
    target = env.user / PLUGIN_ID
    entry = SimpleNamespace(plugin_id=PLUGIN_ID)
    manager = SimpleNamespace(is_degraded=False, entry_for_directory=lambda _path: entry)

    assert cli._lock_entry_for_plugin(manager, target, PLUGIN_ID) is entry
    assert cli._lock_entry_for_plugin(manager, target, "another") is None
    manager.is_degraded = True
    assert cli._lock_entry_for_plugin(manager, target, PLUGIN_ID) is None
    assert cli._lock_entry_for_plugin(None, target, PLUGIN_ID) is None
