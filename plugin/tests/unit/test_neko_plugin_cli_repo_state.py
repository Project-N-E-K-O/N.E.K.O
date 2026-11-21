"""Local git facts of a plugin source checkout."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from plugin.neko_plugin_cli import repo_state
from plugin.neko_plugin_cli.repo_state import committed_file_sizes, read_repo_state, read_workspace_log

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


def test_ssh_origin_credentials_are_not_reported(tmp_path):
    _repository(tmp_path)
    _git(tmp_path, "remote", "add", "origin", "ssh://user:secret@github.com/owner/repo.git")
    state = read_repo_state(tmp_path, version="0.1.0")
    assert state.origin_url == "ssh://github.com/owner/repo.git"
    assert "secret" not in repr(state)


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
    assert read_workspace_log(tmp_path, read_repo_state(tmp_path, version="0.1.0")).changes == ()

    # Tracked work-dir files still count, as do plugin-owned look-alike directories.
    _git(tmp_path, "add", str(work_dir / "lib.py"))
    _git(tmp_path, "commit", "-m", "track work file")
    (work_dir / "lib.py").write_text("changed", encoding="utf-8")
    look_alike = tmp_path / ".vendor.backup-notes"
    look_alike.mkdir()
    (look_alike / "mine.py").write_text("mine", encoding="utf-8")
    state = read_repo_state(tmp_path, version="0.1.0")
    log = read_workspace_log(tmp_path, state)
    assert {(change.path, change.kind) for change in log.changes} == {
        (".vendor.backup-0123abcd/lib.py", "modified"),
        (".vendor.backup-notes/mine.py", "untracked"),
    }
    assert {change.path for change in log.changes} == set(state.dirty_paths)


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



def test_committed_file_sizes_lists_the_head_tree(tmp_path):
    _repository(tmp_path)
    (tmp_path / "data dir").mkdir()
    (tmp_path / "data dir" / "a b.bin").write_bytes(b"\x00" * 300)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "data")
    # Uncommitted content is not what GitHub zips for the commit.
    (tmp_path / "plugin.toml").write_text("[plugin]\nid = 'changed'\n", encoding="utf-8")

    assert sorted(committed_file_sizes(tmp_path)) == [
        ("data dir/a b.bin", 300),
        ("plugin.toml", len("[plugin]\n")),
    ]


def test_committed_file_sizes_is_none_without_a_commit(tmp_path):
    _git(tmp_path, "init")
    assert committed_file_sizes(tmp_path) is None



def test_dirty_paths_name_renamed_and_spaced_files(tmp_path):
    _repository(tmp_path)
    (tmp_path / "old name.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "old name.py")
    _git(tmp_path, "commit", "-m", "add")
    _git(tmp_path, "mv", "old name.py", "new name.py")
    (tmp_path / "plugin.toml").write_text("[plugin]\nid = 'x'\n", encoding="utf-8")
    (tmp_path / "notes draft.md").write_text("todo\n", encoding="utf-8")

    state = read_repo_state(tmp_path, version="0.1.0")

    assert state.worktree_clean is False
    assert set(state.dirty_paths) == {"new name.py", "plugin.toml", "notes draft.md"}
    log = read_workspace_log(tmp_path, state)
    assert {(change.path, change.kind) for change in log.changes} == {
        ("new name.py", "renamed"),
        ("plugin.toml", "modified"),
        ("notes draft.md", "untracked"),
    }


def test_workspace_log_lists_changes_by_kind_and_recent_commits(tmp_path, monkeypatch):
    _repository(tmp_path)
    (tmp_path / "main.py").write_text("print(1)\n", encoding="utf-8")
    _git(tmp_path, "add", "main.py")
    _git(tmp_path, "commit", "-m", "add entry")
    (tmp_path / "main.py").write_text("print(2)\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("todo\n", encoding="utf-8")
    _git(tmp_path, "rm", "-q", "plugin.toml")
    state = read_repo_state(tmp_path, version="0.1.0")
    reads = []
    real_git_output = repo_state.git_output

    def git_output(path, *args, **kwargs):
        reads.append(args)
        return real_git_output(path, *args, **kwargs)

    monkeypatch.setattr(repo_state, "git_output", git_output)
    log = read_workspace_log(tmp_path, state)
    assert sum(args[0] == "status" for args in reads) == 1
    assert log is not None
    assert {(change.path, change.kind) for change in log.changes} == {
        ("main.py", "modified"),
        ("notes.txt", "untracked"),
        ("plugin.toml", "deleted"),
    }
    assert log.changes_total == 3
    assert [commit.subject for commit in log.commits] == ["add entry", "init"]
    assert log.commits[0].sha == state.head
    assert log.commits[0].author == "test"
    assert log.commits[0].timestamp > 0
    # No upstream: whether a commit is pushed is unknown, not false.
    assert log.commits[0].pushed is None
    assert log.ahead is None and log.behind is None


def test_workspace_log_marks_commits_not_on_the_upstream(tmp_path):
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    work.mkdir()
    _git(tmp_path, "init", "--bare", str(remote))
    _repository(work)
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "HEAD:main")
    _git(work, "branch", "--set-upstream-to=origin/main")
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-m", "local only")
    state = read_repo_state(work, version="0.1.0")
    log = read_workspace_log(work, state)
    assert log is not None
    assert [(commit.subject, commit.pushed) for commit in log.commits] == [
        ("local only", False),
        ("init", True),
    ]
    assert (log.ahead, log.behind) == (1, 0)
    assert log.changes == ()


def test_workspace_log_is_none_without_a_repository(tmp_path):
    assert read_workspace_log(tmp_path, read_repo_state(tmp_path, version="0.1.0")) is None
