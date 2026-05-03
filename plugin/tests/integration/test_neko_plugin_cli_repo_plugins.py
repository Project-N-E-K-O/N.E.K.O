from __future__ import annotations

from pathlib import Path

import pytest

from plugin.neko_plugin_cli import cli as neko_plugin_cli
from plugin.neko_plugin_cli.public import inspect_package, pack_plugin, unpack_package
from plugin.neko_plugin_cli.public.plugin_source import load_plugin_source

REPO_PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "plugins"


def _repo_plugin_dirs() -> list[Path]:
    return sorted(path.parent.resolve() for path in REPO_PLUGINS_ROOT.glob("*/plugin.toml") if path.is_file())


def _repo_packable_plugin_dirs() -> list[Path]:
    return [plugin_dir for plugin_dir in _repo_plugin_dirs() if load_plugin_source(plugin_dir).package_type == "plugin"]


@pytest.mark.plugin_integration
def test_repo_plugins_can_be_loaded_and_classified() -> None:
    plugin_dirs = _repo_plugin_dirs()
    assert plugin_dirs, "expected plugin/plugins to contain repository plugins"

    by_type: dict[str, list[str]] = {}
    for plugin_dir in plugin_dirs:
        source = load_plugin_source(plugin_dir)
        by_type.setdefault(source.package_type, []).append(source.plugin_id)
        assert source.plugin_id
        assert source.name
        assert source.version

    assert "plugin" in by_type


@pytest.mark.plugin_integration
@pytest.mark.parametrize("plugin_dir", _repo_plugin_dirs(), ids=lambda path: path.name)
def test_repo_plugin_packaging_matches_current_package_type_contract(tmp_path: Path, plugin_dir: Path) -> None:
    source = load_plugin_source(plugin_dir)
    package_path = tmp_path / f"{plugin_dir.name}.neko-plugin"

    if source.package_type != "plugin":
        with pytest.raises(ValueError, match="single-plugin pack only supports package_type='plugin'"):
            pack_plugin(plugin_dir, package_path)
        return

    pack_result = pack_plugin(plugin_dir, package_path)
    inspect_result = inspect_package(package_path)
    unpack_result = unpack_package(
        package_path,
        plugins_root=tmp_path / "plugins",
        profiles_root=tmp_path / "profiles",
        on_conflict="rename",
    )

    assert pack_result.plugin_id == source.plugin_id
    assert inspect_result.package_id == source.plugin_id
    assert inspect_result.package_type == "plugin"
    assert inspect_result.payload_hash_verified is True
    assert inspect_result.plugin_count == 1
    assert unpack_result.payload_hash_verified is True
    assert unpack_result.unpacked_plugin_count == 1
    assert (unpack_result.unpacked_plugins[0].target_dir / "plugin.toml").is_file()


@pytest.mark.plugin_integration
def test_cli_batch_smoke_can_pack_current_repo_plugin_packages(tmp_path: Path) -> None:
    target_dir = tmp_path / "target"
    packable_plugin_dirs = _repo_packable_plugin_dirs()

    assert packable_plugin_dirs, "expected at least one packable repository plugin"

    for plugin_dir in packable_plugin_dirs:
        exit_code = neko_plugin_cli.main(
            ["pack", str(plugin_dir), "--target-dir", str(target_dir)]
        )
        assert exit_code == 0

        package_path = target_dir / f"{plugin_dir.name}.neko-plugin"
        assert package_path.is_file()

        inspect_result = inspect_package(package_path)
        assert inspect_result.package_type == "plugin"
        assert inspect_result.payload_hash_verified is True
