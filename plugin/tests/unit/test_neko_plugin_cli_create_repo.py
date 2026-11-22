"""`neko-plugin create-repo`: create the plugin's GitHub repo with gh, or
fall back to manual steps when gh is unavailable."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.commands import init_cmd, release_cmd
from plugin.neko_plugin_cli.paths import CliDefaults


def _quoted(plugin_dir: Path) -> str:
    """The plugin directory as the printed git commands quote it."""
    return release_cmd._plugin_dir_argument(plugin_dir, "")

pytestmark = pytest.mark.plugin_unit

MANUAL = init_cmd.MANUAL_FALLBACK_EXIT


@pytest.mark.skipif(release_cmd._WINDOWS_SHELL, reason="POSIX shell quoting")
def test_git_command_quotes_the_directory_for_a_posix_shell() -> None:
    # Plain double quotes would still let the shell expand $HOME.
    assert init_cmd._git_command(Path("/work/my $HOME (copy)"), "init -b main") == (
        "git -C '/work/my $HOME (copy)' init -b main"
    )


def test_git_command_quotes_the_directory_for_windows_shells(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_cmd, "_WINDOWS_SHELL", True)
    assert init_cmd._git_command(Path(r"C:\work\my plugin"), "init -b main") == (
        'git -C "C:\\work\\my plugin" init -b main'
    )
    # No quoting keeps %VAR% literal in cmd, so the user fills in the path.
    assert init_cmd._git_command(Path(r"C:\work\%K%\demo"), "init -b main") == (
        'git -C "<plugin directory>" init -b main'
    )


@pytest.mark.skipif(release_cmd._WINDOWS_SHELL, reason="POSIX shell quoting")
def test_printed_branch_and_remote_stay_one_literal_argument_in_a_posix_shell(tmp_path, monkeypatch):
    # Branch names may hold $ ; ( &: pasting the hint must not run anything else.
    branch = "feat;$(touch pwned)&x"
    argument = init_cmd._branch_argument(branch)
    assert argument == "'feat;$(touch pwned)&x'"
    echoed = subprocess.run(["sh", "-c", f"printf %s {argument}"], cwd=tmp_path, capture_output=True, text=True, check=True)
    assert echoed.stdout == branch
    assert not (tmp_path / "pwned").exists()

    # shlex.quote leaves "=git" bare, and zsh expands a leading = to a path.
    assert init_cmd._branch_argument("=git") == "'=git'"

    remote = "https://user:t$ken@github.com/octocat/demo.git"
    message = init_cmd._existing_repository_remote_message(Path("/work/demo"), remote)
    assert message.endswith("git -C /work/demo remote add origin 'https://user:t$ken@github.com/octocat/demo.git'")


def test_push_refspec_spells_out_a_branch_starting_with_plus():
    # A leading + in a refspec means "force"; no quoting hides it from git.
    assert release_cmd._push_refspec("main") == "main"
    assert release_cmd._push_refspec("+main") == "refs/heads/+main"
    assert release_cmd._push_refspec("+feat", "main") == "refs/heads/+feat:main"
    assert release_cmd._push_refspec("feat", "+x") == "feat:refs/heads/+x"


@pytest.mark.skipif(release_cmd._WINDOWS_SHELL, reason="runs the printed command in sh")
def test_printed_push_of_a_plus_branch_pushes_that_branch_without_force(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    remote = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", str(remote))
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    _commit_all(plugin_dir)
    _git(plugin_dir, "remote", "add", "origin", str(remote))
    _git(plugin_dir, "push", "-q", "origin", "main")
    pushed_main = _git(plugin_dir, "rev-parse", "main")
    # Local main moves on: a forced "+main" push would overwrite origin/main.
    (plugin_dir / "local.txt").write_text("local\n", encoding="utf-8")
    _git(plugin_dir, "add", "local.txt")
    _git(plugin_dir, "commit", "-q", "--amend", "-m", "rewritten")
    _git(plugin_dir, "checkout", "-q", "-b", "+main")

    command, hidden = init_cmd._push_step(plugin_dir)
    assert hidden is None
    assert command.endswith(" push -u origin refs/heads/+main")
    subprocess.run(["sh", "-c", command], cwd=tmp_path, check=True, capture_output=True)

    assert _git(remote, "rev-parse", "refs/heads/main") == pushed_main
    assert _git(remote, "rev-parse", "refs/heads/+main") == _git(plugin_dir, "rev-parse", "HEAD")


def test_printed_branch_and_remote_for_windows_shells(monkeypatch):
    monkeypatch.setattr(release_cmd, "_WINDOWS_SHELL", True)
    for branch, expected in [
        ("main", "main"),
        ("feature/x-1.2", "feature/x-1.2"),
        ("a&b", '"a&b"'),
        # PowerShell would pass an array, or splat.
        ("feat,x", '"feat,x"'),
        ("@feat", '"@feat"'),
        # No quoting keeps %VAR% or !VAR! literal in cmd, and a " would end it.
        ("a%PATH%b", '"<branch>"'),
        ("a!b!", '"<branch>"'),
        ('a"&calc&"', '"<branch>"'),
    ]:
        assert init_cmd._branch_argument(branch) == expected
    url = "https://github.com/octocat/demo.git"
    assert init_cmd._existing_repository_remote_message(Path("C:/work"), url).endswith(f"remote add origin {url}")
    # Quoted, the trailing backslash would escape the closing quote: "C:\repo\" reaches git as C:\repo".
    local_remote = "C:\\repo\\"
    assert init_cmd._existing_repository_remote_message(Path("C:/work"), local_remote).endswith(
        f'remote add origin "<repository-url>" (repository URL: {local_remote})'
    )
    assert init_cmd._existing_repository_remote_message(Path("C:/work"), "C:\\repo").endswith(
        'remote add origin "C:\\repo"'
    )
    # cmd ends the command at a line break inside the quotes.
    assert init_cmd._existing_repository_remote_message(Path("C:/work"), "C:\\repo\nx").endswith(
        'remote add origin "<repository-url>" (repository URL: C:\\repo\nx)'
    )
    # The same rule covers the plugin directory (a drive root keeps its backslash).
    assert release_cmd._plugin_dir_argument(Path("D:\\"), "") == '"<plugin directory>"'
    token_url = "https://user:p%40ss@github.com/octocat/demo.git"
    assert init_cmd._existing_repository_remote_message(Path("C:/work"), token_url).endswith(
        f'remote add origin "<repository-url>" (repository URL: {token_url})'
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout.strip()


def _commit_all(repo: Path, *, branch: str = "main") -> None:
    _git(repo, "init", "-b", branch)
    _git(repo, "config", "user.name", "Repo Test")
    _git(repo, "config", "user.email", "repo@example.com")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "initial")


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
    # The manual path names the expected repository and git commands that
    # work on the plugin's own repository.
    assert "n.e.k.o_plugin_demo_repo" in out
    assert f'git -C {_quoted(plugin_dir)} init -b main' in out
    assert f'git -C {_quoted(plugin_dir)} remote add origin "<repository-url>"' in out
    assert f'git -C {_quoted(plugin_dir)} push -u origin main' in out
    assert "setup-repo" not in out


def test_manual_steps_name_the_directory_behind_a_placeholder(tmp_path, monkeypatch, capsys):
    # No Windows quoting keeps %VAR% literal, so the commands show a placeholder.
    monkeypatch.setattr(release_cmd, "_WINDOWS_SHELL", True)
    plugin_dir = _make_plugin_dir(tmp_path / "%USERPROFILE%" / "plugins")
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: False)
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _dir: None)

    assert init_cmd.handle_create_repo(_args(plugin_dir)) == MANUAL
    out = capsys.readouterr().out
    assert 'git -C "<plugin directory>" remote add origin "<repository-url>"' in out
    assert out.count(f"(plugin directory: {plugin_dir})") == 1


def test_push_steps_name_the_branch_behind_a_placeholder(tmp_path, monkeypatch, capsys):
    # No Windows quoting keeps $ literal in PowerShell, so the commands show a placeholder.
    monkeypatch.setattr(release_cmd, "_WINDOWS_SHELL", True)
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(init_cmd, "_current_branch", lambda _dir: "feature/$name")

    init_cmd._print_manual_repo_steps(plugin_dir, repo_name="demo")
    init_cmd._print_push_next_step(plugin_dir)
    out = capsys.readouterr().out
    assert out.count('push -u origin "<branch>"') == 2
    assert out.count("(branch: feature/$name)") == 2

    monkeypatch.setattr(init_cmd, "_current_branch", lambda _dir: "main")
    init_cmd._print_manual_repo_steps(plugin_dir, repo_name="demo")
    out = capsys.readouterr().out
    assert "push -u origin main" in out
    assert "branch:" not in out


def test_manual_steps_do_not_add_a_directory_note_to_quoted_commands(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: False)
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _dir: None)

    assert init_cmd.handle_create_repo(_args(plugin_dir)) == MANUAL
    assert "plugin directory:" not in capsys.readouterr().out


def test_manual_steps_for_a_committed_repo_only_wire_and_push(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    _commit_all(plugin_dir, branch="trunk")
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: False)

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == MANUAL
    out = capsys.readouterr().out
    assert "init -b main" not in out
    assert "commit -m" not in out
    assert f'git -C {_quoted(plugin_dir)} remote add origin "<repository-url>"' in out
    assert f'git -C {_quoted(plugin_dir)} push -u origin trunk' in out


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


def test_explicit_owner_must_match_an_existing_origin(tmp_path, monkeypatch):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    monkeypatch.setattr(init_cmd, "_existing_origin_url", lambda _: "https://github.com/other/n.e.k.o_plugin_demo_repo.git")
    assert init_cmd.handle_create_repo(_args(plugin_dir, owner="requested")) == 1


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
    # create-repo does not push; it names the push publish needs next.
    assert f'git -C {_quoted(plugin_dir)} push -u origin main' in out


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
    monkeypatch.setattr(init_cmd, "_gh_repository_view", lambda slug: None)

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 1
    assert "already exists" in capsys.readouterr().err


def _taken_name(monkeypatch, view):
    monkeypatch.setattr(init_cmd, "_gh_available", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_authenticated", lambda: True)
    monkeypatch.setattr(init_cmd, "_gh_default_owner", lambda: "octocat")

    def _raise(*a, **k):
        raise init_cmd._RepositoryNameTaken("octocat/n.e.k.o_plugin_demo_repo")

    monkeypatch.setattr(init_cmd, "_gh_create_repository", _raise)
    views = []
    monkeypatch.setattr(init_cmd, "_gh_repository_view", lambda slug: views.append(slug) or view)
    return views


def test_a_taken_name_that_is_the_callers_empty_repo_is_wired_as_origin(tmp_path, monkeypatch, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    _commit_all(plugin_dir)
    url = "https://github.com/octocat/n.e.k.o_plugin_demo_repo"
    views = _taken_name(
        monkeypatch, {"isEmpty": True, "viewerPermission": "ADMIN", "url": url}
    )

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 0
    assert views == ["octocat/n.e.k.o_plugin_demo_repo"]
    assert _git(plugin_dir, "remote", "get-url", "origin") == url
    out = capsys.readouterr().out
    assert "reusing your existing empty repository" in out
    assert f'git -C {_quoted(plugin_dir)} push -u origin main' in out


@pytest.mark.parametrize(
    "view",
    [
        {"isEmpty": False, "viewerPermission": "ADMIN", "url": "https://github.com/o/r"},
        {"isEmpty": True, "viewerPermission": "READ", "url": "https://github.com/o/r"},
        None,
    ],
)
def test_a_taken_name_that_is_not_an_empty_repo_you_own_is_refused(tmp_path, monkeypatch, capsys, view):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    _commit_all(plugin_dir)
    _taken_name(monkeypatch, view)

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 1
    assert _git(plugin_dir, "remote") == ""
    assert "already exists" in capsys.readouterr().err


def test_no_next_step_once_the_branch_tracks_origin(tmp_path, capsys):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    _commit_all(plugin_dir)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    _git(plugin_dir, "remote", "add", "origin", str(remote))
    _git(plugin_dir, "push", "-u", "origin", "main")
    _git(plugin_dir, "remote", "set-url", "origin", "https://github.com/octocat/n.e.k.o_plugin_demo_repo.git")

    exit_code = init_cmd.handle_create_repo(_args(plugin_dir))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "already configured" in out
    assert "push -u" not in out


def test_create_repo_ignores_a_parent_repositorys_origin(tmp_path):
    # A plugin inside the N.E.K.O checkout without its own .git must not
    # read or reuse N.E.K.O's origin.
    outer = tmp_path / "neko"
    outer.mkdir()
    _git(outer, "init", "-b", "main")
    _git(outer, "remote", "add", "origin", "https://github.com/Project-N-E-K-O/N.E.K.O.git")
    plugin_dir = _make_plugin_dir(outer / "plugin" / "plugins")

    assert init_cmd._existing_origin_url(plugin_dir) is None
    assert init_cmd._initialize_git_repo(plugin_dir) is True
    assert (plugin_dir / ".git").is_dir()
    assert _git(plugin_dir, "symbolic-ref", "--short", "HEAD") == "main"
    assert _git(plugin_dir, "remote") == ""


def test_setup_repo_remote_on_an_existing_repo_names_the_working_command(tmp_path):
    plugin_dir = _make_plugin_dir(tmp_path / "plugins")
    _git(plugin_dir, "init", "-b", "main")
    url = "https://github.com/octocat/n.e.k.o_plugin_demo_repo.git"

    with pytest.raises(RuntimeError) as exc_info:
        init_cmd._preflight_git_request(plugin_dir, initialize_git=True, remote=url)

    assert f'git -C {_quoted(plugin_dir)} remote add origin {url}' in str(exc_info.value)
