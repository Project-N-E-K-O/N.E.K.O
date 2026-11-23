"""Built-in plugins keep a valid, fully translated [permissions] declaration.

Every plugin under plugin/plugins must declare the permissions its code uses
(``[permissions.require]`` / ``[permissions.ask]``), with user-facing texts in
all eight N.E.K.O locales, and the declarations must agree with the permission
scan the way ``neko-plugin check`` judges them: no errors.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from plugin.core.capability_analysis import analyze_capabilities, compare_declarations
from plugin.core.permission_declarations import parse_permission_declarations

_PLUGINS = Path(__file__).resolve().parents[3] / "plugins"
_BUILTIN = tuple(sorted(path.parent.name for path in _PLUGINS.glob("*/plugin.toml")))
_LOCALES = {"zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}

pytestmark = pytest.mark.plugin_unit


def _manifest(plugin_id: str) -> dict:
    return tomllib.loads((_PLUGINS / plugin_id / "plugin.toml").read_text(encoding="utf-8"))


def test_builtin_plugins_found() -> None:
    assert len(_BUILTIN) >= 11


@pytest.mark.parametrize("plugin_id", _BUILTIN)
def test_declarations_parse_without_issues(plugin_id: str) -> None:
    declarations, issues = parse_permission_declarations(_manifest(plugin_id))
    assert declarations.status == "declared"
    assert declarations.items
    assert [(issue.severity, issue.code, dict(issue.params)) for issue in issues] == []
    assert not declarations.invalid


@pytest.mark.parametrize("plugin_id", _BUILTIN)
def test_texts_cover_all_locales(plugin_id: str) -> None:
    declarations, _issues = parse_permission_declarations(_manifest(plugin_id))
    for permission, item in declarations.items.items():
        texts = [("reason", item.reason)]
        if item.background:
            texts.append(("background_reason", item.background_reason))
        for label, text in texts:
            assert isinstance(text, dict), f"{permission}.{label} must be a locale table"
            assert set(text) == _LOCALES, f"{permission}.{label} locales"


@pytest.mark.parametrize("plugin_id", _BUILTIN)
def test_declarations_match_permission_scan(plugin_id: str) -> None:
    report = analyze_capabilities(_PLUGINS / plugin_id)
    assert report["declarations"] == "declared"
    errors = [
        (issue.code, dict(issue.params))
        for issue in compare_declarations(report)
        if issue.severity == "error"
    ]
    assert errors == []
