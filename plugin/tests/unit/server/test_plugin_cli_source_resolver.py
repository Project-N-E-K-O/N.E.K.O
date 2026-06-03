from __future__ import annotations

from pathlib import Path

import pytest

from plugin.server.application.plugin_cli.paths import PluginCliPathPolicy
from plugin.server.application.plugin_cli.source_resolver import PluginSourceResolver

pytestmark = pytest.mark.plugin_unit


def _make_policy(tmp_path: Path) -> PluginCliPathPolicy:
    builtin_root = tmp_path / "builtin"
    user_root = tmp_path / "user"
    packages_root = tmp_path / "packages"
    profiles_root = tmp_path / "profiles"
    for root in (builtin_root, user_root, packages_root, profiles_root):
        root.mkdir(parents=True, exist_ok=True)
    return PluginCliPathPolicy(
        builtin_plugins_root=builtin_root.resolve(),
        user_plugins_root=user_root.resolve(),
        package_artifacts_root=packages_root.resolve(),
        package_profiles_root=profiles_root.resolve(),
    )


def _write_plugin(root: Path, directory_name: str, plugin_id: str) -> Path:
    plugin_dir = root / directory_name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                f'name = "{plugin_id}"',
                'version = "0.1.0"',
                'type = "plugin"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return plugin_dir


def test_list_plugins_scans_builtin_then_user_with_stable_directory_sort(tmp_path: Path) -> None:
    policy = _make_policy(tmp_path)
    _write_plugin(policy.builtin_plugins_root, "z_builtin", "z_builtin")
    _write_plugin(policy.user_plugins_root, "a_user", "a_user")

    sources = PluginSourceResolver(policy).list_plugins()

    assert [(source.root_id, source.directory_name) for source in sources] == [
        ("builtin", "z_builtin"),
        ("user", "a_user"),
    ]


def test_plugin_ref_resolves_exact_root_and_directory_when_names_overlap(tmp_path: Path) -> None:
    policy = _make_policy(tmp_path)
    _write_plugin(policy.builtin_plugins_root, "shared", "builtin_shared")
    _write_plugin(policy.user_plugins_root, "shared", "user_shared")

    source = PluginSourceResolver(policy).resolve_plugin_ref(
        {"root_id": "user", "directory_name": "shared"}
    )

    assert source.root_id == "user"
    assert source.directory_name == "shared"
    assert source.plugin_id == "user_shared"


def test_plugin_ref_rejects_symlinked_directory_even_when_target_has_plugin_toml(
    tmp_path: Path,
) -> None:
    policy = _make_policy(tmp_path)
    outside_plugin = _write_plugin(tmp_path / "outside", "external", "external")
    link = policy.user_plugins_root / "linked_external"
    try:
        link.symlink_to(outside_plugin, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable on this platform: {exc}")

    with pytest.raises(ValueError, match="symlinks are not allowed"):
        PluginSourceResolver(policy).resolve_plugin_ref(
            {"root_id": "user", "directory_name": "linked_external"}
        )


@pytest.mark.parametrize(
    "directory_name",
    ["C:", "C:evil", "C:/evil", "/absolute", "\\absolute"],
)
def test_plugin_ref_rejects_windows_drive_or_root_directory_names(
    tmp_path: Path,
    directory_name: str,
) -> None:
    policy = _make_policy(tmp_path)

    with pytest.raises(ValueError, match="safe plugin directory name"):
        PluginSourceResolver(policy).resolve_plugin_ref(
            {"root_id": "user", "directory_name": directory_name}
        )


def test_absolute_path_is_allowed_only_for_top_level_builtin_or_user_plugin(
    tmp_path: Path,
) -> None:
    policy = _make_policy(tmp_path)
    user_plugin = _write_plugin(policy.user_plugins_root, "neko_minecraft", "neko_minecraft")
    outside_plugin = _write_plugin(tmp_path / "elsewhere", "neko_minecraft", "neko_minecraft")
    nested_file = user_plugin / "plugin.toml"
    resolver = PluginSourceResolver(policy)

    source = resolver.resolve_string(str(user_plugin.resolve()))

    assert source.root_id == "user"
    assert source.directory_name == "neko_minecraft"

    with pytest.raises(ValueError, match="inside builtin or user plugin roots"):
        resolver.resolve_string(str(outside_plugin.resolve()))

    with pytest.raises(ValueError, match="top-level plugin directory"):
        resolver.resolve_string(str(nested_file.resolve()))


def test_string_directory_name_ambiguity_is_reported_without_guessing(tmp_path: Path) -> None:
    policy = _make_policy(tmp_path)
    _write_plugin(policy.builtin_plugins_root, "same_name", "builtin_same")
    _write_plugin(policy.user_plugins_root, "same_name", "user_same")

    with pytest.raises(ValueError, match="ambiguous"):
        PluginSourceResolver(policy).resolve_string("same_name")


def test_string_plugin_id_ambiguity_is_reported_without_guessing(tmp_path: Path) -> None:
    policy = _make_policy(tmp_path)
    _write_plugin(policy.builtin_plugins_root, "builtin_dir", "same_plugin_id")
    _write_plugin(policy.user_plugins_root, "user_dir", "same_plugin_id")

    with pytest.raises(ValueError, match="ambiguous"):
        PluginSourceResolver(policy).resolve_string("same_plugin_id")
