"""The plugin.id / directory-name semantic check accepts a kebab-case directory.

A plugin cloned from a kebab-case repository (``kebab-probe/``) declares its id in
snake_case (``kebab_probe``) because the id doubles as a Python package name. The
check used to warn on that legitimate pair; it must only warn on a real mismatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin.config.plugin_toml_semantics import collect_plugin_toml_semantic_warnings

MISMATCH = "PLUGIN_DIRECTORY_ID_MISMATCH"


def _codes(conf: dict, directory: str, tmp_path: Path) -> set[str]:
    toml_path = tmp_path / directory / "plugin.toml"
    toml_path.parent.mkdir(parents=True, exist_ok=True)
    return {warning["code"] for warning in collect_plugin_toml_semantic_warnings(conf, toml_path=toml_path)}


@pytest.mark.parametrize("directory", ["kebab_probe", "kebab-probe"])
def test_a_kebab_directory_matches_its_snake_case_id(directory: str, tmp_path: Path) -> None:
    conf = {"plugin": {"id": "kebab_probe", "entry": "plugin.plugins.kebab_probe:KebabPlugin"}}

    assert MISMATCH not in _codes(conf, directory, tmp_path)


def test_an_unrelated_directory_still_warns(tmp_path: Path) -> None:
    conf = {"plugin": {"id": "kebab_probe", "entry": "plugin.plugins.kebab_probe:KebabPlugin"}}

    assert MISMATCH in _codes(conf, "somewhere_else", tmp_path)
