from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(__file__).resolve().parents[2]
REMOVED_ROOT_SDK_MODULES = (
    "plugin.sdk.base",
    "plugin.sdk.decorators",
    "plugin.sdk.events",
    "plugin.sdk.logger",
    "plugin.sdk.version",
    "plugin.sdk.extension",
)


def _sdk_module_names() -> list[str]:
    module_names: set[str] = set()
    for source_path in (ROOT / "plugin" / "sdk").rglob("*.py"):
        module_parts = source_path.relative_to(ROOT).with_suffix("").parts
        if module_parts[-1] == "__init__":
            module_parts = module_parts[:-1]
        module_names.add(".".join(module_parts))
    return sorted(module_names)


def test_current_sdk_modules_are_importable() -> None:
    for module_name in _sdk_module_names():
        importlib.import_module(module_name)


def test_pyinstaller_collects_complete_sdk_tree() -> None:
    assert sorted(collect_submodules("plugin.sdk", on_error="raise")) == (
        _sdk_module_names()
    )


def test_launcher_collects_current_plugin_sdk_tree() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")

    assert "collect_submodules('plugin.sdk', on_error='raise')" in spec
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert f"'{removed_module}'" not in spec


def test_launcher_uses_project_root_for_windows_resources() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")

    assert "VERSION_INFO_PATH = os.path.join(PROJECT_ROOT, 'version_info.txt')" in spec
    assert "os.path.isfile(VERSION_INFO_PATH)" in spec
    assert "ICON_PATH = os.path.join(PROJECT_ROOT, 'assets', 'icon.ico')" in spec
    assert "icon=ICON_PATH if sys.platform == 'win32' else None" in spec
    assert (
        "version=VERSION_INFO_PATH if sys.platform == 'win32' and "
        "os.path.isfile(VERSION_INFO_PATH) else None"
    ) in spec


def test_launcher_includes_omni_state_proxy_hidden_import() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")

    assert "'main_logic._module_state_proxy'" in spec


def test_removed_root_sdk_modules_are_not_importable() -> None:
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert importlib.util.find_spec(removed_module) is None
