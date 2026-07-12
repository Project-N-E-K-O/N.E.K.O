from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REMOVED_ROOT_SDK_MODULES = (
    "plugin.sdk.base",
    "plugin.sdk.decorators",
    "plugin.sdk.events",
    "plugin.sdk.logger",
    "plugin.sdk.version",
    "plugin.sdk.extension",
)


def test_launcher_collects_current_plugin_sdk_tree() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")

    assert "collect_submodules('plugin.sdk')" in spec
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert f"'{removed_module}'" not in spec


def test_launcher_uses_project_root_for_windows_resources() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")

    assert "VERSION_INFO_PATH = os.path.join(PROJECT_ROOT, 'version_info.txt')" in spec
    assert "os.path.isfile(VERSION_INFO_PATH)" in spec
    assert "ICON_PATH = os.path.join(PROJECT_ROOT, 'assets', 'icon.ico')" in spec


def test_removed_root_sdk_modules_are_not_importable() -> None:
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert importlib.util.find_spec(removed_module) is None
