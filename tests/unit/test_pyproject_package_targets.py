"""Hatch wheel/sdist targets must ship every first-party package the shipped code imports."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Author-side tooling that stays in the repository but is not shipped.
NOT_SHIPPED = {"theater_workshop"}
# Plugin test suites under plugin/tests import the repository test helpers.
IGNORED_IMPORTS = {"tests"}


def _targets() -> dict:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["hatch"]["build"]["targets"]


def _first_party_top_levels() -> set[str]:
    names = {path.stem for path in ROOT.glob("*.py")}
    for path in ROOT.iterdir():
        if path.is_dir() and not path.name.startswith(".") and any(path.glob("**/*.py")):
            names.add(path.name)
    return names


def _imported_top_levels(package: str, excludes: list[str]) -> dict[str, str]:
    imported: dict[str, str] = {}
    for source in sorted((ROOT / package).rglob("*.py")):
        relative = source.relative_to(ROOT).as_posix()
        if "__pycache__" in source.parts or any(
            relative == pattern or relative.startswith(pattern + "/") for pattern in excludes
        ):
            continue
        tree = ast.parse(source.read_bytes(), filename=relative)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            else:
                continue
            for module in modules:
                imported.setdefault(module.split(".", 1)[0], relative)
    return imported


def test_wheel_ships_every_first_party_package_imported_by_shipped_code() -> None:
    wheel = _targets()["wheel"]
    packages = set(wheel["packages"])
    first_party = _first_party_top_levels() - IGNORED_IMPORTS
    missing: dict[str, str] = {}
    for package in sorted(packages):
        for name, importer in _imported_top_levels(package, wheel["exclude"]).items():
            if name in first_party and name not in packages:
                missing.setdefault(name, importer)
    assert missing == {}, f"wheel packages miss first-party imports: {missing}"


def test_sdist_includes_wheel_packages_and_skips_author_tooling() -> None:
    targets = _targets()
    packages = set(targets["wheel"]["packages"])
    include = set(targets["sdist"]["include"])
    assert packages <= include
    assert "services" in packages
    assert not (packages | include) & NOT_SHIPPED
