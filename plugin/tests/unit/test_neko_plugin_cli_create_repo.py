"""`neko-plugin create-repo`: create the plugin's GitHub repo with gh, or
fall back to manual steps when gh is unavailable."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.commands import init_cmd
from plugin.neko_plugin_cli.paths import CliDefaults

pytestmark = pytest.mark.plugin_unit

MANUAL = init_cmd.MANUAL_FALLBACK_EXIT


def _make_plugin_dir(root: Path, plugin_id: str = "demo_repo") -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                f'id = "{plugin_id}"',
                'name = "Demo"',
                'version = "0.0.1"',
                'type = "plugin"',
                f'entry = "plugin.plugins.{plugin_id}:DemoPlugin"',
                "",
                "[plugin.sdk]",
                'recommended = ">=0.1.0,<0.2.0"',
                'supported = ">=0.1.0,<0.3.0"',
                "",
                "[plugin_runtime]",
                "auto_start = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return plugin_dir


def _args(plugin_dir: Path, *, owner: str | None = None, private: bool = False) -> argparse.Namespace:
    defaults = CliDefaults(
        plugin_root=plugin_dir.parent.parent,
        target_dir=plugin_dir.parent.parent / "target",
        plugins_root=plugin_dir.parent,
        profiles_root=plugin_dir.parent.parent / ".profiles",
    )
    return argparse.Namespace(
        plugin=str(plugin_dir),
        plugins_root=None,
        owner=owner,
        private=private,
        _defaults=defaults,
    )


def test_manual_steps_when_gh_is_unavailable(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: False)
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _dir: None)

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == MANUAL
    out = capsys.readouterr().out
    assert "[MANUAL]" in out
    # The manual path names the expected repository and the wiring command.
    assert "n.e.k.o_plugin_demo_repo" in out
    assert "neko-plugin setup-repo" in out
    assert "--remote" in out


def test_manual_steps_when_gh_is_not_signed_in(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_authenticated", lambda: False)
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _dir: None)

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == MANUAL
    assert "[MANUAL]" in capsys.readouterr().out


def test_already_configured_origin_is_accepted_without_creating(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(
        init_cmd,
        "_existing_origin_url",
        lambda _dir: "https://github.com/someone/n.e.k.o_plugin_demo_repo.git",
    )
    created = []
    monkeypatch.setattr(init_cmd, "_gh_create_repository", lambda *a, **k: created.append(a))

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 0
    assert created == []  # nothing is created when origin already matches
    assert "already configured" in capsys.readouterr().out


def test_a_mismatched_origin_is_refused(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(
        init_cmd,
        "_existing_origin_url",
        lambda _dir: "https://github.com/someone/unrelated-repo.git",
    )
    created = []
    monkeypatch.setattr(init_cmd, "_gh_create_repository", lambda *a, **k: created.append(a))

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 1
    assert created == []  # a foreign origin is never overwritten
    assert "[FAIL]" in capsys.readouterr().err


def test_happy_path_creates_the_repository_and_wires_origin(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    # Before creation there is no origin; after gh runs, origin is wired.
    origins = iter([None, "https://github.com/octocat/n.e.k.o_plugin_demo_repo.git"])
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _dir: next(origins))
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_authenticated", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_default_owner", lambda: "octocat")
    monkeypatch.setattr(init_cmd, "_initialize_git_repo", lambda _dir, **k: True)
    calls = []
    monkeypatch.setattr(
        init_cmd,
        "_gh_create_repository",
        lambda plugin_dir, *, slug, private: calls.append((slug, private)),
    )

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 0
    assert calls == [("octocat/n.e.k.o_plugin_demo_repo", False)]
    out = capsys.readouterr().out
    assert "octocat/n.e.k.o_plugin_demo_repo" in out
    assert "[OK]" in out


def test_explicit_owner_and_private_are_passed_to_gh(tmp_path, monkeypatch):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    origins = iter([None, "https://github.com/acme/n.e.k.o_plugin_demo_repo.git"])
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _dir: next(origins))
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_authenticated", lambda: True)
    monkeypatch.setattr(init_cmd, "_initialize_git_repo", lambda _dir, **k: True)
    calls = []
    monkeypatch.setattr(
        init_cmd,
        "_gh_create_repository",
        lambda plugin_dir, *, slug, private: calls.append((slug, private)),
    )

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir, owner="acme", private=True))

    assert exit_code == 0
    assert calls == [("acme/n.e.k.o_plugin_demo_repo", True)]


def test_a_taken_repository_name_is_reported(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _dir: None)
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_authenticated", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_default_owner", lambda: "octocat")
    monkeypatch.setattr(init_cmd, "_initialize_git_repo", lambda _dir, **k: True)

    def _raise(*a, **k):
        raise init_cmd._RepositoryNameTaken("octocat/n.e.k.o_plugin_demo_repo")

    monkeypatch.setattr(init_cmd, "_gh_create_repository", _raise)

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 1
    assert "already exists" in capsys.readouterr().err
