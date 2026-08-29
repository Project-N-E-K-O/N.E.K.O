from __future__ import annotations

from pathlib import Path

import pytest

import plugin.settings as settings
from plugin.server.application.install_source import manager as manager_module
from plugin.server.application.install_source.manager import resolve_lock_path
from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy

pytestmark = pytest.mark.plugin_unit


def test_execution_root_scope_folds_case_only_on_case_insensitive_filesystems(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upper = "C:/Users/Neko/Plugins"
    lower = "C:/Users/Neko/plugins"
    monkeypatch.setattr(manager_module.os.path, "normcase", lambda value: value)

    monkeypatch.setattr(
        manager_module,
        "_filesystem_is_case_insensitive",
        lambda _path: True,
    )
    assert manager_module._execution_root_scope(upper) == manager_module._execution_root_scope(
        lower
    )

    monkeypatch.setattr(
        manager_module,
        "_filesystem_is_case_insensitive",
        lambda _path: False,
    )
    assert manager_module._execution_root_scope(upper) != manager_module._execution_root_scope(
        lower
    )


def test_execution_root_scope_preserves_unicode_distinctions_on_sensitive_filesystems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    composed = tmp_path / "caf\N{LATIN SMALL LETTER E WITH ACUTE}"
    decomposed = tmp_path / "cafe\N{COMBINING ACUTE ACCENT}"
    monkeypatch.setattr(
        manager_module,
        "_filesystem_is_case_insensitive",
        lambda _path: False,
    )

    assert manager_module._execution_root_scope(str(composed)) != manager_module._execution_root_scope(
        str(decomposed)
    )


def test_default_layout_separates_exec_from_state_and_keeps_metadata_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "N.E.K.O" / "plugins"
    monkeypatch.delenv("PLUGIN_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("PACKAGE_PROFILES_ROOT", raising=False)
    monkeypatch.delenv("PLUGIN_PACKAGES_ROOT", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_INSTALL_LOCK_PATH", raising=False)
    monkeypatch.setattr(settings, "get_plugins_directory", lambda: state_root)

    expected_exec = tmp_path / "N.E.K.O" / ".neko-plugin-installations" / "plugins"
    assert settings.get_plugin_state_root() == state_root.resolve()
    assert settings.get_user_plugin_exec_root() == expected_exec.resolve()
    assert settings.get_user_plugin_config_root() == expected_exec.resolve()
    assert settings.get_user_package_profiles_root() == (
        tmp_path / "N.E.K.O" / ".neko-package-profiles"
    ).resolve()
    assert settings.get_user_plugin_packages_root() == (
        tmp_path / "N.E.K.O" / ".neko-plugin-packages"
    ).resolve()
    assert resolve_lock_path() == (tmp_path / "N.E.K.O" / "plugins.lock.json").resolve()


def test_explicit_legacy_config_root_remains_the_execution_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custom_exec = tmp_path / "custom-code"
    state_root = tmp_path / "user" / "plugins"
    monkeypatch.setenv("PLUGIN_CONFIG_ROOT", str(custom_exec))
    monkeypatch.delenv("PACKAGE_PROFILES_ROOT", raising=False)
    monkeypatch.delenv("PLUGIN_PACKAGES_ROOT", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_INSTALL_LOCK_PATH", raising=False)
    monkeypatch.setattr(settings, "get_plugins_directory", lambda: state_root)

    assert settings.get_user_plugin_exec_root() == custom_exec.resolve()
    assert settings.get_user_package_profiles_root() == (
        custom_exec.parent / ".neko-package-profiles"
    ).resolve()
    assert settings.get_user_plugin_packages_root() == (
        custom_exec.parent / ".neko-plugin-packages"
    ).resolve()
    assert settings.get_plugin_state_root() == state_root.resolve()
    lock_path = resolve_lock_path()
    assert lock_path.parent == state_root.parent.resolve()
    assert lock_path.name.startswith("plugins.")
    assert lock_path.name.endswith(".lock.json")


def test_explicit_execution_roots_get_distinct_install_source_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_root = tmp_path / "state" / "plugins"
    monkeypatch.setattr(settings, "get_plugins_directory", lambda: state_root)
    monkeypatch.delenv("NEKO_PLUGIN_INSTALL_LOCK_PATH", raising=False)

    first_root = tmp_path / "exec-a" / "plugins"
    second_root = tmp_path / "exec-b" / "plugins"
    monkeypatch.setenv("PLUGIN_CONFIG_ROOT", str(first_root))
    first_lock = resolve_lock_path()
    monkeypatch.setenv("PLUGIN_CONFIG_ROOT", str(second_root))
    second_lock = resolve_lock_path()

    assert first_lock.parent == state_root.parent.resolve()
    assert second_lock.parent == state_root.parent.resolve()
    assert first_lock != second_lock


def test_collision_has_stable_error_code(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    with pytest.raises(settings.PluginExecStateRootCollisionError) as exc_info:
        settings.ensure_plugin_exec_state_roots_separated(
            exec_root=root,
            state_root=root,
        )

    assert exc_info.value.code == settings.PLUGIN_EXEC_STATE_ROOT_COLLISION


@pytest.mark.parametrize("exec_is_child", [True, False])
def test_nested_exec_and_state_roots_are_rejected(
    tmp_path: Path,
    exec_is_child: bool,
) -> None:
    parent = tmp_path / "plugins"
    child = parent / "managed"
    exec_root, state_root = (child, parent) if exec_is_child else (parent, child)

    with pytest.raises(settings.PluginExecStateRootCollisionError):
        settings.ensure_plugin_exec_state_roots_separated(
            exec_root=exec_root,
            state_root=state_root,
        )


def test_path_policy_keeps_config_root_compatibility_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compat_exec = tmp_path / "compat-exec"
    state_root = tmp_path / "state"
    monkeypatch.setattr(settings, "BUILTIN_PLUGIN_CONFIG_ROOT", tmp_path / "builtin")
    monkeypatch.setattr(settings, "USER_PLUGIN_EXEC_ROOT", tmp_path / "unused-new-name")
    monkeypatch.setattr(settings, "USER_PLUGIN_CONFIG_ROOT", compat_exec)
    monkeypatch.setattr(settings, "PLUGIN_STATE_ROOT", state_root)
    monkeypatch.setattr(settings, "USER_PLUGIN_PACKAGES_ROOT", tmp_path / "packages")
    monkeypatch.setattr(settings, "USER_PACKAGE_PROFILES_ROOT", tmp_path / "profiles")

    policy = PluginCliPathPolicy.from_settings()

    assert policy.user_plugins_root == compat_exec.resolve()
    policy.ensure_writable_layout()


def test_path_policy_rejects_state_collision(tmp_path: Path) -> None:
    shared = tmp_path / "plugins"
    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin",
        user_plugins_root=shared,
        package_artifacts_root=tmp_path / "packages",
        package_profiles_root=tmp_path / "profiles",
        plugin_state_root=shared,
    )

    with pytest.raises(settings.PluginExecStateRootCollisionError) as exc_info:
        policy.ensure_writable_layout()

    assert exc_info.value.code == settings.PLUGIN_EXEC_STATE_ROOT_COLLISION


def test_path_policy_rejects_profile_root_nested_in_state(tmp_path: Path) -> None:
    state_root = tmp_path / "plugins"
    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin",
        user_plugins_root=tmp_path / "exec",
        package_artifacts_root=tmp_path / "packages",
        package_profiles_root=state_root / "profiles",
        plugin_state_root=state_root,
    )

    with pytest.raises(settings.PluginExecStateRootCollisionError):
        policy.ensure_writable_layout()


@pytest.mark.parametrize("relation", ["equal", "profile_child", "exec_child"])
def test_path_policy_rejects_exec_and_profile_root_collisions(
    tmp_path: Path,
    relation: str,
) -> None:
    shared = tmp_path / "managed"
    if relation == "equal":
        exec_root = profile_root = shared
    elif relation == "profile_child":
        exec_root, profile_root = shared, shared / "profiles"
    else:
        exec_root, profile_root = shared / "plugins", shared
    policy = PluginCliPathPolicy(
        builtin_plugins_root=tmp_path / "builtin",
        user_plugins_root=exec_root,
        package_artifacts_root=tmp_path / "packages",
        package_profiles_root=profile_root,
        plugin_state_root=tmp_path / "state",
    )

    with pytest.raises(settings.PluginExecStateRootCollisionError) as exc_info:
        policy.ensure_writable_layout()

    assert exc_info.value.code == settings.PLUGIN_EXEC_STATE_ROOT_COLLISION


@pytest.mark.parametrize("writable_kind", ["exec", "profiles"])
@pytest.mark.parametrize("relation", ["equal", "writable_child", "builtin_child"])
def test_path_policy_rejects_writable_roots_colliding_with_builtin(
    tmp_path: Path,
    writable_kind: str,
    relation: str,
) -> None:
    shared = tmp_path / "managed"
    if relation == "equal":
        writable_root = builtin_root = shared
    elif relation == "writable_child":
        builtin_root, writable_root = shared, shared / "writable"
    else:
        writable_root, builtin_root = shared, shared / "builtin"
    exec_root = writable_root if writable_kind == "exec" else tmp_path / "exec"
    profiles_root = writable_root if writable_kind == "profiles" else tmp_path / "profiles"
    policy = PluginCliPathPolicy(
        builtin_plugins_root=builtin_root,
        user_plugins_root=exec_root,
        package_artifacts_root=tmp_path / "packages",
        package_profiles_root=profiles_root,
        plugin_state_root=tmp_path / "state",
    )

    with pytest.raises(settings.PluginExecStateRootCollisionError) as exc_info:
        policy.ensure_writable_layout()

    assert exc_info.value.code == settings.PLUGIN_EXEC_STATE_ROOT_COLLISION


def test_path_policy_does_not_treat_archive_root_as_plugin_tree(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin"
    policy = PluginCliPathPolicy(
        builtin_plugins_root=builtin_root,
        user_plugins_root=tmp_path / "exec",
        package_artifacts_root=builtin_root,
        package_profiles_root=tmp_path / "profiles",
        plugin_state_root=tmp_path / "state",
    )

    policy.ensure_writable_layout()
