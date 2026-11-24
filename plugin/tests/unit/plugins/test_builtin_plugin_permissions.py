"""Built-in plugins keep a valid, fully translated [permissions] declaration.

Every plugin under plugin/plugins must declare the permissions its code uses
(``[permissions.require]`` / ``[permissions.ask]``), with user-facing texts in
all eight N.E.K.O locales, and the declarations must agree with the permission
scan the way ``neko-plugin check`` judges them: no errors, and only the
reviewed exceptions below ("possible use" call sites left undeclared, and
declared uses the scan cannot see yet).
"""
from __future__ import annotations

import subprocess
import tomllib
from collections import Counter
from pathlib import Path

import pytest

from plugin.core.capability_analysis import analyze_capabilities, compare_declarations
from plugin.core.permission_declarations import parse_permission_declarations

_REPO = Path(__file__).resolve().parents[4]
_PLUGINS = _REPO / "plugin" / "plugins"
_LOCALES = {"zh-CN", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}

_NET = "system.network:connect"
_LISTEN = "system.network:listen"
_SPAWN = "system.process:spawn"
_ENV = "system.env:read"
_FS_READ = "system.fs:read"
_FS_WRITE = "system.fs:write"
_PUSH = "plugin_sdk.main_agent.messages:push"

# Reviewed classification: permission -> (level, background).
_EXPECTED = {
    "app_launcher": {_SPAWN: ("require", False), _ENV: ("require", False)},
    "claude_companion": {
        _FS_READ: ("require", False),
        _PUSH: ("require", False),
        _ENV: ("require", False),
        _LISTEN: ("require", False),
    },
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

# Possible uses the scan reports that are deliberately not declared, pinned to
# their call sites (file, function, method), listed once per line the scan
# reports (it keeps one finding per file and line), so a new call on another
# line is not let through, not even in the same function:
# writes inside the plugin's data directory (claude_companion also moves a
# legacy inbox out of its own install directory once), and a DNS-only socket
# import.
_ALLOWED_POSSIBLE = {
    "claude_companion": {
        _FS_WRITE: [
            ("__init__.py", "InboxQueue._save", "os.replace"),
            ("__init__.py", "_migrate_legacy_inbox", "os.replace"),
            ("__init__.py", "_migrate_legacy_inbox", "os.replace"),
            ("__init__.py", "_open_private", "os.open"),
            ("__init__.py", "_sync_directory", "os.open"),
        ],
    },
    "music_pusher": {
        _FS_WRITE: [
            ("__init__.py", "MusicPusherPlugin._copy_static_ui_assets", "shutil.copy2"),
            ("__init__.py", "MusicPusherPlugin._migrate_legacy_uploads", "shutil.copy2"),
        ],
        _NET: [("__init__.py", "", "import")],
    },
    "netease_music": {
        _FS_WRITE: [
            ("credentials.py", "CredentialStore._save_sync", "pathlib.Path().mkdir"),
            ("credentials.py", "CredentialStore._atomic_write", "os.replace"),
            ("credentials.py", "CredentialStore._atomic_write", "pathlib.Path().unlink"),
        ],
    },
}

# Declared uses the scan cannot see yet: app_launcher reads environment
# variables through os.path.expandvars and its child processes inherit the
# environment, neither of which the scanner recognises as system.env:read.
_ALLOWED_UNUSED = {
    "app_launcher": {_ENV},
}

pytestmark = pytest.mark.plugin_unit


def _tracked_plugins() -> tuple[str, ...] | None:
    """Plugins git tracks under plugin/plugins; local, ignored plugins are not built in.

    ``None`` only when there is no git checkout or no git binary; a git error
    inside a checkout fails the test instead of skipping it.
    """
    if not (_REPO / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            # :(glob) keeps "*" inside one directory level, so a nested
            # fixtures/plugin.toml is not taken for a plugin.
            ["git", "ls-files", ":(glob)plugin/plugins/*/plugin.toml"],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    assert completed.returncode == 0, completed.stderr
    return tuple(sorted(Path(line).parent.name for line in completed.stdout.splitlines() if line))


# Parametrize over the reviewed list, so a local plugin in the checkout is not
# picked up; the first test ties the list to what git actually tracks.
_BUILTIN = tuple(sorted(_EXPECTED))


def _manifest(plugin_id: str) -> dict:
    return tomllib.loads((_PLUGINS / plugin_id / "plugin.toml").read_text(encoding="utf-8"))


def test_every_builtin_plugin_has_a_reviewed_classification() -> None:
    tracked = _tracked_plugins()
    if tracked is None:
        pytest.skip("not a git checkout: cannot list the tracked built-in plugins")
    assert set(tracked) == set(_EXPECTED)


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
    unused = {i.params["permission"] for i in issues if i.code == "declared_unused"}
    assert unused == _ALLOWED_UNUSED.get(plugin_id, set())
    allowed = _ALLOWED_POSSIBLE.get(plugin_id, {})
    possible = {i.params["permission"] for i in issues if i.code == "used_undeclared_possible"}
    assert possible == set(allowed)
    for permission, sites in allowed.items():
        found = Counter(
            (row["file"], row["function"], row["method"])
            for row in report["permissions"][permission]["findings"]
        )
        assert found == Counter(sites), permission
    assert {i.code for i in issues} <= {"used_undeclared_possible", "declared_unused"}
