from __future__ import annotations

import importlib
import importlib.util
import pkgutil
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
NUITKA_STEPS = (
    ("build-desktop.yml", "Build with Nuitka (Unix)"),
    ("build-desktop.yml", "Build with Nuitka (Windows)"),
    ("build-desktop-linux.yml", "Build with Nuitka"),
)
REMOVED_ROOT_SDK_MODULES = (
    "plugin.sdk.base",
    "plugin.sdk.decorators",
    "plugin.sdk.events",
    "plugin.sdk.logger",
    "plugin.sdk.version",
    "plugin.sdk.extension",
)

_BASH_APPEND = 'NUITKA_OPTS="$NUITKA_OPTS '
_CMD_APPEND = "set NUITKA_OPTS=%NUITKA_OPTS% "


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


def _nuitka_script(workflow: str, step_name: str) -> str:
    jobs = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"]
    steps = [step for step in jobs["build-python"]["steps"] if step.get("name") == step_name]
    assert len(steps) == 1, f"{workflow}: step {step_name!r} not found exactly once"
    return steps[0]["run"]


def _nuitka_flags(script: str, *, unconditional_only: bool) -> list[str]:
    """Flags appended to NUITKA_OPTS; lines inside if blocks count only when asked for."""
    flags: list[str] = []
    for line in script.splitlines():
        candidate = line if unconditional_only else line.strip()
        if candidate.startswith(_BASH_APPEND) and candidate.endswith('"'):
            flags.extend(candidate[len(_BASH_APPEND) : -1].split())
        elif candidate.startswith(_CMD_APPEND):
            flags.extend(candidate[len(_CMD_APPEND) :].split())
        elif not unconditional_only and _CMD_APPEND in candidate:
            # cmd one-liners: `if exist "x" set NUITKA_OPTS=%NUITKA_OPTS% --flag`
            flags.extend(candidate.split(_CMD_APPEND, 1)[1].split())
    return flags


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


@pytest.mark.parametrize(("workflow", "step_name"), NUITKA_STEPS)
def test_nuitka_workflows_compile_the_plugin_package_without_removed_sdk_modules(
    workflow: str, step_name: str
) -> None:
    script = _nuitka_script(workflow, step_name)
    always = _nuitka_flags(script, unconditional_only=True)
    all_flags = _nuitka_flags(script, unconditional_only=False)
    assert "--include-package=plugin" in always, (workflow, step_name)
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert f"--include-package={removed_module}" not in all_flags, (workflow, removed_module)
        assert f"--include-module={removed_module}" not in all_flags, (workflow, removed_module)


def test_windows_nuitka_build_uses_project_root_icon() -> None:
    script = _nuitka_script("build-desktop.yml", "Build with Nuitka (Windows)")
    always = _nuitka_flags(script, unconditional_only=True)

    assert "--windows-icon-from-ico=assets/icon.ico" in always
    assert (ROOT / "assets" / "icon.ico").is_file()


@pytest.mark.parametrize(("workflow", "step_name"), NUITKA_STEPS)
def test_nuitka_workflows_compile_main_logic_with_the_omni_state_proxy(
    workflow: str, step_name: str
) -> None:
    always = _nuitka_flags(_nuitka_script(workflow, step_name), unconditional_only=True)
    assert "--include-package=main_logic" in always, (workflow, step_name)
    assert (ROOT / "main_logic" / "_module_state_proxy.py").is_file()


def test_removed_root_sdk_modules_are_not_importable() -> None:
    for removed_module in REMOVED_ROOT_SDK_MODULES:
        assert importlib.util.find_spec(removed_module) is None
