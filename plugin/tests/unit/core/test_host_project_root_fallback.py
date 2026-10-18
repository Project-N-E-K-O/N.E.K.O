"""Cover _find_project_root fallback for user plugins installed outside the repository.

User plugins install under `Documents/{APP_NAME}/plugins/<id>/`, where upward discovery finds no repository root.
The old fallback counted four parents from built-in layout plugin/plugins/<id>/plugin.toml, reaching Documents
for user plugins; that result then became the plugin subprocess's sys.path[0].
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core import host as host_module

_HOST_REPO_ROOT = Path(host_module.__file__).resolve().parents[2]

# Upward discovery is limited to 10 steps; nesting deeper cannot reach any actual repository root,
# ensuring fallback regardless of tmp_path placement in the system temporary directory or an in-repository basetemp.
# This always exercises the fallback branch.
_DEEPER_THAN_WALK_LIMIT = tuple(f"lvl{index}" for index in range(12))


def _make_config(root: Path, *parts: str) -> Path:
    config_path = root.joinpath(*parts)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\n", encoding="utf-8")
    return config_path


def test_builtin_layout_resolves_by_walking_up(tmp_path: Path) -> None:
    """Upward discovery still finds in-repository layouts, unaffected by fallback changes."""
    fake_repo = tmp_path / "checkout"
    (fake_repo / "utils").mkdir(parents=True)
    config_path = _make_config(fake_repo, "plugin", "plugins", "demo", "plugin.toml")

    assert host_module._find_project_root(config_path) == fake_repo.resolve()


def test_fallback_returns_host_repo_root_not_the_user_directory(tmp_path: Path) -> None:
    """Fallback must return the host's own repository root, not four parents above the plugin directory."""
    documents = tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "Documents")
    config_path = _make_config(documents, "N.E.K.O", "plugins", "demo", "plugin.toml")

    resolved = host_module._find_project_root(config_path)

    assert resolved == _HOST_REPO_ROOT
    assert resolved != documents.resolve()


def test_resolved_root_always_looks_like_a_neko_root(tmp_path: Path) -> None:
    """The result enters subprocess sys.path[0] and must never be an ordinary user directory."""
    config_path = _make_config(
        tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "Documents"),
        "N.E.K.O", "plugins", "demo", "plugin.toml",
    )

    resolved = host_module._find_project_root(config_path)

    # Assert only plugin/. Packaged builds compile utils/ into the executable, leaving no disk directory;
    # using it as a criterion would hardcode a source-tree-only assumption into the test.
    assert (resolved / "plugin").is_dir()


def test_fallback_never_returns_the_plugin_directory(tmp_path: Path) -> None:
    """The plugin's own directory must never become subprocess sys.path[0]."""
    config_path = _make_config(
        tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "Documents"),
        "N.E.K.O", "plugins", "demo", "plugin.toml",
    )

    resolved = host_module._find_project_root(config_path)

    assert resolved != config_path.parent.resolve()
    assert config_path.parent.resolve() not in resolved.parents


def test_packaged_layout_without_utils_dir_still_resolves_to_the_app_root(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """Packaged builds compile utils/ into the executable, leaving no directory; the criterion must still work.

    Source trees always contain utils/, so tests using only real repositories cannot distinguish this criterion.
    Build a distribution-shaped tree containing plugin/ but no utils/, pointing the host module's __file__
    into it.
    """
    app_root = tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "dist", "Xiao8")
    (app_root / "plugin" / "plugins").mkdir(parents=True)
    assert not (app_root / "utils").exists()
    monkeypatch.setattr(host_module, "__file__", str(app_root / "plugin" / "core" / "host.py"))

    config_path = _make_config(
        tmp_path / "userdata" / "Documents",
        *_DEEPER_THAN_WALK_LIMIT, "N.E.K.O", "plugins", "demo", "plugin.toml",
    )

    resolved = host_module._find_project_root(config_path)

    assert resolved == app_root.resolve()
    assert resolved != config_path.parent.resolve()


def test_packaged_layout_keeps_builtin_plugins_off_their_own_directory(
    tmp_path: Path, monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """Packaged built-in plugins must not treat their own directory as the project root either."""
    # Nest deeper than the upward-search limit, or a local tmp_path inside the repository may reach
    # the real root and never execute fallback.
    app_root = tmp_path.joinpath(*_DEEPER_THAN_WALK_LIMIT, "dist", "Xiao8")
    (app_root / "plugin" / "plugins").mkdir(parents=True)
    monkeypatch.setattr(host_module, "__file__", str(app_root / "plugin" / "core" / "host.py"))
    config_path = _make_config(app_root, "plugin", "plugins", "demo", "plugin.toml")

    resolved = host_module._find_project_root(config_path)

    assert resolved == app_root.resolve()
