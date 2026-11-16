"""The application ships no browser of its own.

The browser tools drive a browser the user already has installed. So no build
may download or bundle a Chromium, the launcher must not look for one at start,
and the runtime dependencies carry no browser-automation package. What every
build must carry instead is the whole ``nekoclaw`` package, which holds the
browser tools and the keyboard-and-mouse tool.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest
import yaml
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
BUILD_WORKFLOWS = ("build-desktop.yml", "build-desktop-linux.yml")
NUITKA_STEPS = (
    ("build-desktop.yml", "Build with Nuitka (Unix)"),
    ("build-desktop.yml", "Build with Nuitka (Windows)"),
    ("build-desktop-linux.yml", "Build with Nuitka"),
)

# What a build that fetches or packs a browser has to say somewhere.
BROWSER_TRACES = (
    "playwright_browsers",
    "PLAYWRIGHT_BROWSERS_PATH",
    "playwright install",
    "browser_use",
)
# Packages that held the previous browser and desktop automation.
REMOVED_PACKAGES = ("brain", "browser_use", "browser_use_sdk", "playwright")
# Third-party modules the built-in tools import inside functions.
BUILTIN_TOOL_IMPORTS = frozenset({"websockets", "psutil", "pyautogui", "pyperclip", "PIL"})

_BASH_APPEND = 'NUITKA_OPTS="$NUITKA_OPTS '
_CMD_APPEND = "set NUITKA_OPTS=%NUITKA_OPTS% "


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


def _requirement_name(requirement: str) -> str:
    match = re.match("[A-Za-z0-9._-]+", requirement.strip())
    assert match is not None, requirement
    return match.group(0).lower()


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _module_names(package: str) -> list[str]:
    names: set[str] = set()
    for source in (ROOT / package).rglob("*.py"):
        parts = source.relative_to(ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        names.add(".".join(parts))
    return sorted(names)


def _ensure_project_root_first() -> None:
    """Keep PyInstaller's isolated scanner on this checkout's package tree."""
    root = str(ROOT)
    while root in sys.path:
        sys.path.remove(root)
    sys.path.insert(0, root)


@pytest.mark.parametrize("workflow", BUILD_WORKFLOWS)
def test_desktop_build_neither_downloads_nor_bundles_a_browser(workflow: str) -> None:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    found = [trace for trace in BROWSER_TRACES if trace in text]
    assert found == [], f"{workflow} still refers to a bundled browser: {found}"


@pytest.mark.parametrize(("workflow", "step_name"), NUITKA_STEPS)
def test_nuitka_build_names_no_removed_package(workflow: str, step_name: str) -> None:
    # Nuitka stops with a fatal error when asked to include a package that does
    # not exist, so one leftover option fails the whole build.
    flags = _nuitka_flags(_nuitka_script(workflow, step_name), unconditional_only=False)
    assert flags, "no NUITKA_OPTS lines were recognised"
    leftovers = [
        flag
        for flag in flags
        for package in REMOVED_PACKAGES
        if flag in (f"--include-package={package}", f"--include-package-data={package}")
    ]
    assert leftovers == []


@pytest.mark.parametrize(("workflow", "step_name"), NUITKA_STEPS)
def test_nuitka_build_compiles_the_package_of_the_builtin_tools(workflow: str, step_name: str) -> None:
    script = _nuitka_script(workflow, step_name)
    always = _nuitka_flags(script, unconditional_only=True)
    # nekoclaw.runtime resolves its public names through importlib, and the
    # built-in tools are only reached from there, so following imports from the
    # entry script is not enough: the package has to be named.
    assert "--include-package=nekoclaw" in always
    assert "--include-package=websockets" in always

    excluded = {
        flag.split("=", 1)[1]
        for flag in _nuitka_flags(script, unconditional_only=False)
        if flag.startswith("--nofollow-import-to=")
    }
    assert excluded & BUILTIN_TOOL_IMPORTS == set()


@pytest.mark.parametrize(("workflow", "step_name"), NUITKA_STEPS)
def test_nuitka_build_keeps_the_test_only_playwright_out_of_the_bundle(workflow: str, step_name: str) -> None:
    # The build environment installs the dev group, so Playwright is importable
    # there, and plugin/tests/e2e (inside the compiled `plugin` package) imports it.
    always = _nuitka_flags(_nuitka_script(workflow, step_name), unconditional_only=True)
    assert "--nofollow-import-to=playwright" in always


def test_launcher_does_not_look_for_a_browser_at_start() -> None:
    source = (ROOT / "launcher_core" / "runtime.py").read_text(encoding="utf-8")
    assert "playwright" not in source.lower()


def test_runtime_dependencies_carry_no_browser_automation_package() -> None:
    project = _pyproject()
    runtime = {_requirement_name(item) for item in project["project"]["dependencies"]}
    assert runtime & {"browser-use", "browser-use-sdk", "playwright"} == set()

    # The frontend tests still drive a Chromium through Playwright.
    dev = {_requirement_name(item) for item in project["dependency-groups"]["dev"]}
    assert "playwright" in dev

    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "browser-use"' not in lock
    assert 'name = "playwright"' in lock


def test_html_converter_of_the_web_tools_is_declared_not_inherited() -> None:
    # nekoclaw/runtime/tools/web.py falls back to plain text without it and the
    # MCP adapter plugin imports it at module load; no other dependency brings it.
    runtime = {_requirement_name(item) for item in _pyproject()["project"]["dependencies"]}
    assert "markdownify" in runtime


def test_pyinstaller_spec_names_no_removed_package_and_collects_nekoclaw() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")
    assert "collect_submodules('nekoclaw', on_error='raise')" in spec
    for removed in ("'brain'", "'brain.computer_use'", "'browser_use'", "'playwright'", "browser_use_prompts"):
        assert removed not in spec


def test_pyinstaller_finds_every_nekoclaw_module() -> None:
    _ensure_project_root_first()
    collected = sorted(collect_submodules("nekoclaw", on_error="raise"))
    assert collected == _module_names("nekoclaw")
    assert "nekoclaw.builtin.browser.cdp" in collected
    assert "nekoclaw.builtin.desktop" in collected
