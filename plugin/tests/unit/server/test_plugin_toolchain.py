"""The publish page's toolchain read: local only, and never a token."""

from __future__ import annotations

import subprocess

from plugin.server.application import plugin_toolchain as toolchain


def _fake_tools(monkeypatch, installed: set[str], outputs: dict[tuple[str, ...], str | None]):
    monkeypatch.setattr(toolchain.shutil, "which", lambda name: f"/bin/{name}" if name in installed else None)
    calls: list[tuple[str, ...]] = []

    def run(argv, **_kwargs):
        calls.append(tuple(argv))
        out = outputs.get(tuple(argv))
        return subprocess.CompletedProcess(argv, 0 if out is not None else 1, out or "", "")

    monkeypatch.setattr(toolchain.subprocess, "run", run)
    return calls


def test_reports_installed_tools_their_versions_and_the_github_login(monkeypatch):
    calls = _fake_tools(
        monkeypatch,
        {"git", "uv", "uvx", "gh"},
        {
            ("git", "--version"): "git version 2.55.0.windows.3",
            ("git", "config", "--global", "user.name"): "Dev",
            ("git", "config", "--global", "user.email"): "dev@example.com",
            ("uv", "--version"): "uv 0.12.18 (01cb90c1a 2026-09-22)",
            ("gh", "--version"): "gh version 2.101.0 (2026-09-15)",
            ("gh", "auth", "token", "--hostname", "github.com"): "gho_secret",
            ("gh", "config", "get", "--host", "github.com", "user"): "octocat",
        },
    )
    result = toolchain.read_toolchain()
    assert result["git"] == {
        "installed": True,
        "version": "2.55.0",
        "user_name": "Dev",
        "user_email": "dev@example.com",
    }
    assert result["uv"] == {"installed": True, "version": "0.12.18"}
    assert result["gh"] == {
        "installed": True,
        "version": "2.101.0",
        "authenticated": True,
        "login": "octocat",
    }
    # The token proves a sign-in; it never leaves this function.
    assert "gho_secret" not in repr(result)
    # Nothing that would ask GitHub over the network.
    assert not any(call[:2] == ("gh", "api") for call in calls)


def test_missing_tools_are_not_run(monkeypatch):
    calls = _fake_tools(monkeypatch, set(), {})
    result = toolchain.read_toolchain()
    assert result["git"]["installed"] is False
    assert result["uv"] == {"installed": False, "version": None}
    assert result["gh"] == {"installed": False, "version": None, "authenticated": False, "login": None}
    assert calls == []


def test_uv_without_uvx_counts_as_missing(monkeypatch):
    _fake_tools(monkeypatch, {"uv"}, {("uv", "--version"): "uv 0.12.18"})
    assert toolchain.read_toolchain()["uv"]["installed"] is False


def test_gh_without_a_stored_token_is_signed_out(monkeypatch):
    _fake_tools(
        monkeypatch,
        {"gh"},
        {
            ("gh", "--version"): "gh version 2.101.0",
            ("gh", "config", "get", "--host", "github.com", "user"): "stale",
        },
    )
    gh = toolchain.read_toolchain()["gh"]
    assert gh["authenticated"] is False
    # A login left in gh's config after a logout is not a signed-in account.
    assert gh["login"] is None


def test_unset_git_identity_is_reported_as_missing(monkeypatch):
    _fake_tools(monkeypatch, {"git"}, {("git", "--version"): "git version 2.40.1"})
    git = toolchain.read_toolchain()["git"]
    assert git["installed"] is True
    assert git["user_name"] is None and git["user_email"] is None


def test_the_git_identity_is_read_as_a_commit_in_the_project_folder_sees_it(monkeypatch, tmp_path):
    # A repository-level identity counts: that is what a commit there uses.
    _fake_tools(
        monkeypatch,
        {"git"},
        {
            ("git", "--version"): "git version 2.55.0",
            ("git", "-C", str(tmp_path), "config", "user.name"): "Local",
            ("git", "-C", str(tmp_path), "config", "user.email"): "local@example.com",
        },
    )
    git = toolchain.read_toolchain(tmp_path)["git"]
    assert (git["user_name"], git["user_email"]) == ("Local", "local@example.com")


def test_project_toolchain_of_an_unknown_project_is_not_found(monkeypatch):
    from plugin.server.domain.errors import ServerDomainError

    monkeypatch.setattr(toolchain.store, "list_records", lambda: [])
    try:
        toolchain.read_project_toolchain("missing")
    except ServerDomainError as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("an unknown project must be refused")
