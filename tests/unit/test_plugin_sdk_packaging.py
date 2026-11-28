from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
BUILD_WORKFLOWS = ("build-desktop.yml", "build-desktop-linux.yml")
REMOVED_ROOT_SDK_MODULES = (
    "plugin.sdk.base",
    "plugin.sdk.decorators",
    "plugin.sdk.events",
    "plugin.sdk.logger",
    "plugin.sdk.version",
    "plugin.sdk.extension",
)


def _ensure_project_root_first() -> None:
    """Keep the package scanner on the repository package tree."""
    root = str(ROOT)
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)


def _raise_import_error(name: str) -> None:
    raise ImportError(name)


def _sdk_module_names() -> list[str]:
    module_names: set[str] = set()
    for source_path in (ROOT / "plugin" / "sdk").rglob("*.py"):
        module_parts = source_path.relative_to(ROOT).with_suffix("").parts
        if module_parts[-1] == "__init__":
            module_parts = module_parts[:-1]
        module_names.add(".".join(module_parts))
    return sorted(module_names)


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_current_sdk_modules_are_importable() -> None:
    _ensure_project_root_first()
    for module_name in _sdk_module_names():
        importlib.import_module(module_name)


def test_package_scan_finds_complete_sdk_tree() -> None:
    _ensure_project_root_first()
    scanned = {"plugin.sdk"} | {
        module.name
        for module in pkgutil.walk_packages(
            [str(ROOT / "plugin" / "sdk")],
            prefix="plugin.sdk.",
            onerror=_raise_import_error,
        )
    }
    assert sorted(scanned) == _sdk_module_names()


def test_nuitka_workflows_compile_the_plugin_package_without_removed_sdk_modules() -> None:
    for name in BUILD_WORKFLOWS:
        workflow = _workflow(name)
        assert "--include-package=plugin" in workflow, name
        for removed_module in REMOVED_ROOT_SDK_MODULES:
            assert f"--include-package={removed_module}" not in workflow, (name, removed_module)
            assert f"--include-module={removed_module}" not in workflow, (name, removed_module)


def test_windows_nuitka_build_uses_project_root_icon() -> None:
    workflow = _workflow("build-desktop.yml")

    assert "--windows-icon-from-ico=assets/icon.ico" in workflow
    assert (ROOT / "assets" / "icon.ico").is_file()


def test_nuitka_workflows_compile_main_logic_with_the_omni_state_proxy() -> None:
    for name in BUILD_WORKFLOWS:
        assert "--include-package=main_logic" in _workflow(name), name
    assert (ROOT / "main_logic" / "_module_state_proxy.py").is_file()


def test_removed_root_sdk_modules_are_not_importable() -> None:
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert importlib.util.find_spec(removed_module) is None
