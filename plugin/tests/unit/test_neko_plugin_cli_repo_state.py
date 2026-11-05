"""Local git facts of a plugin source checkout."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from plugin.neko_plugin_cli.repo_state import read_repo_state

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _repository(path: Path) -> Path:
    _git(path, "init")
    (path / "plugin.toml").write_text("[plugin]\n", encoding="utf-8")
    _git(path, "add", "plugin.toml")
    _git(path, "commit", "-m", "init")
    return path


def test_directory_without_its_own_repository(tmp_path):
    state = read_repo_state(tmp_path, version="1.2.3")
    assert state.git_available is True
    assert state.is_repository is False
    assert state.tag == "v1.2.3"
    assert state.github_repository is None


def test_clean_repository_with_github_origin_and_tag_at_head(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "https://github.com/owner/repo.git")
    _git(tmp_path, "tag", "v1.2.3")
    state = read_repo_state(tmp_path, version="1.2.3")
    assert state.is_repository is True
    assert state.github_repository == "owner/repo"
    assert state.worktree_clean is True
    assert state.tag_exists is True
    assert state.tag_at_head is True
    # No upstream branch is configured, so HEAD cannot count as pushed.
    assert state.upstream is None
    assert state.head_pushed is False


def test_non_github_origin_is_not_a_github_repository(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "https://example.com/owner/repo.git")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.origin_url == "https://example.com/owner/repo.git"
    assert state.github_repository is None


def test_tracked_changes_dirty_the_worktree_but_untracked_files_do_not(tmp_path):
    _repository(tmp_path)
    (tmp_path / "notes.txt").write_text("untracked", encoding="utf-8")
    assert read_repo_state(tmp_path, version="0.1.0").worktree_clean is True
    (tmp_path / "plugin.toml").write_text("[plugin]\nid = 'x'\n", encoding="utf-8")
    assert read_repo_state(tmp_path, version="0.1.0").worktree_clean is False


def test_tag_left_behind_an_older_commit_is_not_at_head(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "tag", "v0.1.0")
    (tmp_path / "plugin.toml").write_text("[plugin]\nid = 'x'\n", encoding="utf-8")
    _git(tmp_path, "commit", "-am", "more")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.tag_exists is True
    assert state.tag_at_head is False
