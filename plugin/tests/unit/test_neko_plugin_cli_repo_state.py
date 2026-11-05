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
    # No upstream branch is configured, so whether HEAD is pushed is unknown.
    assert state.upstream is None
    assert state.head_pushed is None


def test_non_github_origin_is_not_a_github_repository(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "https://example.com/owner/repo.git")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.origin_url == "https://example.com/owner/repo.git"
    assert state.github_repository is None


def test_credentials_in_the_origin_url_are_not_reported(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "https://x-access-token:secret@github.com/owner/repo.git")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.origin_url == "https://github.com/owner/repo.git"
    assert state.github_repository == "owner/repo"


def test_untracked_and_tracked_changes_both_dirty_the_worktree(tmp_path):
    _repository(tmp_path)
    (tmp_path / "notes.txt").write_text("untracked", encoding="utf-8")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.worktree_clean is False
    assert state.dirty_paths == ("notes.txt",)
    (tmp_path / "notes.txt").unlink()
    (tmp_path / "plugin.toml").write_text("[plugin]\nid = 'x'\n", encoding="utf-8")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.worktree_clean is False
    assert state.dirty_paths == ("plugin.toml",)


def test_ignored_files_and_sync_work_dirs_keep_the_worktree_clean(tmp_path):
    _repository(tmp_path)
    (tmp_path / ".gitignore").write_text("build/\n", encoding="utf-8")
    _git(tmp_path, "add", ".gitignore")
    _git(tmp_path, "commit", "-m", "ignore")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.txt").write_text("x", encoding="utf-8")
    work_dir = tmp_path / ".vendor.backup-0123abcd"
    work_dir.mkdir()
    (work_dir / "lib.py").write_text("x", encoding="utf-8")
    assert read_repo_state(tmp_path, version="0.1.0").worktree_clean is True


def test_head_pushed_needs_an_origin_upstream(tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare")
    work = tmp_path / "work"
    work.mkdir()
    _repository(work)
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "fork", str(remote))
    _git(work, "push", "-u", "fork", "main")
    assert read_repo_state(work, version="0.1.0").head_pushed is False
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "fetch", "origin")
    _git(work, "branch", "--set-upstream-to", "origin/main")
    assert read_repo_state(work, version="0.1.0").head_pushed is True


def test_detached_head_has_no_push_verdict(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "checkout", "--detach")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.detached is True
    assert state.branch is None
    assert state.head_pushed is None


def test_tag_left_behind_an_older_commit_is_not_at_head(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "tag", "v0.1.0")
    (tmp_path / "plugin.toml").write_text("[plugin]\nid = 'x'\n", encoding="utf-8")
    _git(tmp_path, "commit", "-am", "more")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.tag_exists is True
    assert state.tag_at_head is False
