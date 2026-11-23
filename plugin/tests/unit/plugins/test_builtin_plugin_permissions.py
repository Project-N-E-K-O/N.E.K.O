"""Built-in plugins keep a valid, fully translated [permissions] declaration.

Every plugin under plugin/plugins must declare the permissions its code uses
(``[permissions.require]`` / ``[permissions.ask]``), with user-facing texts in
all eight N.E.K.O locales, and the declarations must agree with the permission
scan the way ``neko-plugin check`` judges them: no errors, no declaration
left without a use, and only the reviewed "possible use" warnings below.
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

_NET = "system.network:connect"
_SPAWN = "system.process:spawn"
_ENV = "system.env:read"
_FS_READ = "system.fs:read"
_FS_WRITE = "system.fs:write"
_PUSH = "plugin_sdk.main_agent.messages:push"

# Reviewed classification: permission -> (level, background).
_EXPECTED = {
    "app_launcher": {_SPAWN: ("require", False)},
    "claude_companion": {_FS_READ: ("require", False), _PUSH: ("require", False), _ENV: ("require", False)},
    "game_agent_minecraft": {_NET: ("require", False), _ENV: ("require", False), _PUSH: ("ask", True)},
    "jukebox_controller": {_PUSH: ("require", False)},
    "lifekit": {_NET: ("require", False), _ENV: ("require", False), _FS_READ: ("require", False), _PUSH: ("ask", False)},
    "mcp_adapter": {_NET: ("require", False), _SPAWN: ("require", False), _ENV: ("require", False)},
    "memo_reminder": {_PUSH: ("require", False)},
    "music_pusher": {_PUSH: ("require", False), _ENV: ("require", False)},
    "netease_music": {_NET: ("require", False), _PUSH: ("require", False)},
    "proactive_controller": {_NET: ("require", False)},
    "web_search": {_NET: ("require", False)},
}

# Possible uses the scan reports that are deliberately not declared: files in
# the plugin's own directory or data directory, and a DNS lookup (no connection).
_ALLOWED_POSSIBLE = {
    "claude_companion": {_FS_WRITE},
    "music_pusher": {_FS_WRITE, _NET},
    "netease_music": {_FS_WRITE},
}

pytestmark = pytest.mark.plugin_unit


def _manifest(plugin_id: str) -> dict:
    return tomllib.loads((_PLUGINS / plugin_id / "plugin.toml").read_text(encoding="utf-8"))


def test_every_builtin_plugin_has_a_reviewed_classification() -> None:
    assert set(_BUILTIN) == set(_EXPECTED)


@pytest.mark.parametrize("plugin_id", _BUILTIN)
def test_declared_levels_match_classification(plugin_id: str) -> None:
    declarations, _issues = parse_permission_declarations(_manifest(plugin_id))
    actual = {
        permission: (item.level, item.background) for permission, item in declarations.items.items()
    }
    assert actual == _EXPECTED[plugin_id]


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
    issues = compare_declarations(report)
    assert [(i.code, dict(i.params)) for i in issues if i.severity == "error"] == []
    assert [i.params["permission"] for i in issues if i.code == "declared_unused"] == []
    possible = {i.params["permission"] for i in issues if i.code == "used_undeclared_possible"}
    assert possible == _ALLOWED_POSSIBLE.get(plugin_id, set())
    assert {i.code for i in issues} <= {"used_undeclared_possible"}
