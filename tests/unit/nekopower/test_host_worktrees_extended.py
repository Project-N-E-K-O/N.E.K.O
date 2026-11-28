# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for RemoteDefaultBranch, BranchCommit/PullRequest landing, dependency linking, and non-git copies."""

from __future__ import annotations

import asyncio
import contextlib
import difflib
import errno
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

import config.nekopower_settings as settings
from app.agent_server.api_console import TOKEN_HEADER, build_console_router
from nekopower.host import _copy_trees, worktrees
from nekopower.host.worktrees import (
    DIRECTORY_TOO_LARGE,
    NOT_A_REPOSITORY,
    SUBMODULES,
    BranchCommit,
    GitError,
    PRInfo,
    PRRequest,
    PullRequest,
    RemoteDefaultBranch,
    WorktreeError,
    WorktreeManager,
    WorktreeUnavailable,
    parse_git_version,
)
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish
from tests.unit.nekopower.host_fakes import FakeDecisionLLM, fake_model, make_host, scripted_runtime
from tests.unit.nekopower.test_host_tree_work import wait_status

GIT = shutil.which("git")
USER_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git_version() -> tuple[int, int, int] | None:
    if GIT is None:
        return None
    out = subprocess.run([GIT, "--version"], capture_output=True, text=True, check=False).stdout
    return parse_git_version(out)


REAL_GIT_TOO_OLD = GIT is None or (_git_version() or (0, 0, 0)) < settings.WORKTREE_GIT_MIN_VERSION


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: Any) -> None:
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "git-template").mkdir(exist_ok=True)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "git-template"))
    for name in (*worktrees._REDIRECTING_ENV, *USER_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("config.nekopower_settings.WORKTREE_MIN_FREE_BYTES", 1)


def _git(cwd: Path, *args: str, check: bool = True, input: bytes | str | None = None) -> str:
    env = {**os.environ, **USER_ENV, "GIT_OPTIONAL_LOCKS": "0"}
    raw_in = input.encode("utf-8") if isinstance(input, str) else input
    done = subprocess.run(
        ["git", "-c", "core.longpaths=true", *args],
        cwd=cwd,
        input=raw_in,
        capture_output=True,
        env=env,
        check=False,
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {args} failed: {done.stderr.decode('utf-8', 'replace')}")
    return done.stdout.decode("utf-8", "surrogateescape")


def _write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


def _make_repo(path: Path, *, default_branch: str = "main", files: dict[str, str] | None = None) -> Path:
    if REAL_GIT_TOO_OLD:
        pytest.skip("git is missing or older than WORKTREE_GIT_MIN_VERSION")
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", default_branch)
    _git(path, "config", "core.autocrlf", "false")
    base_files = files if files is not None else {
        ".gitignore": "node_modules/\n.venv/\nignored_build/\n",
        "a.txt": "line1\nline2\nline3\nline4\nline5\n",
        "src/app.py": "print('hello')\n",
    }
    for rel, content in base_files.items():
        _write(path / rel, content)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _make_repo_with_remote(
    tmp_path: Path, *, default_branch: str = "main"
) -> tuple[Path, Path, Path]:
    """Return ``(local_repo, bare_remote, second_clone)`` connected via ``origin``."""
    bare = tmp_path / "origin.git"
    bare.mkdir(parents=True, exist_ok=True)
    if REAL_GIT_TOO_OLD:
        pytest.skip("git is missing or older than WORKTREE_GIT_MIN_VERSION")
    _git(bare, "init", "-q", "--bare", "-b", default_branch)
    local = _make_repo(tmp_path / "local", default_branch=default_branch)
    _git(local, "remote", "add", "origin", str(bare))
    _git(local, "push", "-q", "-u", "origin", default_branch)
    _git(local, "remote", "set-head", "origin", "-a")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(bare), str(other))
    _git(other, "config", "core.autocrlf", "false")
    return local, bare, other


# ---------------------------------------------------------------------------
# 1. Start from remote default branch (RemoteDefaultBranch)
# ---------------------------------------------------------------------------


async def test_remote_default_branch_fetches_latest_without_touching_local_refs(tmp_path: Path) -> None:
    local, _bare, other = _make_repo_with_remote(tmp_path)
    # Advance remote via other clone.
    _write(other / "remote_only.txt", "from remote\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "remote advance")
    _git(other, "push", "-q", "origin", "main")
    remote_tip = _git(other, "rev-parse", "HEAD").strip()

    # Leave local dirty and behind.
    local_head_before = _git(local, "rev-parse", "HEAD").strip()
    local_remote_ref_before = _git(local, "rev-parse", "refs/remotes/origin/main").strip()
    _write(local / "local_dirty.txt", "uncommitted\n")
    _write(local / "a.txt", "dirty local edit\n")

    mgr = WorktreeManager()
    tree = await mgr.create(local, tmp_path / "wt" / "r1", RemoteDefaultBranch())

    assert tree.start == remote_tip
    assert (tree.path / "remote_only.txt").read_text(encoding="utf-8") == "from remote\n"
    assert not (tree.path / "local_dirty.txt").exists()
    assert (tree.path / "a.txt").read_text(encoding="utf-8") == "line1\nline2\nline3\nline4\nline5\n"
    # User's local refs and remote-tracking refs were not modified, and temp fetch ref is cleaned up.
    assert _git(local, "rev-parse", "HEAD").strip() == local_head_before
    assert _git(local, "rev-parse", "refs/remotes/origin/main").strip() == local_remote_ref_before
    assert _git(local, "for-each-ref", "refs/nekopower/fetch/").strip() == ""
    await mgr.remove(tree)


async def test_remote_default_branch_discovers_non_main_default_branch(tmp_path: Path) -> None:
    local, _bare, other = _make_repo_with_remote(tmp_path, default_branch="develop")
    _write(other / "dev.txt", "develop branch\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "dev commit")
    _git(other, "push", "-q", "origin", "develop")

    mgr = WorktreeManager()
    assert await mgr.remote_default_branch(local, fetch=True) == "develop"
    tree = await mgr.create(local, tmp_path / "wt" / "dev1", RemoteDefaultBranch())
    assert (tree.path / "dev.txt").read_text(encoding="utf-8") == "develop branch\n"
    await mgr.remove(tree)


async def test_remote_default_branch_offline_and_explicit_branch_and_errors(tmp_path: Path) -> None:
    local, _bare, other = _make_repo_with_remote(tmp_path)
    _git(other, "checkout", "-q", "-b", "release")
    _write(other / "rel.txt", "v1\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "release commit")
    _git(other, "push", "-q", "origin", "release")

    mgr = WorktreeManager()
    # Offline without fetch uses local origin/main.
    offline_tree = await mgr.create(local, tmp_path / "wt" / "off1", RemoteDefaultBranch(fetch=False))
    assert offline_tree.start == _git(local, "rev-parse", "refs/remotes/origin/main").strip()
    await mgr.remove(offline_tree)

    # Explicit remote branch with fetch.
    rel_tree = await mgr.create(local, tmp_path / "wt" / "rel1", RemoteDefaultBranch(branch="release", fetch=True))
    assert (rel_tree.path / "rel.txt").read_text(encoding="utf-8") == "v1\n"
    await mgr.remove(rel_tree)

    # Missing remote raises WorktreeError.
    with pytest.raises(WorktreeError, match="not configured"):
        await mgr.create(local, tmp_path / "wt" / "bad1", RemoteDefaultBranch(remote="upstream"))

    # Invalid branch name raises WorktreeError.
    with pytest.raises(WorktreeError, match="invalid branch name"):
        await mgr.create(local, tmp_path / "wt" / "bad2", RemoteDefaultBranch(branch="--evil"))

    # Non-git directory refuses RemoteDefaultBranch.
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeUnavailable) as raised:
        await mgr.create(plain, tmp_path / "wt" / "bad3", RemoteDefaultBranch())
    assert raised.value.reason == NOT_A_REPOSITORY


async def test_remote_default_branch_rejects_commit_with_submodules(tmp_path: Path) -> None:
    local, _bare, other = _make_repo_with_remote(tmp_path)
    head = _git(other, "rev-parse", "HEAD").strip()
    _git(other, "update-index", "--add", "--cacheinfo", f"160000,{head},submod")
    _git(other, "commit", "-q", "-m", "add submodule link")
    _git(other, "push", "-q", "origin", "main")

    mgr = WorktreeManager()
    with pytest.raises(WorktreeUnavailable) as raised:
        await mgr.create(local, tmp_path / "wt" / "sub1", RemoteDefaultBranch())
    assert raised.value.reason == SUBMODULES


# ---------------------------------------------------------------------------
# 2. Landing to branch (BranchCommit) and Pull Request (PullRequest)
# ---------------------------------------------------------------------------


async def test_branch_commit_local_and_follow_up_and_three_way_conflict(tmp_path: Path) -> None:
    local, _bare, _other = _make_repo_with_remote(tmp_path)
    _write(local / "local_uncommitted.txt", "keep untouched\n")
    head_before = _git(local, "rev-parse", "HEAD").strip()

    mgr = WorktreeManager()
    tree = await mgr.create(local, tmp_path / "wt" / "br1", RemoteDefaultBranch(fetch=False))
    # Nothing changed yet -> unchanged.
    noop = await mgr.merge_back(tree, BranchCommit(branch="feat/demo", message="noop"))
    assert noop.status == "unchanged"

    # Round 1: edit and add file.
    _write(tree.path / "a.txt", "LINE1\nline2\nline3\nline4\nline5\n")
    _write(tree.path / "added.txt", "hello\nworld\n")
    r1 = await mgr.merge_back(tree, BranchCommit(branch="feat/demo", message="round 1"))
    assert r1.status == "landed", r1.detail
    assert r1.branch == "feat/demo" and r1.commit
    assert sorted((f.path, f.status) for f in r1.files) == [("a.txt", "modified"), ("added.txt", "added")]
    assert _git(local, "rev-parse", "refs/heads/feat/demo").strip() == r1.commit
    # User's working tree and HEAD are completely untouched.
    assert _git(local, "rev-parse", "HEAD").strip() == head_before
    assert not (local / "added.txt").exists()
    assert (local / "local_uncommitted.txt").read_text(encoding="utf-8") == "keep untouched\n"

    # Round 2: follow-up commit fast-forwards on feat/demo.
    _write(tree.path / "added.txt", "hello\nworld\nround2\n")
    r2 = await mgr.merge_back(tree, BranchCommit(branch="feat/demo", message="round 2"))
    assert r2.status == "landed", r2.detail
    assert _git(local, "rev-parse", f"{r2.commit}^").strip() == r1.commit

    # Simulate someone advancing feat/demo with a conflicting edit on line 1 of a.txt.
    conflict_wt = tmp_path / "other_branch_wt"
    _git(local, "worktree", "add", "-q", str(conflict_wt), "feat/demo")
    _write(conflict_wt / "a.txt", "EXTERNAL\nline2\nline3\nline4\nline5\n")
    _git(conflict_wt, "add", "a.txt")
    _git(conflict_wt, "commit", "-q", "-m", "external edit")
    _git(local, "worktree", "remove", "--force", str(conflict_wt))

    _write(tree.path / "a.txt", "WORKTREE\nline2\nline3\nline4\nline5\n")
    r3 = await mgr.merge_back(tree, BranchCommit(branch="feat/demo", message="round 3"))
    assert r3.status == "conflict" and r3.conflicts == ("a.txt",)

    tree = await mgr.prepare_conflicts(tree, r3)
    assert "<<<<<<<" in (tree.path / "a.txt").read_text(encoding="utf-8")
    _write(tree.path / "a.txt", "BOTH\nline2\nline3\nline4\nline5\n")
    r4 = await mgr.merge_back(tree, BranchCommit(branch="feat/demo", message="round 4 resolved"))
    assert r4.status == "landed", r4.detail
    assert _git(local, "show", "feat/demo:a.txt") == "BOTH\nline2\nline3\nline4\nline5\n"
    await mgr.remove(tree)


async def test_branch_commit_pushes_protects_defaults_and_rolls_back_on_reject(tmp_path: Path) -> None:
    local, bare, other = _make_repo_with_remote(tmp_path)
    mgr = WorktreeManager()
    tree = await mgr.create(local, tmp_path / "wt" / "push1", RemoteDefaultBranch(fetch=False))
    _write(tree.path / "src/app.py", "print('updated')\n")

    # Refuses protected & checked-out branches.
    for forbidden in ("main", "master", "MAIN"):
        denied = await mgr.merge_back(tree, BranchCommit(branch=forbidden, remote="origin"))
        assert denied.status == "failed" and "protected" in denied.detail

    # Refuses pre-existing branch owned by user.
    _git(local, "branch", "existing-user-branch")
    denied_existing = await mgr.merge_back(tree, BranchCommit(branch="existing-user-branch"))
    assert denied_existing.status == "failed" and "already exists" in denied_existing.detail

    # Refuses pushing non-fast-forward (never force-pushes) and rolls back local branch ref.
    _git(other, "checkout", "-q", "-b", "feat/diverged")
    _write(other / "div.txt", "remote commit\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "remote diverged")
    _git(other, "push", "-q", "origin", "feat/diverged")
    rejected = await mgr.merge_back(tree, BranchCommit(branch="feat/diverged", remote="origin"))
    assert rejected.status == "failed" and "pushing branch" in rejected.detail
    assert _git(local, "show-ref", "--verify", "refs/heads/feat/diverged", check=False).strip() == ""

    # Clean push succeeds to origin.
    pushed = await mgr.merge_back(tree, BranchCommit(branch="feat/clean", message="clean push", remote="origin"))
    assert pushed.status == "landed", pushed.detail
    assert pushed.remote == "origin" and pushed.branch == "feat/clean"
    assert _git(bare, "rev-parse", "refs/heads/feat/clean").strip() == pushed.commit
    await mgr.remove(tree)


async def test_pull_request_landing_opens_draft_retries_and_reuses_pr(tmp_path: Path) -> None:
    local, bare, _other = _make_repo_with_remote(tmp_path)
    mgr = WorktreeManager()
    tree = await mgr.create(local, tmp_path / "wt" / "pr1", RemoteDefaultBranch(fetch=False))
    _write(tree.path / "feature.py", "def run():\n    return 42\n")

    seen: list[PRRequest] = []
    fail_once = {"flag": True}

    async def fake_create_pr(req: PRRequest) -> PRInfo:
        seen.append(req)
        if fail_once["flag"]:
            fail_once["flag"] = False
            raise RuntimeError("temporary github outage")
        return PRInfo(url="https://github.com/acme/demo/pull/77", number=77)

    # First attempt pushes branch but PR API fails -> returns failed while preserving pushed branch.
    pr_landing = PullRequest(
        title="Add feature",
        body="Detailed description",
        branch="feat/pr-demo",
        create_pr=fake_create_pr,
    )
    first = await mgr.merge_back(tree, pr_landing)
    assert first.status == "failed" and "temporary github outage" in first.detail
    assert first.branch == "feat/pr-demo" and first.commit
    assert _git(bare, "rev-parse", "refs/heads/feat/pr-demo").strip() == first.commit

    # Second attempt retries PR creation cleanly even though no new file edits happened.
    second = await mgr.merge_back(tree, pr_landing)
    assert second.status == "landed", second.detail
    assert second.pr_url == "https://github.com/acme/demo/pull/77" and second.pr_number == 77
    assert len(seen) == 2
    assert seen[-1].draft is True and seen[-1].base_branch == "main" and seen[-1].branch == "feat/pr-demo"

    # Follow-up edit pushes a second commit to the same branch and reuses existing PR without creating a duplicate.
    _write(tree.path / "feature.py", "def run():\n    return 99\n")
    third = await mgr.merge_back(tree, pr_landing)
    assert third.status == "landed", third.detail
    assert third.pr_url == "https://github.com/acme/demo/pull/77" and third.pr_number == 77
    assert len(seen) == 2  # create_pr not called again
    assert _git(bare, "show", "feat/pr-demo:feature.py") == "def run():\n    return 99\n"
    await mgr.remove(tree)


async def test_pull_request_landing_with_gh_cli_shim(tmp_path: Path) -> None:
    local, bare, _other = _make_repo_with_remote(tmp_path)
    recorded = tmp_path / "gh_call.json"
    shim_py = tmp_path / "fake_gh.py"
    shim_py.write_text(
        "import json, sys\n"
        f"open({str(recorded)!r}, 'w', encoding='utf-8').write(json.dumps({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}))\n"
        "print('https://github.com/acme/demo/pull/456')\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        gh_bin = tmp_path / "fake_gh.cmd"
        gh_bin.write_text(f'@echo off\r\n"{sys.executable}" "{shim_py}" %*\r\n', encoding="utf-8")
    else:
        gh_bin = tmp_path / "fake_gh.sh"
        gh_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{shim_py}" "$@"\n', encoding="utf-8")
        os.chmod(gh_bin, 0o755)

    mgr = WorktreeManager()
    tree = await mgr.create(local, tmp_path / "wt" / "gh1", RemoteDefaultBranch(fetch=False))
    _write(tree.path / "hello.txt", "hi\n")

    res = await mgr.merge_back(
        tree,
        PullRequest(title="My PR", body="Line 1\nLine 2\n", branch="feat/gh", draft=True, gh=str(gh_bin)),
    )
    assert res.status == "landed", res.detail
    assert res.pr_url == "https://github.com/acme/demo/pull/456" and res.pr_number == 456
    assert _git(bare, "rev-parse", "refs/heads/feat/gh").strip() == res.commit
    call_data = json.loads(recorded.read_text(encoding="utf-8"))
    assert "--draft" in call_data["argv"] and "--head" in call_data["argv"] and "feat/gh" in call_data["argv"]
    assert call_data["stdin"] == "Line 1\nLine 2\n"
    await mgr.remove(tree)


# ---------------------------------------------------------------------------
# 3. Dependency linking in git worktrees & target deletion safety
# ---------------------------------------------------------------------------


async def test_git_worktree_links_ignored_dependencies_safely(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    # Tracked vendor dir must NOT be overwritten/linked.
    _write(repo / "vendor/tracked.py", "tracked = True\n")
    _write(repo / "frontend/app/index.js", "export const x = 1;\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add tracked vendor and frontend")

    # Ignored dependency directories + ignored build dir (must not link build dir).
    _write(repo / "node_modules/pkg/index.js", "module.exports = 1;\n")
    _write(repo / ".venv/pyvenv.cfg", "home = /python\n")
    _write(repo / "frontend/app/node_modules/ui/btn.js", "export const btn = 1;\n")
    _write(repo / "ignored_build/out.js", "compiled\n")

    mgr = WorktreeManager()
    tree = await mgr.create(repo, tmp_path / "wt" / "dep1")
    assert tree.linked_dependencies == (".venv", "frontend/app/node_modules", "node_modules")
    assert _copy_trees.is_dir_link(tree.path / "node_modules")
    assert _copy_trees.is_dir_link(tree.path / ".venv")
    assert _copy_trees.is_dir_link(tree.path / "frontend/app/node_modules")
    assert not (tree.path / "ignored_build").exists()
    # Tracked vendor stayed a real directory.
    assert not _copy_trees.is_dir_link(tree.path / "vendor")
    assert (tree.path / "node_modules/pkg/index.js").read_text(encoding="utf-8") == "module.exports = 1;\n"
    assert (tree.path / ".venv/pyvenv.cfg").read_text(encoding="utf-8") == "home = /python\n"

    # Linked deps do NOT count as uncommitted changes, even after reopen.
    assert not await mgr.has_changes(tree)
    reopened = await mgr.open(repo, tree.path)
    assert reopened.linked_dependencies == tree.linked_dependencies
    assert not await mgr.has_changes(reopened)

    # Normal file edit merges cleanly without touching or skipping linked deps.
    _write(tree.path / "a.txt", "line1-edited\nline2\nline3\nline4\nline5\n")
    landed = await mgr.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert [f.path for f in landed.files] == ["a.txt"]
    assert landed.skipped == ()

    # Removing the worktree MUST NOT delete a single file in the real dependency directories!
    await mgr.remove(tree)
    assert not tree.path.exists()
    assert (repo / "node_modules/pkg/index.js").read_text(encoding="utf-8") == "module.exports = 1;\n"
    assert (repo / ".venv/pyvenv.cfg").read_text(encoding="utf-8") == "home = /python\n"
    assert (repo / "frontend/app/node_modules/ui/btn.js").read_text(encoding="utf-8") == "export const btn = 1;\n"


async def test_git_prepare_conflicts_preserves_dependency_links_and_targets(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _write(repo / "node_modules/pkg/keep.txt", "keep\n")
    _write(repo / ".venv/pyvenv.cfg", "keep-venv\n")

    mgr = WorktreeManager()
    tree = await mgr.create(repo, tmp_path / "wt" / "depconf")
    assert tree.linked_dependencies == (".venv", "node_modules")

    _write(repo / "a.txt", "PROJECT\nline2\nline3\nline4\nline5\n")
    _write(tree.path / "a.txt", "WORKTREE\nline2\nline3\nline4\nline5\n")
    clashed = await mgr.merge_back(tree)
    assert clashed.status == "conflict"

    tree = await mgr.prepare_conflicts(tree, clashed)
    assert _copy_trees.is_dir_link(tree.path / "node_modules")
    assert _copy_trees.is_dir_link(tree.path / ".venv")
    assert (repo / "node_modules/pkg/keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (repo / ".venv/pyvenv.cfg").read_text(encoding="utf-8") == "keep-venv\n"

    _write(tree.path / "a.txt", "RESOLVED\nline2\nline3\nline4\nline5\n")
    landed = await mgr.merge_back(tree)
    assert landed.status == "landed", landed.detail
    await mgr.remove(tree)
    assert (repo / "node_modules/pkg/keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (repo / ".venv/pyvenv.cfg").read_text(encoding="utf-8") == "keep-venv\n"


# ---------------------------------------------------------------------------
# 4. Non-git directory parallel working copies
# ---------------------------------------------------------------------------


async def test_non_git_parallel_copies_three_way_merge_without_git(tmp_path: Path) -> None:
    plain = tmp_path / "notes"
    _write(plain / "doc.txt", "top\nmid1\nmid2\nmid3\nbottom\n")
    _write(plain / "keep.txt", "unchanged\n")
    _write(plain / "node_modules/tool/bin.js", "ok\n")
    _write(plain / ".venv/pyvenv.cfg", "venv-ok\n")

    # Works even when git executable does not exist at all!
    mgr = WorktreeManager(git=str(tmp_path / "missing-git"))
    probed = await mgr.probe(plain)
    assert probed.usable and probed.kind == "copy" and probed.repo_root is None

    # git-only probe still reports NOT_A_REPOSITORY / GIT_MISSING.
    assert not (await mgr.probe(plain, allow_non_git=False)).usable

    w1 = await mgr.create(plain, tmp_path / "wt" / "c1")
    w2 = await mgr.create(plain, tmp_path / "wt" / "c2")
    assert w1.kind == "copy" and w2.kind == "copy"
    assert w1.linked_dependencies == (".venv", "node_modules")
    assert _copy_trees.is_dir_link(w1.path / "node_modules")
    assert not await mgr.has_changes(w1)

    # Concurrent edits across w1, w2, and project dir itself!
    _write(w1.path / "doc.txt", "TOP-W1\nmid1\nmid2\nmid3\nbottom\n")
    _write(w1.path / "from_w1.txt", "w1\n")
    _write(w2.path / "doc.txt", "top\nmid1\nmid2\nmid3\nBOTTOM-W2\n")
    _write(w2.path / "from_w2.txt", "w2\n")
    _write(plain / "user_new.txt", "user\n")

    assert await mgr.has_changes(w1)
    r1 = await mgr.merge_back(w1)
    assert r1.status == "landed", r1.detail
    assert not await mgr.has_changes(w1)

    r2 = await mgr.merge_back(w2)
    assert r2.status == "landed", r2.detail
    assert (plain / "doc.txt").read_text(encoding="utf-8") == "TOP-W1\nmid1\nmid2\nmid3\nBOTTOM-W2\n"
    assert (plain / "from_w1.txt").read_text(encoding="utf-8") == "w1\n"
    assert (plain / "from_w2.txt").read_text(encoding="utf-8") == "w2\n"
    assert (plain / "user_new.txt").read_text(encoding="utf-8") == "user\n"
    # Zero pollution inside user's non-git directory.
    assert not (plain / ".git").exists()
    assert not (plain / ".nekopower-copies").exists()

    # Reopen, remove, and ensure real dependency dirs survive completely intact.
    reopened = await mgr.open(plain, w1.path)
    assert reopened.kind == "copy" and reopened.token == w1.token
    await mgr.remove(w1)
    await mgr.remove(w2)
    assert not w1.path.exists() and not w2.path.exists()
    assert (plain / "node_modules/tool/bin.js").read_text(encoding="utf-8") == "ok\n"
    assert (plain / ".venv/pyvenv.cfg").read_text(encoding="utf-8") == "venv-ok\n"


async def test_non_git_conflict_prepare_resolve_and_reconcile_and_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    _write(plain / "a.txt", "one\ntwo\nthree\n")
    _write(plain / "sub/del.txt", "bye\n")

    mgr = WorktreeManager()
    w1 = await mgr.create(plain, tmp_path / "wt" / "nc1")
    w_orphan_clean = await mgr.create(plain, tmp_path / "wt" / "orphan_clean")
    w_orphan_dirty = await mgr.create(plain, tmp_path / "wt" / "orphan_dirty")
    _write(w_orphan_dirty.path / "extra.txt", "keep me\n")

    # Conflicting edits on line 1.
    _write(plain / "a.txt", "PROJECT\ntwo\nthree\n")
    _write(w1.path / "a.txt", "COPY\ntwo\nthree\n")
    (w1.path / "sub/del.txt").unlink()
    res = await mgr.merge_back(w1)
    assert res.status == "conflict" and res.conflicts == ("a.txt",)
    # Project untouched on conflict.
    assert (plain / "a.txt").read_text(encoding="utf-8") == "PROJECT\ntwo\nthree\n"
    assert (plain / "sub/del.txt").exists()

    w1 = await mgr.prepare_conflicts(w1, res)
    marked = (w1.path / "a.txt").read_text(encoding="utf-8")
    assert "<<<<<<<" in marked and "PROJECT" in marked and "COPY" in marked
    _write(w1.path / "a.txt", "MERGED\ntwo\nthree\n")
    landed = await mgr.merge_back(w1)
    assert landed.status == "landed", landed.detail
    assert (plain / "a.txt").read_text(encoding="utf-8") == "MERGED\ntwo\nthree\n"
    assert not (plain / "sub/del.txt").exists()

    # Reconcile removes clean orphan and keeps dirty orphan.
    report = await mgr.reconcile(plain, tmp_path / "wt", [w1.id])
    assert w_orphan_clean.id in report.removed
    assert not w_orphan_clean.path.exists()
    assert w_orphan_dirty.path in report.kept and w_orphan_dirty.path.exists()

    # Token guard: stale handle cannot remove a newer copy at the same path.
    old_handle = w1
    await mgr.remove(old_handle)
    fresh = await mgr.create(plain, tmp_path / "wt" / "nc1")
    with pytest.raises(WorktreeError, match="made after this handle"):
        await mgr.remove(old_handle)
    assert fresh.path.exists()
    await mgr.remove(fresh)
    await mgr.remove(w_orphan_dirty)

    # Branch landing is cleanly refused on non-git copy.
    c3 = await mgr.create(plain, tmp_path / "wt" / "nc3")
    _write(c3.path / "a.txt", "x\n")
    denied = await mgr.merge_back(c3, BranchCommit(branch="feat/nope"))
    assert denied.status == "failed" and "git repository" in denied.detail
    await mgr.remove(c3)

    # Size limit check.
    monkeypatch.setattr(settings, "WORKTREE_COPY_MAX_FILES", 0)
    too_big = await mgr.probe(plain)
    assert not too_big.usable and too_big.reason == DIRECTORY_TOO_LARGE


# ---------------------------------------------------------------------------
# 5. End-to-end host (TreeWork) & console API integration
# ---------------------------------------------------------------------------


async def test_host_non_git_project_runs_team_in_parallel_with_linked_deps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "TASK_AGENT_FORK_CONTINUE_ENABLED", False)
    monkeypatch.setattr(settings, "TASK_AGENT_REVIEW_ENABLED", False)
    project_dir = tmp_path / "PlainProject"
    _write(project_dir / "index.txt", "hello\n")
    _write(project_dir / "node_modules/checker/version.txt", "1.0\n")
    _write(project_dir / ".venv/pyvenv.cfg", "venv\n")

    gate_a = asyncio.Event()
    gate_b = asyncio.Event()
    entered: list[str] = []

    def _report(text: str) -> list[Any]:
        return [call("report", {"status": "ready", "summary": text}), finish("tool_calls")]

    def _write_call(path: str, content: str) -> list[Any]:
        return [call("write", {"file_path": path, "content": content}), finish("tool_calls")]

    leader_llm = ScriptedLLM([
        [
            call(
                "start_team",
                {
                    "parts": [
                        {"name": "alpha", "preset": "worker", "description": "write alpha", "brief": "create alpha.txt"},
                        {"name": "beta", "preset": "worker", "description": "write beta", "brief": "create beta.txt"},
                    ]
                },
            ),
            finish("tool_calls"),
        ],
        [call("wait_agent", {"names": ["alpha", "beta"]}), finish("tool_calls")],
        _report("both parts finished"),
    ])

    async def wait_both_a() -> None:
        entered.append("alpha")
        if len(entered) == 2:
            gate_a.set()
            gate_b.set()
        await asyncio.wait_for(gate_a.wait(), timeout=10.0)

    async def wait_both_b() -> None:
        entered.append("beta")
        if len(entered) == 2:
            gate_a.set()
            gate_b.set()
        await asyncio.wait_for(gate_b.wait(), timeout=10.0)

    worker_a = ScriptedLLM([[wait_both_a, *_write_call("alpha.txt", "from alpha\n")], _report("alpha done")])
    worker_b = ScriptedLLM([[wait_both_b, *_write_call("beta.txt", "from beta\n")], _report("beta done")])

    # Route by commission title/dispatch text via a small dispatcher.
    by_prompt: dict[str, ScriptedLLM] = {}

    def router(spec: Any) -> ScriptedLLM:
        # First session is leader; subsequent children get worker_a then worker_b.
        if not by_prompt:
            by_prompt["leader"] = leader_llm
            return leader_llm
        if "a" not in by_prompt:
            by_prompt["a"] = worker_a
            return worker_a
        by_prompt["b"] = worker_b
        return worker_b

    host = make_host(
        tmp_path / "data",
        None,
        runtime_factory=scripted_runtime(router),
        task_agent_model=fake_model,
        decision_llm=FakeDecisionLLM(),
    )
    try:
        await host.space.touch_project("PlainProject", workdir=str(project_dir))
        await host.derive_leader("PlainProject", "Lan")
        await host.permission_store.set_mode("open_session", "allow")
        receipt = await host.dispatch(
            "PlainProject: split alpha and beta in parallel",
            user_text="split alpha and beta in parallel",
            lanlan_name="Lan",
        )
        done = await wait_status(host, receipt.commission_id, "done", timeout=30)
        assert done.status == "done"
        # Both workers entered concurrently in isolated copies (not serialized!).
        assert set(entered) == {"alpha", "beta"}
        assert (project_dir / "alpha.txt").read_text(encoding="utf-8") == "from alpha\n"
        assert (project_dir / "beta.txt").read_text(encoding="utf-8") == "from beta\n"
        # Real dependency dirs survived cleanup untouched.
        assert (project_dir / "node_modules/checker/version.txt").read_text(encoding="utf-8") == "1.0\n"
        assert (project_dir / ".venv/pyvenv.cfg").read_text(encoding="utf-8") == "venv\n"
        # Opening prompt told workers about isolated working copy and linked deps (not git worktree).
        first_worker_messages = str(worker_a.requests[0])
        assert "isolated working copy" in first_worker_messages
        assert "node_modules" in first_worker_messages
        assert ".venv" in first_worker_messages
    finally:
        await host.shutdown()


async def test_console_land_route_supports_branch_and_rejects_bad_strategy(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    host = make_host(tmp_path / "data", None, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())

    async def no_reconcile() -> dict[str, list[str]]:
        return {"removed": [], "kept": []}

    host.task_agents.reconcile_trees = no_reconcile
    token = "test-console-token"
    headers = {TOKEN_HEADER: token}
    app = FastAPI()
    app.include_router(build_console_router(lambda: asyncio.sleep(0, result=host), lambda: token))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent.test")
    try:
        wt_path = host.paths.worktree_dir("consolebr1")
        handle = await host.task_agents.worktrees.create(repo, wt_path)
        _write(handle.path / "a.txt", "console-branch-change\n")
        await host.ledger.add_tree(
            id=handle.id,
            project="Demo",
            project_dir=str(repo),
            repo_root=str(handle.repo_root),
            path=str(handle.path),
            workdir=str(handle.workdir),
            token=handle.token,
            state="held",
            reason="ask",
            lanlan_name="Lan",
        )
        # Invalid strategy -> 400 bad_landing.
        bad = await client.post(f"/console/worktrees/{handle.id}/land", headers=headers, json={"strategy": "magic"})
        assert bad.status_code == 400 and bad.json()["detail"]["code"] == "bad_landing"

        # Missing title for PR -> 400 bad_landing.
        bad_pr = await client.post(f"/console/worktrees/{handle.id}/land", headers=headers, json={"strategy": "pr"})
        assert bad_pr.status_code == 400 and bad_pr.json()["detail"]["code"] == "bad_landing"

        # Valid branch strategy lands to branch without touching repo working files.
        ok = await client.post(
            f"/console/worktrees/{handle.id}/land",
            headers=headers,
            json={"strategy": "branch", "branch": "feat/from-console", "message": "from console"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["success"] and body["status"] == "landed" and body["branch"] == "feat/from-console" and body["commit"]
        assert _git(repo, "show", "feat/from-console:a.txt") == "console-branch-change\n"
        assert (repo / "a.txt").read_text(encoding="utf-8") == "line1\nline2\nline3\nline4\nline5\n"
    finally:
        await client.aclose()
        await host.shutdown()


async def test_branch_three_way_preserves_shared_files_local_to_pr_and_remote_protection(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    # Create a bare remote whose default branch is `develop` with no cached refs/remotes/devremote/HEAD.
    dev_bare = tmp_path / "dev-bare.git"
    _git(tmp_path, "init", "--bare", "-q", str(dev_bare))
    _git(dev_bare, "symbolic-ref", "HEAD", "refs/heads/develop")
    _git(repo, "remote", "add", "devremote", str(dev_bare))
    _git(repo, "push", "-q", "devremote", "HEAD:refs/heads/develop")

    origin_bare = tmp_path / "origin-bare.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "main", str(origin_bare))
    _git(repo, "remote", "add", "origin", str(origin_bare))
    _git(repo, "push", "-q", "origin", "main")

    manager = WorktreeManager()
    wt = await manager.create(repo, tmp_path / "wt" / "br3way")

    # 1. Default branch `develop` on a newly added remote without cached HEAD must still be protected.
    _write(wt.path / "a.txt", "edit-1\nline2\nline3\nline4\nline5\n")
    blocked_dev = await manager.merge_back(wt, BranchCommit(branch="develop", remote="devremote", message="bad"))
    assert blocked_dev.status == "failed" and "protected default branch" in blocked_dev.detail

    # 2. Local BranchCommit followed by PullRequest without further edits must push and open the PR.
    local_res = await manager.merge_back(wt, BranchCommit(branch="feat/shared", message="local step"))
    assert local_res.status == "landed" and local_res.commit

    # Advance the branch externally with a new shared file (simulating a collaborator commit).
    helper_wt = tmp_path / "ext-wt"
    _git(repo, "worktree", "add", "-q", str(helper_wt), "feat/shared")
    _write(helper_wt / "shared.txt", "collaborator file\n")
    _git(helper_wt, "add", "shared.txt")
    _git(helper_wt, "commit", "-q", "--no-gpg-sign", "-m", "add shared file")
    _git(repo, "worktree", "remove", "--force", str(helper_wt))

    # Worktree makes a second edit; 3-way merge keeps shared.txt.
    _write(wt.path / "a.txt", "edit-2\nline2\nline3\nline4\nline5\n")
    merged_once = await manager.merge_back(wt, BranchCommit(branch="feat/shared", message="second step"))
    assert merged_once.status == "landed"
    assert _git(repo, "show", "feat/shared:shared.txt") == "collaborator file\n"

    # Worktree makes a third edit; shared.txt MUST still survive (not treated as deleted by baseline drift).
    _write(wt.path / "a.txt", "edit-3\nline2\nline3\nline4\nline5\n")
    merged_twice = await manager.merge_back(wt, BranchCommit(branch="feat/shared", message="third step"))
    assert merged_twice.status == "landed"
    assert _git(repo, "show", "feat/shared:shared.txt") == "collaborator file\n"
    assert _git(repo, "show", "feat/shared:a.txt").startswith("edit-3\n")

    # Now deliver the already-committed local branch as a PR without any further edits.
    pr_requests: list[PRRequest] = []

    async def fake_pr(req: PRRequest) -> PRInfo:
        pr_requests.append(req)
        num = 100 + len(pr_requests)
        return PRInfo(url=f"https://github.com/org/repo/pull/{num}", number=num)

    pr1 = await manager.merge_back(
        wt,
        PullRequest(branch="feat/shared", remote="origin", base_branch="main", title="Shared PR", create_pr=fake_pr),
    )
    assert pr1.status == "landed" and pr1.pr_number == 101 and len(pr_requests) == 1
    assert _git(repo, "ls-remote", "--heads", "origin", "feat/shared").strip()

    # Different base branch must not silently return PR #101.
    _git(repo, "branch", "release", "main")
    _git(repo, "push", "-q", "origin", "release")
    pr2 = await manager.merge_back(
        wt,
        PullRequest(branch="feat/shared", remote="origin", base_branch="release", title="Release PR", create_pr=fake_pr),
    )
    assert pr2.status == "landed" and pr2.pr_number == 102 and len(pr_requests) == 2
    assert [req.base_branch for req in pr_requests] == ["main", "release"]

    # Remote URL parsing for `gh --repo`.
    assert WorktreeManager._github_repo_from_remote_url("git@github.com:Project-N-E-K-O/N.E.K.O.git") == "Project-N-E-K-O/N.E.K.O"
    assert WorktreeManager._github_repo_from_remote_url("https://github.com/acme/widget.git") == "acme/widget"
    assert WorktreeManager._github_repo_from_remote_url("https://ghe.corp.local/team/app.git") == "ghe.corp.local/team/app"
    assert WorktreeManager._github_repo_from_remote_url(r"C:\tmp\bare.git") is None

    await manager.remove(wt)


async def test_copy_blocks_project_junction_skipped_files_and_drifted_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "plain"
    outside = tmp_path / "outside"
    _write(project / "a.txt", "alpha\n")
    _write(outside / "secret.txt", "keep\n")
    # Oversized file in project (lower threshold for fast testing).
    monkeypatch.setattr(settings, "WORKTREE_UNTRACKED_MAX_BYTES", 64)
    _write(project / "big.bin", "X" * 200)
    # Non-dependency directory junction / symlink pointing outside project.
    _copy_trees.make_dir_link(outside, project / "escape")

    manager = WorktreeManager()
    copy = await manager.create(project, tmp_path / "copies" / "sec1")

    # 1. Agent trying to add a file at a skipped oversized path must conflict, never overwrite big.bin.
    _write(copy.path / "big.bin", "tiny\n")
    clash_big = await manager.merge_back(copy)
    assert clash_big.status == "conflict" and "big.bin" in clash_big.conflicts
    assert (project / "big.bin").read_text(encoding="utf-8") == "X" * 200
    (copy.path / "big.bin").unlink()

    # 2. Agent trying to write through project's non-dependency junction/symlink must be blocked.
    # On POSIX, populate_copy replicates the directory symlink into the copy; replace only the link
    # with a real directory so the write stays in the copy and exercises project-side link protection.
    escape_in_copy = copy.path / "escape"
    escape_symlink_target = os.readlink(escape_in_copy) if escape_in_copy.is_symlink() else None
    if _copy_trees.is_dir_link(escape_in_copy):
        _copy_trees.remove_dir_link(escape_in_copy)
    escape_in_copy.mkdir(exist_ok=True)
    _write(escape_in_copy / "pwn.txt", "boom\n")
    clash_link = await manager.merge_back(copy)
    assert clash_link.status == "conflict" and "escape/pwn.txt" in clash_link.conflicts
    assert not (outside / "pwn.txt").exists()
    shutil.rmtree(escape_in_copy)
    if escape_symlink_target is not None:
        os.symlink(escape_symlink_target, escape_in_copy)

    # Direct defense-in-depth check in apply_copy_changes as well.
    with pytest.raises(OSError, match="directory link"):
        _copy_trees.apply_copy_changes(
            project,
            [("escape/pwn.txt", "added", 1, 0)],
            {"escape/pwn.txt": {"kind": "file", "sha256": "0", "size": 5, "mode": 0o644}},
            {"escape/pwn.txt": b"boom\n"},
        )
    assert not (outside / "pwn.txt").exists()

    # 3. Newly created real `vendor/custom.py` in copy (not a linked dep) IS detected and lands,
    # AND subsequent edits to `vendor/custom.py` also land cleanly without conflicting as a dep dir.
    _write(copy.path / "vendor" / "custom.py", "print('ok')\n")
    assert await manager.has_changes(copy) is True
    landed_vendor = await manager.merge_back(copy)
    assert landed_vendor.status == "landed"
    assert (project / "vendor" / "custom.py").read_text(encoding="utf-8") == "print('ok')\n"

    _write(copy.path / "vendor" / "custom.py", "print('ok-v2')\n")
    landed_vendor_v2 = await manager.merge_back(copy)
    assert landed_vendor_v2.status == "landed"
    assert (project / "vendor" / "custom.py").read_text(encoding="utf-8") == "print('ok-v2')\n"

    # If the user later turns that tracked `vendor/` directory into a nested Git repo,
    # subsequent edits in the copy must conflict instead of overwriting files inside it.
    (project / "vendor" / ".git").mkdir()
    _write(copy.path / "vendor" / "custom.py", "print('overwrite-repo')\n")
    clash_nested_git = await manager.merge_back(copy)
    assert clash_nested_git.status == "conflict" and "vendor/custom.py" in clash_nested_git.conflicts
    assert (project / "vendor" / "custom.py").read_text(encoding="utf-8") == "print('ok-v2')\n"
    _write(copy.path / "vendor" / "custom.py", "print('ok-v2')\n")

    # 4. If user edits a.txt between compute_copy_merge and apply_copy_changes, landing fails without clobbering.
    _write(copy.path / "a.txt", "from-agent\n")
    orig_compute = _copy_trees.compute_copy_merge

    def compute_then_user_saves(*args: Any, **kwargs: Any) -> dict[str, Any]:
        res = orig_compute(*args, **kwargs)
        _write(project / "a.txt", "user-newer-save\n")
        return res

    monkeypatch.setattr(_copy_trees, "compute_copy_merge", compute_then_user_saves)
    drifted = await manager.merge_back(copy)
    assert drifted.status == "failed" and "changed" in drifted.detail
    assert (project / "a.txt").read_text(encoding="utf-8") == "user-newer-save\n"

    await manager.remove(copy)


async def test_copy_binary_conflict_preserves_both_versions_and_cancel_waits_for_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import stat as stat_mod
    import threading

    project = tmp_path / "plain"
    project.mkdir(parents=True)
    (project / "img.bin").write_bytes(b"\x00\x01base")
    _write(project / "a.txt", "one\ntwo\n")

    manager = WorktreeManager()
    copy = await manager.create(project, tmp_path / "copies" / "bin1")

    # Binary conflict: preparing conflicts must keep worktree's binary in copy AND save both versions.
    (project / "img.bin").write_bytes(b"\x00\x02project")
    (copy.path / "img.bin").write_bytes(b"\x00\x03worktree")
    clash = await manager.merge_back(copy)
    assert clash.status == "conflict" and "img.bin" in clash.conflicts

    # Simulate mid-preparation failure -> copy stays intact and retry works.
    orig_copyfile = shutil.copyfile
    fail_once = {"done": False}

    def flaky_copyfile(src: str, dst: str, *args: Any, **kwargs: Any) -> Any:
        if not fail_once["done"] and str(dst).endswith("img.bin"):
            fail_once["done"] = True
            raise OSError("simulated ENOSPC")
        return orig_copyfile(src, dst, *args, **kwargs)

    monkeypatch.setattr(shutil, "copyfile", flaky_copyfile)
    with pytest.raises(OSError, match="simulated ENOSPC"):
        await manager.prepare_conflicts(copy, clash)
    assert (copy.path / "img.bin").read_bytes() == b"\x00\x03worktree"

    # Retry succeeds and preserves worktree bytes plus conflict-version metadata.
    prepared = await manager.prepare_conflicts(copy, clash)
    assert (prepared.path / "img.bin").read_bytes() == b"\x00\x03worktree"
    versions_file = _copy_trees.copy_meta_dir(prepared.path) / "conflicts" / clash.merged_tree / "versions.json"
    assert versions_file.is_file()
    versions = json.loads(versions_file.read_text(encoding="utf-8"))
    assert versions["img.bin"]["project"]["sha256"] != versions["img.bin"]["worktree"]["sha256"]

    # Subsequent landing applies the kept worktree binary cleanly.
    resolved = await manager.merge_back(prepared)
    assert resolved.status == "landed"
    assert (project / "img.bin").read_bytes() == b"\x00\x03worktree"

    # Cancellation during apply_copy_changes must wait for worker exit BEFORE _restore starts.
    _write(prepared.path / "a.txt", "updated-a\n")
    events: list[str] = []
    entered_apply = threading.Event()
    orig_apply = _copy_trees.apply_copy_changes
    orig_restore = worktrees._restore

    def slow_apply(*args: Any, **kwargs: Any) -> None:
        import time

        entered_apply.set()
        cancel_ev = kwargs.get("cancel_event")
        if cancel_ev is not None:
            cancel_ev.wait(5.0)
        # Simulate in-flight I/O continuing briefly after cancellation was requested.
        time.sleep(0.25)
        events.append("apply_exit")
        orig_apply(*args, **kwargs)

    def traced_restore(*args: Any, **kwargs: Any) -> Any:
        events.append("restore_start")
        return orig_restore(*args, **kwargs)

    monkeypatch.setattr(_copy_trees, "apply_copy_changes", slow_apply)
    monkeypatch.setattr(worktrees, "_restore", traced_restore)
    task = asyncio.create_task(manager.merge_back(prepared))
    await asyncio.to_thread(entered_apply.wait, 5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["apply_exit", "restore_start"]

    # Non-regular file (FIFO) is skipped by scan_copy_directory without opening it.
    fifo_dir = tmp_path / "fifo_proj"
    _write(fifo_dir / "pipe.fifo", "placeholder")
    orig_lstat = os.lstat

    class _FakeStat:
        def __init__(self, real: Any) -> None:
            self.st_mode = stat_mod.S_IFIFO | 0o600
            self.st_size = 0
            self.st_dev = real.st_dev
            self.st_ino = real.st_ino

    def fake_lstat(p: Any, *args: Any, **kwargs: Any) -> Any:
        real = orig_lstat(p, *args, **kwargs)
        if str(p).endswith("pipe.fifo"):
            return _FakeStat(real)
        return real

    monkeypatch.setattr(os, "lstat", fake_lstat)
    fifo_scan = _copy_trees.scan_copy_directory(fifo_dir, dependency_dirs=(), max_file_bytes=1024, hash_files=True)
    assert "pipe.fifo" in fifo_scan.skipped and "pipe.fifo" not in fifo_scan.files

    await manager.remove(prepared)


async def test_tree_work_copy_cleanup_checks_identity_and_branch_conflict_keeps_target(tmp_path: Path) -> None:
    # 1. TreeWork._remove_what_is_left refuses to delete a replacement directory with mismatched identity/token.
    project = tmp_path / "plain_gone"
    _write(project / "a.txt", "hello\n")
    host = make_host(tmp_path / "data1", None, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())
    try:
        wt_path = host.paths.worktree_dir("copyid1")
        handle = await host.task_agents.worktrees.create(project, wt_path)
        await host.ledger.add_tree(
            id=handle.id,
            project="Plain",
            project_dir=str(project),
            repo_root="",
            path=str(handle.path),
            workdir=str(handle.workdir),
            token=handle.token,
            state="unlanded",
            reason="stopped",
            lanlan_name="Lan",
        )
        # Replace the copy directory with an unrelated user directory and remove project dir.
        _copy_trees.remove_tree_safe(handle.path)
        _write(handle.path / "user_important.txt", "do not delete\n")
        shutil.rmtree(project)
        row = await host.ledger.get_tree(handle.id)
        assert row is not None
        removed = await host.task_agents._remove_what_is_left(row)
        assert removed is None
        assert (handle.path / "user_important.txt").read_text(encoding="utf-8") == "do not delete\n"
    finally:
        await host.shutdown()

    # 2. Explicit branch landing conflict must NOT auto-wake the agent into WorkingFiles landing.
    repo = _make_repo(tmp_path / "repo_br")
    host2 = make_host(tmp_path / "data2", None, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())
    try:
        wt_path2 = host2.paths.worktree_dir("brclash1")
        handle2 = await host2.task_agents.worktrees.create(repo, wt_path2)
        _write(handle2.path / "a.txt", "from-wt-1\nline2\nline3\nline4\nline5\n")
        first = await host2.task_agents.worktrees.merge_back(handle2, BranchCommit(branch="feat/clash", message="v1"))
        assert first.status == "landed"
        # Advance feat/clash externally on line 1.
        ext = tmp_path / "ext_clash"
        _git(repo, "worktree", "add", "-q", str(ext), "feat/clash")
        _write(ext / "a.txt", "from-branch-2\nline2\nline3\nline4\nline5\n")
        _git(ext, "add", "a.txt")
        _git(ext, "commit", "-q", "--no-gpg-sign", "-m", "branch edit")
        _git(repo, "worktree", "remove", "--force", str(ext))

        _write(handle2.path / "a.txt", "from-wt-2\nline2\nline3\nline4\nline5\n")
        opened = await host2.open_session("Lan", name="Demo", workdir=str(repo))
        session_id = opened["session_id"]
        comm, _ = await host2.ledger.create(
            lanlan_name="Lan", title="edit", dispatch_text="edit", user_text="edit",
            source="task_agent", route="task_agent", session_id=session_id,
        )
        await host2.ledger.transition(comm.id, "working", reason="start")
        await host2.ledger.transition(comm.id, "blocked", reason="held")
        await host2.ledger.add_tree(
            id=handle2.id,
            commission_id=comm.id,
            session_id=session_id,
            project="Demo",
            project_dir=str(repo),
            repo_root=str(handle2.repo_root),
            path=str(handle2.path),
            workdir=str(handle2.workdir),
            token=handle2.token,
            state="unlanded",
            reason="conflict",
            lanlan_name="Lan",
        )
        answer = await host2.land_worktree(handle2.id, landing=BranchCommit(branch="feat/clash", message="v2"))
        assert answer["status"] == "conflict" and answer["resolving"] is False
        assert "a.txt" in answer["conflicts"]
        assert (await host2.ledger.get_tree(handle2.id)).state == "unlanded"
    finally:
        await host2.shutdown()


async def test_current_state_pr_delivery_strips_uncommitted_and_untracked_user_files(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path / "repo")
    _write(repo / "config.py", "SETTING = 'clean'\n")
    _git(repo, "add", "config.py")
    _git(repo, "commit", "-q", "--no-gpg-sign", "-m", "add config")
    real_head = _git(repo, "rev-parse", "HEAD").strip()

    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "main", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "main")

    # User has uncommitted local edit + untracked private note in the project before delegating.
    _write(repo / "config.py", "SETTING = 'local-secret'\n")
    _write(repo / "notes.txt", "private local notes\n")

    manager = WorktreeManager()
    wt = await manager.create(repo, tmp_path / "wt" / "dirty_pr")

    # Agent only creates feature.py (does not touch config.py or notes.txt).
    _write(wt.path / "feature.py", "def run():\n    return 42\n")
    created: list[PRRequest] = []

    async def fake_pr(req: PRRequest) -> PRInfo:
        created.append(req)
        return PRInfo(url="https://github.com/org/repo/pull/55", number=55)

    res = await manager.merge_back(
        wt,
        PullRequest(branch="feat/clean-pr", remote="origin", base_branch="main", title="Add feature", create_pr=fake_pr),
    )
    assert res.status == "landed" and res.pr_number == 55 and len(created) == 1
    # Commit parent must be the real HEAD commit — never the synthetic `nekopower: worktree start` snapshot.
    assert _git(repo, "rev-parse", f"{res.commit}^").strip() == real_head
    subjects = _git(repo, "log", "--format=%s", f"{real_head}..feat/clean-pr").splitlines()
    assert subjects == ["Add feature"]
    # Pushed branch must NOT contain user's uncommitted config edit or untracked notes.txt.
    assert _git(bare, "show", "refs/heads/feat/clean-pr:config.py") == "SETTING = 'clean'\n"
    assert _git(bare, "show", "refs/heads/feat/clean-pr:feature.py") == "def run():\n    return 42\n"
    tree_files = set(_git(bare, "ls-tree", "-r", "--name-only", "refs/heads/feat/clean-pr").splitlines())
    assert "notes.txt" not in tree_files

    # A second edit on the same worktree also lands cleanly without leaking user files.
    _write(wt.path / "feature.py", "def run():\n    return 99\n")
    res2 = await manager.merge_back(
        wt,
        PullRequest(branch="feat/clean-pr", remote="origin", base_branch="main", title="Add feature", create_pr=fake_pr),
    )
    assert res2.status == "landed" and res2.pr_number == 55
    assert _git(bare, "show", "refs/heads/feat/clean-pr:config.py") == "SETTING = 'clean'\n"
    assert _git(bare, "show", "refs/heads/feat/clean-pr:feature.py") == "def run():\n    return 99\n"
    assert "notes.txt" not in set(_git(bare, "ls-tree", "-r", "--name-only", "refs/heads/feat/clean-pr").splitlines())

    # If the agent also edits the dirty file `config.py`, branch delivery conflicts instead of leaking user edits.
    wt2 = await manager.create(repo, tmp_path / "wt" / "dirty_conflict")
    _write(wt2.path / "config.py", "SETTING = 'agent-edit'\n")
    clash = await manager.merge_back(wt2, BranchCommit(branch="feat/dirty-clash", message="edit config"))
    assert clash.status == "conflict" and "config.py" in clash.conflicts
    assert clash.current == real_head
    assert _git(repo, "show-ref", "--heads", "feat/dirty-clash", check=False) == ""

    await manager.remove(wt)
    await manager.remove(wt2)


async def test_copy_oversized_new_file_kept_pending_pruned_and_compute_cancel_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    project = tmp_path / "plain_big"
    _write(project / "unchanged1.txt", "same-content-one\n")
    _write(project / "unchanged2.txt", "same-content-two\n")
    _write(project / "shared.txt", "base-line\n")
    monkeypatch.setattr(settings, "WORKTREE_UNTRACKED_MAX_BYTES", 64)

    manager = WorktreeManager()
    copy = await manager.create(project, tmp_path / "copies" / "oversize1")

    # 1. Oversized file created in copy is skipped at land, and has_changes MUST stay True afterwards.
    _write(copy.path / "small.txt", "ok\n")
    _write(copy.path / "huge.bin", "Z" * 200)
    landed = await manager.merge_back(copy)
    assert landed.status == "landed" and "huge.bin" in landed.skipped
    assert (project / "small.txt").read_text(encoding="utf-8") == "ok\n"
    assert not (project / "huge.bin").exists()
    assert await manager.has_changes(copy) is True
    again = await manager.merge_back(copy)
    assert again.status == "unchanged" and "huge.bin" in again.skipped
    assert await manager.has_changes(copy) is True
    (copy.path / "huge.bin").unlink()
    assert await manager.has_changes(copy) is False

    # 2. Repeated conflict attempts prune stale pending snapshots and do not duplicate baseline blobs.
    _write(project / "shared.txt", "project-edit\n")
    _write(copy.path / "shared.txt", "agent-edit\n")
    clash1 = await manager.merge_back(copy)
    clash2 = await manager.merge_back(copy)
    clash3 = await manager.merge_back(copy)
    assert clash1.status == "conflict" and clash2.status == "conflict" and clash3.status == "conflict"
    pending_root = _copy_trees.copy_meta_dir(copy.path) / "pending"
    pending_dirs = [p for p in pending_root.iterdir() if p.is_dir() and not p.name.startswith("prep-bak-")]
    assert len(pending_dirs) == 1 and pending_dirs[0].name == clash3.merged_tree
    pending_blobs = {p.name for p in (pending_dirs[0] / "blobs").iterdir() if p.is_file()}
    unchanged_sha = _copy_trees._hash_file(str(project / "unchanged1.txt"))
    assert unchanged_sha not in pending_blobs
    # Preparing conflicts still works cleanly using baseline blobs for unchanged files.
    prepared = await manager.prepare_conflicts(copy, clash3)
    assert (prepared.path / "unchanged1.txt").read_text(encoding="utf-8") == "same-content-one\n"
    assert "<<<<<<<" in (prepared.path / "shared.txt").read_text(encoding="utf-8")

    # 3. Cancelling merge_back during compute_copy_merge waits for the worker to exit before releasing _copy_lock.
    _write(prepared.path / "shared.txt", "resolved\n")
    events: list[str] = []
    entered = threading.Event()
    orig_compute = _copy_trees.compute_copy_merge

    def slow_compute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        entered.set()
        cancel_ev = kwargs.get("cancel_event")
        if cancel_ev is not None:
            cancel_ev.wait(5.0)
        time.sleep(0.2)
        events.append("compute_exit")
        return orig_compute(*args, **kwargs)

    monkeypatch.setattr(_copy_trees, "compute_copy_merge", slow_compute)
    task = asyncio.create_task(manager.merge_back(prepared))
    await asyncio.to_thread(entered.wait, 5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    events.append("after_cancel")
    assert events == ["compute_exit", "after_cancel"]

    await manager.remove(prepared)


async def test_discard_git_worktree_after_deleting_dotgit_on_large_project_and_restart_copy_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Deleting `.git` on a large project (> WORKTREE_COPY_MAX_FILES) must still allow discarding its git worktree.
    repo = _make_repo(tmp_path / "large_repo")
    for idx in range(5):
        _write(repo / f"f{idx}.txt", f"file {idx}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "--no-gpg-sign", "-m", "add files")

    host = make_host(tmp_path / "data_large", None, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())
    try:
        wt_path = host.paths.worktree_dir("largegit1")
        handle = await host.task_agents.worktrees.create(repo, wt_path)
        await host.ledger.add_tree(
            id=handle.id,
            project="Large",
            project_dir=str(repo),
            repo_root=str(handle.repo_root),
            path=str(handle.path),
            workdir=str(handle.workdir),
            token=handle.token,
            state="unlanded",
            reason="stopped",
            lanlan_name="Lan",
        )
        # Simulate user removing `.git` on a project larger than copy limits.
        monkeypatch.setattr(settings, "WORKTREE_COPY_MAX_FILES", 2)
        _copy_trees.remove_tree_safe(repo / ".git")
        probed = await host.task_agents.worktrees.probe(repo)
        assert probed.reason == worktrees.DIRECTORY_TOO_LARGE
        row = await host.ledger.get_tree(handle.id)
        assert row is not None
        assert await host.task_agents._repository_gone(row) is True
        discarded = await host.discard_worktree(handle.id)
        assert discarded["removed"] is True
        assert not wt_path.exists()
    finally:
        await host.shutdown()

    # 2. After restart (empty _tree_handles), _tree_kind_and_links still detects copy kind and linked deps from disk.
    plain = tmp_path / "plain_restart"
    _write(plain / "app.txt", "hi\n")
    _write(plain / "node_modules" / "pkg" / "index.js", "module.exports = 1;\n")
    host2 = make_host(tmp_path / "data_restart", None, task_agent_model=fake_model, decision_llm=FakeDecisionLLM())
    try:
        copy_path = host2.paths.worktree_dir("copyrestart1")
        handle2 = await host2.task_agents.worktrees.create(plain, copy_path)
        await host2.ledger.add_tree(
            id=handle2.id,
            project="Plain",
            project_dir=str(plain),
            repo_root="",
            path=str(handle2.path),
            workdir=str(handle2.workdir),
            token=handle2.token,
            state="unlanded",
            reason="restart",
            lanlan_name="Lan",
        )
        host2.task_agents._tree_handles.clear()
        row2 = await host2.ledger.get_tree(handle2.id)
        assert row2 is not None
        kind, linked = await host2.task_agents._tree_kind_and_links(row2)
        assert kind == "copy" and "node_modules" in linked
    finally:
        await host2.shutdown()


async def test_codex_findings_copy_link_prepare_unreadable_scan_commit_point_and_remote_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    # 1. Replacing a directory in a copy with a link/junction must refuse prepare_conflicts without writing outside.
    project = tmp_path / "plain_link"
    outside = tmp_path / "outside_target"
    _write(project / "dir" / "a.txt", "base\n")
    _write(outside / "a.txt", "outside-keep\n")
    manager = WorktreeManager()
    copy = await manager.create(project, tmp_path / "copies" / "linkprep1")
    shutil.rmtree(copy.path / "dir")
    _copy_trees.make_dir_link(outside, copy.path / "dir")
    _write(project / "dir" / "a.txt", "project-updated\n")
    clash = await manager.merge_back(copy)
    assert clash.status == "conflict" and "dir/a.txt" in clash.conflicts
    with pytest.raises(WorktreeError, match="directory link"):
        await manager.prepare_conflicts(copy, clash)
    assert (outside / "a.txt").read_text(encoding="utf-8") == "outside-keep\n"
    await manager.remove(copy)

    # 2. Unreadable directory during os.walk must abort copy scan/merge instead of deleting project files.
    project2 = tmp_path / "plain_unreadable"
    _write(project2 / "dir" / "keep.txt", "precious\n")
    copy2 = await manager.create(project2, tmp_path / "copies" / "unreadable1")
    orig_walk = os.walk

    def failing_walk(top: Any, *args: Any, onerror: Any = None, **kwargs: Any) -> Any:
        if os.path.normcase(os.path.abspath(str(top).removeprefix("\\\\?\\"))) == os.path.normcase(
            os.path.abspath(str(copy2.path))
        ):
            err = PermissionError(13, "Permission denied", str(copy2.path / "dir"))
            if onerror is not None:
                onerror(err)
            return iter(())
        return orig_walk(top, *args, onerror=onerror, **kwargs)

    monkeypatch.setattr(os, "walk", failing_walk)
    failed_merge = await manager.merge_back(copy2)
    assert failed_merge.status == "failed" and "reading directory failed" in failed_merge.detail
    assert (project2 / "dir" / "keep.txt").read_text(encoding="utf-8") == "precious\n"
    assert await manager.has_changes(copy2) is True
    monkeypatch.setattr(os, "walk", orig_walk)

    # 3. Cancelling merge_back while advance_copy_baseline runs keeps project files and baseline consistent.
    _write(copy2.path / "dir" / "keep.txt", "landed-v2\n")
    entered_adv = threading.Event()
    orig_advance = _copy_trees.advance_copy_baseline

    def slow_advance(*args: Any, **kwargs: Any) -> None:
        entered_adv.set()
        time.sleep(0.25)
        orig_advance(*args, **kwargs)

    monkeypatch.setattr(_copy_trees, "advance_copy_baseline", slow_advance)
    land_task = asyncio.create_task(manager.merge_back(copy2))
    await asyncio.to_thread(entered_adv.wait, 5.0)
    land_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await land_task
    assert (project2 / "dir" / "keep.txt").read_text(encoding="utf-8") == "landed-v2\n"
    assert await manager.has_changes(copy2) is False
    monkeypatch.setattr(_copy_trees, "advance_copy_baseline", orig_advance)
    await manager.remove(copy2)

    # 4. When an owned branch advanced locally with the same edit as the worktree, BranchCommit(remote=...) still pushes.
    local, bare, _other = _make_repo_with_remote(tmp_path / "codex_push")
    wt = await manager.create(local, tmp_path / "wt" / "push_same", RemoteDefaultBranch(fetch=False))
    _write(wt.path / "a.txt", "v1\nline2\nline3\nline4\nline5\n")
    first = await manager.merge_back(wt, BranchCommit(branch="feat/sync", remote="origin", message="v1"))
    assert first.status == "landed" and first.commit

    # Advance feat/sync locally (without pushing) with v2, and make the identical v2 edit in the worktree.
    ext = tmp_path / "ext_sync"
    _git(local, "worktree", "add", "-q", str(ext), "feat/sync")
    _write(ext / "a.txt", "v2\nline2\nline3\nline4\nline5\n")
    _git(ext, "add", "a.txt")
    _git(ext, "commit", "-q", "--no-gpg-sign", "-m", "local v2")
    local_v2 = _git(ext, "rev-parse", "HEAD").strip()
    _git(local, "worktree", "remove", "--force", str(ext))

    _write(wt.path / "a.txt", "v2\nline2\nline3\nline4\nline5\n")
    second = await manager.merge_back(wt, BranchCommit(branch="feat/sync", remote="origin", message="v2"))
    assert second.status == "landed" and second.commit == local_v2
    assert _git(bare, "rev-parse", "refs/heads/feat/sync").strip() == local_v2
    assert await manager.has_changes(wt) is False
    await manager.remove(wt)


async def test_coderabbit_hardening_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = WorktreeManager()

    # 1. Replacing a baseline directory `docs/a.txt` with a regular file `docs` lands cleanly.
    proj_dir = tmp_path / "dir_to_file"
    _write(proj_dir / "docs" / "a.txt", "old doc\n")
    cp = await manager.create(proj_dir, tmp_path / "copies" / "d2f1")
    shutil.rmtree(cp.path / "docs")
    _write(cp.path / "docs", "now a plain file\n")
    landed = await manager.merge_back(cp)
    assert landed.status == "landed", landed.detail
    assert (proj_dir / "docs").is_file() and (proj_dir / "docs").read_text(encoding="utf-8") == "now a plain file\n"
    await manager.remove(cp)

    # 2. Stale copy handle from project A must be rejected when path was reused for project B.
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    _write(proj_a / "same.txt", "shared\n")
    _write(proj_b / "same.txt", "shared\n")
    shared_path = tmp_path / "copies" / "reused1"
    handle_a = await manager.create(proj_a, shared_path)
    await manager.remove(handle_a)
    handle_b = await manager.create(proj_b, shared_path)
    _write(handle_b.path / "from_b.txt", "secret B\n")
    stale_land = await manager.merge_back(handle_a)
    assert stale_land.status == "failed"
    assert not (proj_a / "from_b.txt").exists()
    await manager.remove(handle_b)

    # 3. unlink_dependencies refuses to remove a link through a linked parent directory.
    outside_parent = tmp_path / "moved_sub"
    outside_dep = tmp_path / "real_dep"
    outside_parent.mkdir()
    outside_dep.mkdir()
    _copy_trees.make_dir_link(outside_dep, outside_parent / "node_modules")
    fake_wt = tmp_path / "fake_wt"
    fake_wt.mkdir()
    _copy_trees.make_dir_link(outside_parent, fake_wt / "sub")
    assert _copy_trees.unlink_dependencies(fake_wt, ["sub/node_modules"]) == []
    assert _copy_trees.is_dir_link(outside_parent / "node_modules")

    # 4. _store_blob_file rejects mismatched content hash and deletes temp file.
    src_file = tmp_path / "blob_src.txt"
    _write(src_file, "actual-v2\n")
    blobs_dir = tmp_path / "blobs"
    with pytest.raises(OSError, match="changed while storing blob"):
        _copy_trees._store_blob_file(src_file, "0" * 64, blobs_dir)
    assert not (blobs_dir / ("0" * 64)).exists()
    assert list(blobs_dir.glob("*.tmp")) == []

    # 5. _create_copy refuses an existing regular file at target without deleting it.
    race_target = tmp_path / "copies" / "occupied_file"
    _write(race_target, "keep this file\n")
    with pytest.raises(WorktreeError):
        await manager.create(proj_a, race_target)
    assert race_target.is_file() and race_target.read_text(encoding="utf-8") == "keep this file\n"

    # 6. Diff budget caps quadratic work on huge files and embeds whole-file conflict markers in O(N).
    monkeypatch.setattr(_copy_trees, "_MAX_DIFF_LINES", 20)
    big_base = b"dup\n" * 30
    big_curr = b"dup\n" * 29 + b"curr\n"
    big_wt = b"dup\n" * 29 + b"wt\n"
    assert _copy_trees.line_diff_counts(big_base, big_wt) == (None, None)
    merged_big, clashed_big = _copy_trees.merge3_bytes(big_base, big_curr, big_wt)
    assert clashed_big is True and merged_big is not None
    assert b"<<<<<<< project\n" in merged_big and b">>>>>>> worktree\n" in merged_big
    assert big_curr in merged_big and big_wt in merged_big

    # 7. Atomic note writes preserve prior content on replace failure; failed copy into an existing
    # empty target without birth time still cleans up partial files.
    note_dir = tmp_path / "atomic_note"
    note_dir.mkdir()
    note_file = note_dir / "note.json"
    _copy_trees._atomic_write_json(note_file, {"branch": "feat/owned"})
    orig_replace = os.replace

    def fail_replace(src: Any, dst: Any) -> None:
        if str(dst).endswith("note.json"):
            raise OSError("simulated replace failure")
        orig_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        _copy_trees._atomic_write_json(note_file, {"branch": "feat/corrupt"})
    monkeypatch.setattr(os, "replace", orig_replace)
    assert json.loads(note_file.read_text(encoding="utf-8")) == {"branch": "feat/owned"}
    assert list(note_dir.glob("*.tmp")) == []

    empty_target = tmp_path / "copies" / "empty_no_birth"
    empty_target.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "_directory_identity", lambda _p: None)
    orig_pop = _copy_trees.populate_copy

    def fail_mid_copy(src: Path, dst: Path, *args: Any, **kwargs: Any) -> Any:
        _write(dst / "partial.txt", "leftover\n")
        raise OSError("simulated mid-copy failure")

    monkeypatch.setattr(_copy_trees, "populate_copy", fail_mid_copy)
    with pytest.raises(OSError, match="simulated mid-copy failure"):
        await manager.create(proj_a, empty_target)
    monkeypatch.setattr(_copy_trees, "populate_copy", orig_pop)
    assert empty_target.is_dir() and list(empty_target.iterdir()) == []


@pytest.mark.skipif(REAL_GIT_TOO_OLD, reason="git missing or below minimum version")
async def test_coderabbit_git_hardening_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = WorktreeManager()

    # 1. pushurl default branch protection, PR default base from pushurl, multi-pushurl rejection,
    # pending PR durability across both exceptions and cancellation, and strict has_pending_delivery errors.
    local, bare_fetch, _other = _make_repo_with_remote(tmp_path / "cr_pushurl", default_branch="main")
    bare_push = tmp_path / "cr_push_target.git"
    _git(tmp_path, "init", "--bare", "-q", "-b", "develop", str(bare_push))
    _git(local, "push", "-q", str(bare_push), "HEAD:refs/heads/develop")
    _git(local, "remote", "set-url", "--push", "origin", str(bare_push))

    wt_pu = await manager.create(local, tmp_path / "wt" / "pu1", RemoteDefaultBranch(fetch=False))
    _write(wt_pu.path / "a.txt", "changed\nline2\nline3\nline4\nline5\n")
    blocked = await manager.merge_back(wt_pu, BranchCommit(branch="develop", remote="origin", message="nope"))
    assert blocked.status == "failed" and "protected default branch" in blocked.detail

    # Omitting base_branch when pushurl differs from fetch URL uses the push target's default branch (`develop`).
    seen_bases: list[str] = []

    async def capture_pr_base(req: PRRequest) -> PRInfo:
        seen_bases.append(req.base_branch)
        return PRInfo(url="https://github.com/org/push-repo/pull/12", number=12)

    pr_push_default = await manager.merge_back(
        wt_pu, PullRequest(title="Push default base", branch="feat/push-base", remote="origin", create_pr=capture_pr_base)
    )
    assert pr_push_default.status == "landed" and seen_bases == ["develop"]

    # Explicit base_branch overrides push target default branch.
    _write(wt_pu.path / "a.txt", "changed-v2\nline2\nline3\nline4\nline5\n")
    pr_explicit_base = await manager.merge_back(
        wt_pu,
        PullRequest(
            title="Explicit base",
            branch="feat/push-base",
            remote="origin",
            base_branch="release",
            create_pr=capture_pr_base,
        ),
    )
    assert pr_explicit_base.status == "landed" and seen_bases == ["develop", "release"]

    # Multiple push URLs configured -> refused before touching refs.
    _git(local, "remote", "set-url", "--add", "--push", "origin", str(bare_fetch))
    multi = await manager.merge_back(wt_pu, BranchCommit(branch="feat/multi", remote="origin", message="nope"))
    assert multi.status == "failed" and "multiple push URLs" in multi.detail

    # Restore single push URL; cancellation OR failure during PR creation leaves has_pending_delivery=True.
    _git(local, "config", "--unset-all", "remote.origin.pushurl")
    wt_pr = await manager.create(local, tmp_path / "wt" / "pu2", RemoteDefaultBranch(fetch=False))
    _write(wt_pr.path / "a.txt", "retry-pr\nline2\nline3\nline4\nline5\n")

    pr_started = asyncio.Event()

    async def hang_pr(_req: PRRequest) -> PRInfo:
        pr_started.set()
        await asyncio.sleep(60)
        return PRInfo(url="never", number=0)

    pr_task = asyncio.create_task(
        manager.merge_back(
            wt_pr, PullRequest(title="Cancelled PR", branch="feat/retry-pr", remote="origin", base_branch="main", create_pr=hang_pr)
        )
    )
    wait_started = asyncio.create_task(pr_started.wait())
    try:
        await asyncio.wait({pr_task, wait_started}, timeout=30.0, return_when=asyncio.FIRST_COMPLETED)
        assert pr_started.is_set(), (
            f"create_pr never started; pr_task done={pr_task.done()} result={pr_task.result() if pr_task.done() else None}"
        )
        pr_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pr_task
    finally:
        for task in (wait_started, pr_task):
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
    assert await manager.has_pending_delivery(wt_pr) is True

    async def fail_pr(_req: PRRequest) -> PRInfo:
        raise RuntimeError("transient API outage")

    failed_pr = await manager.merge_back(
        wt_pr, PullRequest(title="Retryable PR", branch="feat/retry-pr", remote="origin", base_branch="main", create_pr=fail_pr)
    )
    assert failed_pr.status == "failed" and "creating the pull request failed" in failed_pr.detail
    assert await manager.has_pending_delivery(wt_pr) is True

    # has_pending_delivery must propagate git/read errors so cleanup stays fail-closed.
    orig_git = manager._git

    async def fail_rev_parse(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if "--git-common-dir" in args:
            raise GitError(list(args), 1, b"simulated git failure")
        return await orig_git(cwd, *args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(manager, "_git", fail_rev_parse)
        with pytest.raises(GitError):
            await manager.has_pending_delivery(wt_pr)

    # Orphan reconcile must keep a clean worktree that still has pr_pending=True.
    rec_pending = await manager.reconcile(local, tmp_path / "wt", known=[wt_pu.id])
    assert wt_pr.path in rec_pending.kept and wt_pr.id not in rec_pending.removed

    async def ok_pr(_req: PRRequest) -> PRInfo:
        return PRInfo(url="https://github.com/org/repo/pull/77", number=77)

    ok_res = await manager.merge_back(
        wt_pr, PullRequest(title="Retryable PR", branch="feat/retry-pr", remote="origin", base_branch="main", create_pr=ok_pr)
    )
    assert ok_res.status == "landed" and ok_res.pr_number == 77
    assert await manager.has_pending_delivery(wt_pr) is False
    await manager.remove(wt_pu)
    await manager.remove(wt_pr)


async def test_coderabbit_and_greptile_copy_followup_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-git copy recovery, diff budget, alignment, xattr, and claim-race tests (independent of git)."""
    from nekopower.host.tree_work import _has_markers

    manager = WorktreeManager()

    # 1. Small-file alignment is preserved (full SequenceMatcher runs when within pair-work budget).
    merged_small, clashed_small = _copy_trees.merge3_bytes(b"A\nB\nA\n", b"B\nA\nA\n", b"A\nX\nA\n")
    assert clashed_small is False and merged_small == b"X\nA\nA\n"

    # 2. Diff budget short-circuits BEFORE calling SequenceMatcher on both repeated lines and
    # pairwise-swapped unique lines, embedding whole-file conflict markers in O(N).
    rep_base = b"top-0\n" + b"dup\n" * 6000 + b"bot-0\n"
    rep_curr = b"top-c\n" + b"dup\n" * 6000 + b"bot-c\n"
    rep_wt = b"top-w\n" + b"dup\n" * 6000 + b"bot-w\n"
    uniq_lines = [f"line-{i}\n".encode() for i in range(8000)]
    swap_base = b"".join(uniq_lines)
    swap_curr = b"".join(uniq_lines[i ^ 1] for i in range(8000))
    swap_wt = b"".join(reversed(uniq_lines))

    def forbid_matcher(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("SequenceMatcher must not be called when diff budget is exceeded")

    with monkeypatch.context() as mp:
        mp.setattr(difflib, "SequenceMatcher", forbid_matcher)
        assert _copy_trees.line_diff_counts(rep_base, rep_wt) == (None, None)
        merged_rep, clashed_rep = _copy_trees.merge3_bytes(rep_base, rep_curr, rep_wt)
        assert _copy_trees.line_diff_counts(swap_base, swap_curr) == (None, None)
        merged_swap, clashed_swap = _copy_trees.merge3_bytes(swap_base, swap_curr, swap_wt)
    assert clashed_rep is True and merged_rep is not None
    assert b"<<<<<<< project\n" in merged_rep and b">>>>>>> worktree\n" in merged_rep
    assert clashed_swap is True and merged_swap is not None
    assert b"<<<<<<< project\n" in merged_swap and b">>>>>>> worktree\n" in merged_swap
    marker_file = tmp_path / "budget_markers.txt"
    marker_file.write_bytes(merged_rep)
    assert _has_markers(marker_file) is True

    # 3. Baseline inconsistency (meta.start != base_manifest.digest) rejects clean merges safely.
    proj_inc = tmp_path / "inc_proj"
    _write(proj_inc / "a.txt", "v1\n")
    cp_inc = await manager.create(proj_inc, tmp_path / "copies" / "inc1")
    meta_inc_file = _copy_trees.copy_meta_dir(cp_inc.path) / "meta.json"
    meta_inc = json.loads(meta_inc_file.read_text(encoding="utf-8"))
    meta_inc["start"] = "0" * 64
    _copy_trees._atomic_write_json(meta_inc_file, meta_inc)
    inc_res = await manager.merge_back(cp_inc)
    assert inc_res.status == "failed" and "inconsistent" in inc_res.detail
    await manager.remove(cp_inc)

    # 4. Pre-mutation marker failure leaves copy untouched; triple-fault rollback failure still blocks landing.
    proj_rb = tmp_path / "rb_proj"
    _write(proj_rb / "a.txt", "base\n")
    cp_rb = await manager.create(proj_rb, tmp_path / "copies" / "rb1")
    _write(cp_rb.path / "a.txt", "worktree-edit\n")
    _write(proj_rb / "a.txt", "project-edit\n")
    clash_rb = await manager.merge_back(cp_rb)
    assert clash_rb.status == "conflict"
    orig_awb = _copy_trees._atomic_write_bytes

    def fail_pre_marker(path: Path, data: bytes) -> None:
        if path.name == "rollback_incomplete.json":
            raise OSError("disk refused pre-mutation marker")
        orig_awb(path, data)

    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "_atomic_write_bytes", fail_pre_marker)
        with pytest.raises(OSError, match="disk refused pre-mutation marker"):
            await manager.prepare_conflicts(cp_rb, clash_rb)
    assert (cp_rb.path / "a.txt").read_text(encoding="utf-8") == "worktree-edit\n"

    # Now fail meta.json write, base_manifest restore, AND rollback marker rewrite (triple fault):
    # the pre-mutation marker already on disk must still block subsequent merges and prepares.
    base_writes = {"count": 0}
    marker_writes = {"count": 0}

    def fail_triple_fault(path: Path, data: bytes) -> None:
        if path.name == "meta.json":
            raise OSError("simulated meta write failure")
        if path.name == "base_manifest.json":
            base_writes["count"] += 1
            if base_writes["count"] >= 2:
                raise OSError("simulated base restore failure")
        if path.name == "rollback_incomplete.json":
            marker_writes["count"] += 1
            if marker_writes["count"] >= 2:
                raise OSError("simulated marker rewrite failure")
        orig_awb(path, data)

    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "_atomic_write_bytes", fail_triple_fault)
        with pytest.raises(OSError, match="simulated meta write failure"):
            await manager.prepare_conflicts(cp_rb, clash_rb)
    pending_root = _copy_trees.copy_meta_dir(cp_rb.path) / "pending"
    bak_dirs = [p for p in pending_root.iterdir() if p.name.startswith("prep-bak-")]
    assert len(bak_dirs) == 1
    assert (bak_dirs[0] / "old_base_manifest.json").is_file()
    assert (bak_dirs[0] / "old_meta.json").is_file()
    marker_payload = json.loads((bak_dirs[0] / "rollback_incomplete.json").read_text(encoding="utf-8"))
    assert isinstance(marker_payload.get("entries"), list) and any(e["rel"] == "a.txt" for e in marker_payload["entries"])
    blocked_land = await manager.merge_back(cp_rb)
    assert blocked_land.status == "failed" and "incomplete rollback" in blocked_land.detail
    with pytest.raises(WorktreeError, match="incomplete rollback"):
        await manager.prepare_conflicts(cp_rb, clash_rb)
    await manager.remove(cp_rb)

    # A failed backup-dir removal after a clean prepare_conflicts still unlinks the marker first,
    # so the healthy copy is not falsely blocked on subsequent merge_back.
    proj_clean = tmp_path / "clean_bak_proj"
    _write(proj_clean / "a.txt", "base\n")
    cp_clean = await manager.create(proj_clean, tmp_path / "copies" / "cb1")
    _write(cp_clean.path / "a.txt", "wt-line\n")
    _write(proj_clean / "a.txt", "proj-line\n")
    clash_clean = await manager.merge_back(cp_clean)
    assert clash_clean.status == "conflict"
    orig_rm = _copy_trees.remove_tree_safe

    def fail_prep_bak_rm(path: Path | str) -> None:
        if "prep-bak-" in Path(path).name:
            raise OSError("simulated locked backup file")
        orig_rm(path)

    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "remove_tree_safe", fail_prep_bak_rm)
        await manager.prepare_conflicts(cp_clean, clash_clean)
    _write(cp_clean.path / "a.txt", "resolved\n")
    after_locked_rm = await manager.merge_back(cp_clean)
    assert after_locked_rm.status == "landed", after_locked_rm.detail
    await manager.remove(cp_clean)

    # 5. xattr read OSError returns "unknown" (preserving copy + metadata), while ENODATA returns "theirs".
    proj_xa = tmp_path / "xa_proj"
    _write(proj_xa / "a.txt", "v1\n")
    cp_xa = await manager.create(proj_xa, tmp_path / "copies" / "xa1")
    meta_xa_file = _copy_trees.copy_meta_dir(cp_xa.path) / "meta.json"
    meta_xa = json.loads(meta_xa_file.read_text(encoding="utf-8"))
    meta_xa.pop("identity", None)
    meta_xa["xattr"] = True
    _copy_trees._atomic_write_json(meta_xa_file, meta_xa)
    with monkeypatch.context() as mp:
        mp.setattr(worktrees, "_directory_identity", lambda _p: None)

        def fail_getxattr(*_args: Any, **_kwargs: Any) -> bytes:
            raise OSError(errno.EIO, "transient xattr error")

        mp.setattr(os, "getxattr", fail_getxattr, raising=False)
        assert _copy_trees.copy_matches_identity(cp_xa.path, meta_xa, None) == "unknown"
        with pytest.raises(WorktreeError):
            await manager.remove(cp_xa)
        assert cp_xa.path.is_dir() and meta_xa_file.is_file()

        enodata = getattr(errno, "ENODATA", None) or getattr(errno, "ENOATTR", None)
        if enodata is not None:
            def missing_getxattr(*_args: Any, **_kwargs: Any) -> bytes:
                raise OSError(enodata, "no attribute")

            mp.setattr(os, "getxattr", missing_getxattr, raising=False)
            assert _copy_trees.copy_matches_identity(cp_xa.path, meta_xa, None) == "theirs"
    _copy_trees.remove_tree_safe(cp_xa.path)
    _copy_trees.remove_tree_safe(_copy_trees.copy_meta_dir(cp_xa.path))

    # 6. Claim-time file race, post-claim target swap, and cancel during claim cleanup.
    proj_rc = tmp_path / "rc_proj"
    _write(proj_rc / "a.txt", "v1\n")
    race_file_target = tmp_path / "copies" / "claim_race_file"
    orig_claim = worktrees._claim_directory

    def race_create_file(path: Path) -> bool:
        if path == race_file_target:
            _write(path, "raced regular file\n")
            return False
        return orig_claim(path)

    with monkeypatch.context() as mp:
        mp.setattr(worktrees, "_claim_directory", race_create_file)
        with pytest.raises(WorktreeError, match="not an empty directory"):
            await manager.create(proj_rc, race_file_target)
    assert race_file_target.is_file() and race_file_target.read_text(encoding="utf-8") == "raced regular file\n"

    swap_target = tmp_path / "copies" / "new_target_swapped"
    orig_pop = _copy_trees.populate_copy

    def swap_during_populate(src: Path, dst: Path, *args: Any, **kwargs: Any) -> Any:
        if dst == swap_target:
            moved = tmp_path / "copies" / "moved_aside_orig"
            dst.rename(moved)
            dst.mkdir()
            _write(dst / "foreign.txt", "do not delete\n")
            raise OSError("simulated populate failure after swap")
        return orig_pop(src, dst, *args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "populate_copy", swap_during_populate)
        with pytest.raises(OSError, match="simulated populate failure after swap"):
            await manager.create(proj_rc, swap_target)
    assert (swap_target / "foreign.txt").read_text(encoding="utf-8") == "do not delete\n"

    # 7. Two-phase prepare_conflicts rollback restores file `docs` replaced by `docs/a.txt`.
    proj_fd = tmp_path / "file_to_dir_rb"
    _write(proj_fd / "docs", "orig-docs\n")
    _write(proj_fd / "clash.txt", "base\n")
    cp_fd = await manager.create(proj_fd, tmp_path / "copies" / "fd_rb1")
    _write(cp_fd.path / "clash.txt", "wt-clash\n")
    (proj_fd / "docs").unlink()
    _write(proj_fd / "docs" / "a.txt", "nested-doc\n")
    _write(proj_fd / "clash.txt", "proj-clash\n")
    clash_fd = await manager.merge_back(cp_fd)
    assert clash_fd.status == "conflict"

    def fail_meta_only(path: Path, data: bytes) -> None:
        if path.name == "meta.json":
            raise OSError("simulated meta write failure for file-to-dir rollback")
        orig_awb(path, data)

    def make_fail_meta_once(msg: str) -> Any:
        failed = {"done": False}

        def _hook(path: Path, data: bytes) -> None:
            if path.name == "meta.json" and not failed["done"]:
                failed["done"] = True
                raise OSError(msg)
            orig_awb(path, data)

        return _hook

    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "_atomic_write_bytes", make_fail_meta_once("simulated meta write failure for file-to-dir rollback"))
        with pytest.raises(OSError, match="simulated meta write failure for file-to-dir rollback"):
            await manager.prepare_conflicts(cp_fd, clash_fd)
    assert (cp_fd.path / "docs").is_file() and (cp_fd.path / "docs").read_text(encoding="utf-8") == "orig-docs\n"
    assert (cp_fd.path / "clash.txt").read_text(encoding="utf-8") == "wt-clash\n"
    assert not _copy_trees._has_incomplete_rollback(_copy_trees.copy_meta_dir(cp_fd.path))
    await manager.remove(cp_fd)

    # 7b. Reverse replacement: nested dir `docs/sub/a.txt` replaced by regular file `docs` prunes
    # emptied subdirs so prepare_conflicts succeeds, rolls back cleanly if meta.json fails, and
    # never deletes unmerged contents if a subdirectory is non-empty.
    proj_df = tmp_path / "dir_to_file_rb"
    _write(proj_df / "docs" / "sub" / "a.txt", "nested-orig\n")
    _write(proj_df / "clash.txt", "base\n")
    cp_df = await manager.create(proj_df, tmp_path / "copies" / "df_rb1")
    _write(cp_df.path / "clash.txt", "wt-clash\n")
    shutil.rmtree(proj_df / "docs")
    _write(proj_df / "docs", "project-file-docs\n")
    _write(proj_df / "clash.txt", "proj-clash\n")
    clash_df = await manager.merge_back(cp_df)
    assert clash_df.status == "conflict"

    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "_atomic_write_bytes", make_fail_meta_once("simulated meta write failure for dir-to-file rollback"))
        with pytest.raises(OSError, match="simulated meta write failure for dir-to-file rollback"):
            await manager.prepare_conflicts(cp_df, clash_df)
    assert (cp_df.path / "docs").is_dir()
    assert (cp_df.path / "docs" / "sub" / "a.txt").read_text(encoding="utf-8") == "nested-orig\n"
    assert (cp_df.path / "clash.txt").read_text(encoding="utf-8") == "wt-clash\n"
    assert not _copy_trees._has_incomplete_rollback(_copy_trees.copy_meta_dir(cp_df.path))

    # Without fault injection, empty `docs/sub` is pruned so `docs` can become a regular file cleanly.
    await manager.prepare_conflicts(cp_df, clash_df)
    assert (cp_df.path / "docs").is_file()
    assert (cp_df.path / "docs").read_text(encoding="utf-8") == "project-file-docs\n"
    await manager.remove(cp_df)

    # Unmerged (skipped oversized) content inside `docs/sub/` blocks replacing `docs` with a file
    # and survives rollback untouched alongside restored `docs/sub/a.txt`.
    proj_df_keep = tmp_path / "dir_to_file_keep"
    _write(proj_df_keep / "docs" / "sub" / "a.txt", "nested-keep\n")
    _write(proj_df_keep / "clash.txt", "base\n")
    with monkeypatch.context() as mp:
        mp.setattr(settings, "WORKTREE_UNTRACKED_MAX_BYTES", 64)
        cp_df_keep = await manager.create(proj_df_keep, tmp_path / "copies" / "df_keep1")
        _write(cp_df_keep.path / "docs" / "sub" / "unmerged.bin", "Z" * 200)
        _write(cp_df_keep.path / "clash.txt", "wt-clash\n")
        shutil.rmtree(proj_df_keep / "docs")
        _write(proj_df_keep / "docs", "project-file-docs\n")
        _write(proj_df_keep / "clash.txt", "proj-clash\n")
        clash_df_keep = await manager.merge_back(cp_df_keep)
        assert clash_df_keep.status == "conflict"
        with pytest.raises(OSError):
            await manager.prepare_conflicts(cp_df_keep, clash_df_keep)
    assert (cp_df_keep.path / "docs" / "sub" / "unmerged.bin").read_text(encoding="utf-8") == "Z" * 200
    assert (cp_df_keep.path / "docs" / "sub" / "a.txt").read_text(encoding="utf-8") == "nested-keep\n"
    await manager.remove(cp_df_keep)

    # 8. If advance_copy_baseline fails writing meta.json after project files landed, keeping
    # base_manifest.json (source="landed") lets compute_copy_merge self-heal meta.start without
    # replaying the already-landed edit over a user's subsequent undo.
    proj_adv = tmp_path / "adv_rb"
    _write(proj_adv / "a.txt", "v1\n")
    cp_adv = await manager.create(proj_adv, tmp_path / "copies" / "adv1")
    _write(cp_adv.path / "a.txt", "v2\n")
    orig_meta_start = _copy_trees.read_copy_meta(cp_adv.path)["start"]  # type: ignore[index]
    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "_atomic_write_bytes", fail_meta_only)
        with pytest.raises(OSError, match="simulated meta write failure"):
            await manager.merge_back(cp_adv)
    # base_manifest stays at the landed digest (not rolled back to orig_meta_start).
    landed_digest = _copy_trees.read_base_manifest(cp_adv.path)["digest"]  # type: ignore[index]
    assert landed_digest != orig_meta_start
    assert _copy_trees.read_copy_meta(cp_adv.path)["start"] == orig_meta_start  # type: ignore[index]
    # User undoes a.txt back to v1 in project_dir; retry self-heals meta.start and preserves undo.
    _write(proj_adv / "a.txt", "v1\n")
    retry_adv = await manager.merge_back(cp_adv)
    assert retry_adv.status == "unchanged"
    assert (proj_adv / "a.txt").read_text(encoding="utf-8") == "v1\n"
    assert _copy_trees.read_copy_meta(cp_adv.path)["start"] == landed_digest  # type: ignore[index]
    # A genuinely new edit in the copy still lands cleanly afterwards (detecting conflict with undo).
    _write(cp_adv.path / "a.txt", "v3\n")
    clash_after_undo = await manager.merge_back(cp_adv)
    assert clash_after_undo.status == "conflict" and "a.txt" in clash_after_undo.conflicts
    await manager.remove(cp_adv)

    # 9. Turning a baseline directory into a nested Git repository in the working copy skips it
    # instead of deleting the project's existing files (on clean landing, no-op landing, and conflict prep).
    proj_ng = tmp_path / "nested_git_copy"
    _write(proj_ng / "sub" / "keep.txt", "keep-in-project\n")
    _write(proj_ng / "other.txt", "base\n")
    cp_ng = await manager.create(proj_ng, tmp_path / "copies" / "ng1")
    (cp_ng.path / "sub" / ".git").mkdir()
    # No other edits: must be unchanged (never delete project/sub/keep.txt) and report sub/ skipped.
    noop_ng = await manager.merge_back(cp_ng)
    assert noop_ng.status == "unchanged" and "sub/" in noop_ng.skipped
    assert (proj_ng / "sub" / "keep.txt").read_text(encoding="utf-8") == "keep-in-project\n"
    # Unrelated conflict on other.txt: prepare_conflicts must succeed without overwriting copy/sub/.git,
    # and resolving other.txt must land without deleting project/sub/keep.txt.
    _write(cp_ng.path / "other.txt", "wt-other\n")
    _write(proj_ng / "other.txt", "proj-other\n")
    clash_ng = await manager.merge_back(cp_ng)
    assert clash_ng.status == "conflict" and clash_ng.conflicts == ("other.txt",) and "sub/" in clash_ng.skipped
    await manager.prepare_conflicts(cp_ng, clash_ng)
    assert (cp_ng.path / "sub" / ".git").is_dir()
    _write(cp_ng.path / "other.txt", "resolved-other\n")
    landed_ng = await manager.merge_back(cp_ng)
    assert landed_ng.status == "landed" and "sub/" in landed_ng.skipped
    assert (proj_ng / "other.txt").read_text(encoding="utf-8") == "resolved-other\n"
    assert (proj_ng / "sub" / "keep.txt").read_text(encoding="utf-8") == "keep-in-project\n"
    await manager.remove(cp_ng)

    # 10. Non-execute POSIX permission preservation helper + stale conflict snapshot rejection +
    # pre-staged landing_intent blocking replay after base_manifest failure + _remove_copy preserving
    # recovery data when meta.json is corrupt/unreadable.
    assert _copy_trees._merged_file_mode(0o600, 0o644) == 0o600
    assert _copy_trees._merged_file_mode(0o600, 0o755) == 0o700
    assert _copy_trees._merged_file_mode(0o750, 0o644) == 0o640
    assert _copy_trees._merged_file_mode(None, 0o644) == 0o644
    if sys.platform != "win32":
        proj_perm = tmp_path / "perm_proj"
        _write(proj_perm / "secret.txt", "v1\n")
        os.chmod(proj_perm / "secret.txt", 0o644)
        cp_perm = await manager.create(proj_perm, tmp_path / "copies" / "perm1")
        os.chmod(proj_perm / "secret.txt", 0o600)
        _write(cp_perm.path / "secret.txt", "v2\n")
        land_perm = await manager.merge_back(cp_perm)
        assert land_perm.status == "landed", land_perm.detail
        assert (os.lstat(proj_perm / "secret.txt").st_mode & 0o777) == 0o600
        await manager.remove(cp_perm)

    # Stale conflict snapshot must be rejected when base_manifest already advanced even if meta.start lagged.
    proj_stale = tmp_path / "stale_conflict_proj"
    _write(proj_stale / "a.txt", "base\n")
    cp_stale = await manager.create(proj_stale, tmp_path / "copies" / "stale1")
    _write(cp_stale.path / "a.txt", "W\n")
    _write(proj_stale / "a.txt", "P\n")
    old_clash = await manager.merge_back(cp_stale)
    assert old_clash.status == "conflict"
    # User sets project to W; merge_back advances base_manifest.json, fails on meta.json.
    _write(proj_stale / "a.txt", "W\n")
    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "_atomic_write_bytes", fail_meta_only)
        with pytest.raises(OSError, match="simulated meta write failure"):
            await manager.merge_back(cp_stale)
    with pytest.raises(WorktreeError, match="landed or was prepared"):
        await manager.prepare_conflicts(cp_stale, old_clash)
    assert (cp_stale.path / "a.txt").read_text(encoding="utf-8") == "W\n"
    await manager.remove(cp_stale)

    # If base_manifest.json write fails after project files landed, pre-staged landing_intent.json
    # blocks retrying from replaying stale diffs over a user's subsequent undo.
    proj_intent = tmp_path / "intent_block_proj"
    _write(proj_intent / "a.txt", "v1\n")
    cp_intent = await manager.create(proj_intent, tmp_path / "copies" / "intent1")
    _write(cp_intent.path / "a.txt", "v2\n")

    def fail_base_manifest(path: Path, data: bytes) -> None:
        if path.name == "base_manifest.json":
            raise OSError("simulated base_manifest write failure")
        orig_awb(path, data)

    with monkeypatch.context() as mp:
        mp.setattr(_copy_trees, "_atomic_write_bytes", fail_base_manifest)
        with pytest.raises(OSError, match="simulated base_manifest write failure"):
            await manager.merge_back(cp_intent)
    assert (proj_intent / "a.txt").read_text(encoding="utf-8") == "v2\n"
    _write(proj_intent / "a.txt", "v1\n")
    blocked_intent = await manager.merge_back(cp_intent)
    assert blocked_intent.status == "failed" and "unfinalized landing" in blocked_intent.detail
    assert (proj_intent / "a.txt").read_text(encoding="utf-8") == "v1\n"
    await manager.remove(cp_intent)

    # _remove_copy must preserve copy and recovery metadata when meta.json is corrupt or unreadable.
    proj_rm = tmp_path / "rm_corrupt_proj"
    _write(proj_rm / "a.txt", "v1\n")
    cp_rm = await manager.create(proj_rm, tmp_path / "copies" / "rm_corrupt1")
    rm_meta_dir = _copy_trees.copy_meta_dir(cp_rm.path)
    rm_meta_file = rm_meta_dir / "meta.json"
    saved_meta_bytes = rm_meta_file.read_bytes()
    rm_meta_file.write_text("{corrupt json", encoding="utf-8")
    with pytest.raises(WorktreeError, match="reading working-copy metadata"):
        await manager.remove(cp_rm)
    assert cp_rm.path.is_dir() and (rm_meta_dir / "base_manifest.json").is_file()
    rm_meta_file.write_bytes(saved_meta_bytes)
    await manager.remove(cp_rm)


@pytest.mark.skipif(REAL_GIT_TOO_OLD, reason="git missing or below minimum version")
async def test_coderabbit_and_greptile_git_followup_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Git-specific dependency-link unstage/kept filtering and atomic delivery note retry tests."""
    manager = WorktreeManager()

    # 1. Staged dependency link (including staged content differing from both HEAD and disk) must be
    # force-unstaged from the scratch index without `-z` and must never be re-added via `kept`.
    repo = _make_repo(tmp_path / "dep_stage_repo")
    _write(repo / ".gitignore", "node_modules/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "--no-gpg-sign", "-m", "ignore node_modules")
    _write(repo / "node_modules" / "pkg" / "index.js", "module.exports = 1;\n")
    wt = await manager.create(repo, tmp_path / "wt" / "dep1", link_dependencies=True)
    assert wt.linked_dependencies == ("node_modules",)
    # Replace link with a real file and land on a branch so start tracks `node_modules`.
    _copy_trees.remove_dir_link(wt.path / "node_modules")
    _write(wt.path / "node_modules", "placeholder\n")
    _git(wt.path, "add", "-f", "node_modules")
    first_land = await manager.merge_back(wt, BranchCommit(branch="feat/dep-keep", message="track file"))
    assert first_land.status == "landed", first_land.detail
    # Stage a different regular-file content (differs from HEAD), then replace disk entry with active link
    # (differs from both staged and HEAD) so `git rm --cached` requires `--force` on the scratch index.
    _write(wt.path / "node_modules", "staged-differs-from-head\n")
    _git(wt.path, "add", "-f", "node_modules")
    (wt.path / "node_modules").unlink()
    _copy_trees.make_dir_link(repo / "node_modules", wt.path / "node_modules")
    _write(wt.path / "a.txt", "updated-after-link\nline2\nline3\nline4\nline5\n")
    second_land = await manager.merge_back(wt, BranchCommit(branch="feat/dep-keep", message="edit without link"))
    assert second_land.status == "landed", second_land.detail
    assert second_land.commit
    tree_entries = _git(repo, "ls-tree", "-r", "--name-only", second_land.commit).splitlines()
    assert "node_modules" not in tree_entries
    await manager.remove(wt)

    # 2. First delivery failing on the final delivery note write still proves ownership via pending
    # intent on direct retry, while an external OID mismatch on that branch is still refused.
    local_nd, _bare_nd, _other_nd = _make_repo_with_remote(tmp_path / "nd_atomic")
    wt_first = await manager.create(local_nd, tmp_path / "wt" / "nd_first", RemoteDefaultBranch(fetch=False))
    _write(wt_first.path / "a.txt", "first-only\nline2\nline3\nline4\nline5\n")
    orig_replace = os.replace
    first_calls = {"n": 0}

    def fail_first_final_note(src: Any, dst: Any) -> None:
        if str(dst).endswith("nekopower-delivery.json"):
            first_calls["n"] += 1
            if first_calls["n"] == 2:
                raise OSError("simulated first final note write failure")
        orig_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_first_final_note)
        with pytest.raises(OSError, match="simulated first final note write failure"):
            await manager.merge_back(wt_first, BranchCommit(branch="feat/first-intent", message="initial"))
    # Direct retry with no further edits promotes pending intent to finalized delivery note,
    # so later edits still land even if the branch advances externally.
    direct_retry = await manager.merge_back(wt_first, BranchCommit(branch="feat/first-intent", message="initial-retry"))
    assert direct_retry.status in ("unchanged", "landed"), direct_retry.detail
    common_nd = Path(_git(local_nd, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
    assert worktrees._noted_delivery(common_nd, wt_first.path, strict=True).get("branch") == "feat/first-intent"
    # Verify strict registration lookup propagates gitdir read OSError (fail-closed in has_pending_delivery).
    orig_read_text = Path.read_text

    def fail_gitdir_read(self_path: Path, *args: Any, **kwargs: Any) -> str:
        if self_path.name == "gitdir":
            raise PermissionError("simulated unreadable gitdir")
        return orig_read_text(self_path, *args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(Path, "read_text", fail_gitdir_read)
        with pytest.raises(PermissionError, match="unreadable gitdir"):
            await manager.has_pending_delivery(wt_first)

    # Externally advance feat/first-intent and land a follow-up edit cleanly using finalized ownership.
    ext_adv = tmp_path / "ext_adv"
    _git(local_nd, "worktree", "add", "-q", str(ext_adv), "feat/first-intent")
    _write(ext_adv / "extra.txt", "branch-side\n")
    _git(ext_adv, "add", "extra.txt")
    _git(ext_adv, "commit", "-q", "--no-gpg-sign", "-m", "external advance")
    _git(local_nd, "worktree", "remove", "--force", str(ext_adv))
    _write(wt_first.path / "a.txt", "after-finalized-retry\nline2\nline3\nline4\nline5\n")
    follow_land = await manager.merge_back(wt_first, BranchCommit(branch="feat/first-intent", message="follow-up"))
    assert follow_land.status == "landed", follow_land.detail

    # If persisting pending_pr_delivery fails before calling create_pr on an already-pushed branch,
    # _land_as_pr must fail without invoking create_pr.
    pushed_first = await manager.merge_back(wt_first, BranchCommit(branch="feat/first-intent", remote="origin", message="push"))
    assert pushed_first.status == "landed", pushed_first.detail
    pr_called = {"flag": False}

    async def should_not_run(_req: PRRequest) -> PRInfo:
        pr_called["flag"] = True
        return PRInfo(url="https://github.com/org/repo/pull/99", number=99)

    def fail_pending_pr_replace(src: Any, dst: Any) -> None:
        if str(dst).endswith("nekopower-delivery.json"):
            raise OSError("simulated pending PR note failure")
        orig_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_pending_pr_replace)
        pr_fail_note = await manager.merge_back(
            wt_first,
            PullRequest(title="Fail pending write", branch="feat/first-intent", remote="origin", base_branch="main", create_pr=should_not_run),
        )
    assert pr_fail_note.status == "failed" and "recording pending pull request state failed" in pr_fail_note.detail
    assert pr_called["flag"] is False

    # Switching from branch A (which has a PR) to branch B with a failed final note write and then
    # locally retrying branch B must clear A's PR/push fields so a later PR on B opens a fresh PR for B.
    pr_a = await manager.merge_back(
        wt_first,
        PullRequest(
            title="PR A",
            branch="feat/first-intent",
            remote="origin",
            base_branch="main",
            create_pr=lambda _req: asyncio.sleep(0, result=PRInfo(url="https://github.com/org/repo/pull/10", number=10)),
        ),
    )
    assert pr_a.status == "landed" and pr_a.pr_number == 10
    _write(wt_first.path / "b_switch.txt", "for branch B\n")
    first_calls["n"] = 0
    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_first_final_note)
        with pytest.raises(OSError, match="simulated first final note write failure"):
            await manager.merge_back(wt_first, BranchCommit(branch="feat/branch-b", message="switch to B"))
    retry_b = await manager.merge_back(wt_first, BranchCommit(branch="feat/branch-b", message="retry B"))
    assert retry_b.status in ("unchanged", "landed"), retry_b.detail
    note_b = worktrees._noted_delivery(common_nd, wt_first.path, strict=True)
    assert note_b.get("branch") == "feat/branch-b"
    assert not note_b.get("pr_url") and note_b.get("pr_number") is None and note_b.get("prs") == {}
    assert note_b.get("pushed") is False
    seen_pr_b: list[str] = []

    async def create_pr_b(req: PRRequest) -> PRInfo:
        seen_pr_b.append(req.branch)
        return PRInfo(url="https://github.com/org/repo/pull/20", number=20)

    pr_b = await manager.merge_back(
        wt_first,
        PullRequest(title="PR B", branch="feat/branch-b", remote="origin", base_branch="main", create_pr=create_pr_b),
    )
    assert pr_b.status == "landed" and pr_b.pr_number == 20 and seen_pr_b == ["feat/branch-b"]
    await manager.remove(wt_first)

    # If branch OID changed externally after a failed first final note write, ownership is refused.
    wt_mismatch = await manager.create(local_nd, tmp_path / "wt" / "nd_mismatch", RemoteDefaultBranch(fetch=False))
    _write(wt_mismatch.path / "a.txt", "mismatch-edit\nline2\nline3\nline4\nline5\n")
    first_calls["n"] = 0
    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_first_final_note)
        with pytest.raises(OSError, match="simulated first final note write failure"):
            await manager.merge_back(wt_mismatch, BranchCommit(branch="feat/mismatch-intent", message="initial"))
    # Externally move feat/mismatch-intent to a different commit OID (HEAD of main).
    main_oid = _git(local_nd, "rev-parse", "refs/heads/main").strip()
    _git(local_nd, "update-ref", "refs/heads/feat/mismatch-intent", main_oid)
    _write(wt_mismatch.path / "a.txt", "mismatch-edit-2\nline2\nline3\nline4\nline5\n")
    refused = await manager.merge_back(wt_mismatch, BranchCommit(branch="feat/mismatch-intent", message="should fail"))
    assert refused.status == "failed" and "does not belong to this worktree" in refused.detail
    await manager.remove(wt_mismatch)

    # 3. Failed _note_delivery writes on subsequent delivery preserve prior ownership note for retry.
    wt_nd = await manager.create(local_nd, tmp_path / "wt" / "nd1", RemoteDefaultBranch(fetch=False))
    _write(wt_nd.path / "a.txt", "first-edit\nline2\nline3\nline4\nline5\n")
    land1 = await manager.merge_back(wt_nd, BranchCommit(branch="feat/owned-atomic", message="first"))
    assert land1.status == "landed", land1.detail
    _write(wt_nd.path / "a.txt", "second-edit\nline2\nline3\nline4\nline5\n")
    fail_on_call = {"n": 0, "target": 1}

    def fail_delivery_note_nth(src: Any, dst: Any) -> None:
        if str(dst).endswith("nekopower-delivery.json"):
            fail_on_call["n"] += 1
            if fail_on_call["n"] == fail_on_call["target"]:
                raise OSError("simulated delivery note write failure")
        orig_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_delivery_note_nth)
        failed_intent = await manager.merge_back(wt_nd, BranchCommit(branch="feat/owned-atomic", message="second"))
        assert failed_intent.status == "failed" and "recording delivery intent failed" in failed_intent.detail
        # Now let the intent write succeed (#2) and fail the final write (#3); ownership via intent survives for retry.
        fail_on_call["target"] = 3
        with pytest.raises(OSError, match="simulated delivery note write failure"):
            await manager.merge_back(wt_nd, BranchCommit(branch="feat/owned-atomic", message="second"))
    # Direct retry advances start_ref and finalizes the delivery note cleanly.
    retry2 = await manager.merge_back(wt_nd, BranchCommit(branch="feat/owned-atomic", message="second-retry"))
    assert retry2.status in ("unchanged", "landed"), retry2.detail
    _write(wt_nd.path / "a.txt", "third-edit\nline2\nline3\nline4\nline5\n")
    land3 = await manager.merge_back(wt_nd, BranchCommit(branch="feat/owned-atomic", message="third-retry"))
    assert land3.status == "landed", land3.detail
    await manager.remove(wt_nd)

    # 4. If PR creation succeeds but writing the final delivery note fails, retrying recovers the
    # already-created PR without invoking PR creation a second time (both same-process callback
    # cache and cross-process recovery via gh pr list / find_pr).
    wt_pr_rec = await manager.create(local_nd, tmp_path / "wt" / "pr_rec1", RemoteDefaultBranch(fetch=False))
    _write(wt_pr_rec.path / "a.txt", "pr-recover-1\nline2\nline3\nline4\nline5\n")
    cb_create_calls = {"n": 0}

    async def create_once_cb(_req: PRRequest) -> PRInfo:
        cb_create_calls["n"] += 1
        return PRInfo(url="https://github.com/org/repo/pull/301", number=301)

    note_writes = {"n": 0}

    def fail_final_pr_note(src: Any, dst: Any) -> None:
        if str(dst).endswith("nekopower-delivery.json"):
            note_writes["n"] += 1
            # 1=pending_branch, 2=after push, 3=pending_pr_delivery, 4=final updated_delivery
            if note_writes["n"] == 4:
                raise OSError("simulated final PR note write failure")
        orig_replace(src, dst)

    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_final_pr_note)
        with pytest.raises(OSError, match="simulated final PR note write failure"):
            await manager.merge_back(
                wt_pr_rec,
                PullRequest(title="Recoverable PR", branch="feat/pr-rec-cb", remote="origin", base_branch="main", create_pr=create_once_cb),
            )
    assert cb_create_calls["n"] == 1
    assert await manager.has_pending_delivery(wt_pr_rec) is True
    # Same-process retry must recover PR #301 without calling create_once_cb again.
    rec_same = await manager.merge_back(
        wt_pr_rec,
        PullRequest(title="Recoverable PR", branch="feat/pr-rec-cb", remote="origin", base_branch="main", create_pr=create_once_cb),
    )
    assert rec_same.status == "landed" and rec_same.pr_number == 301 and cb_create_calls["n"] == 1
    assert await manager.has_pending_delivery(wt_pr_rec) is False
    await manager.remove(wt_pr_rec)

    # Cross-process recovery on the default gh CLI path (fresh WorktreeManager instance after crash):
    # gh pr list runs before gh pr create and recovers the existing PR without calling create twice.
    wt_pr_gh = await manager.create(local_nd, tmp_path / "wt" / "pr_rec_gh", RemoteDefaultBranch(fetch=False))
    _write(wt_pr_gh.path / "a.txt", "pr-recover-gh\nline2\nline3\nline4\nline5\n")
    gh_log = tmp_path / "gh_rec_calls.json"
    gh_shim_py = tmp_path / "fake_gh_rec.py"
    gh_shim_py.write_text(
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(gh_log)!r})\n"
        "calls = json.loads(log.read_text(encoding='utf-8')) if log.exists() else []\n"
        "argv = sys.argv[1:]\n"
        "calls.append(argv)\n"
        "log.write_text(json.dumps(calls), encoding='utf-8')\n"
        "if argv[:2] == ['pr', 'create']:\n"
        "    if sum(1 for c in calls if c[:2] == ['pr', 'create']) > 1:\n"
        "        sys.stderr.write('a pull request for branch already exists\\n')\n"
        "        sys.exit(1)\n"
        "    print('https://github.com/org/repo/pull/302')\n"
        "elif argv[:2] == ['pr', 'list']:\n"
        "    print(json.dumps([{'url': 'https://github.com/org/repo/pull/302', 'number': 302, 'headRefName': 'feat/pr-rec-gh', 'baseRefName': 'main', 'isCrossRepository': False}]))\n"
        "else:\n"
        "    sys.exit(2)\n",
        encoding="utf-8",
    )
    if sys.platform == "win32":
        gh_rec_bin = tmp_path / "fake_gh_rec.cmd"
        gh_rec_bin.write_text(f'@echo off\r\n"{sys.executable}" "{gh_shim_py}" %*\r\n', encoding="utf-8")
    else:
        gh_rec_bin = tmp_path / "fake_gh_rec.sh"
        gh_rec_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{gh_shim_py}" "$@"\n', encoding="utf-8")
        os.chmod(gh_rec_bin, 0o755)

    note_writes["n"] = 0
    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_final_pr_note)
        with pytest.raises(OSError, match="simulated final PR note write failure"):
            await manager.merge_back(
                wt_pr_gh,
                PullRequest(title="Recoverable GH PR", branch="feat/pr-rec-gh", remote="origin", base_branch="main", gh=str(gh_rec_bin)),
            )
    # Simulate process restart with a brand-new WorktreeManager (empty in-memory cache).
    fresh_manager = WorktreeManager()
    assert await fresh_manager.has_pending_delivery(wt_pr_gh) is True
    rec_gh = await fresh_manager.merge_back(
        wt_pr_gh,
        PullRequest(title="Recoverable GH PR", branch="feat/pr-rec-gh", remote="origin", base_branch="main", gh=str(gh_rec_bin)),
    )
    assert rec_gh.status == "landed", rec_gh.detail
    assert rec_gh.pr_number == 302 and rec_gh.pr_url == "https://github.com/org/repo/pull/302"
    assert await fresh_manager.has_pending_delivery(wt_pr_gh) is False
    recorded_calls = json.loads(gh_log.read_text(encoding="utf-8"))
    assert [c[:2] for c in recorded_calls] == [["pr", "create"], ["pr", "list"]]
    await fresh_manager.remove(wt_pr_gh)

    # Cross-process recovery with custom create_pr + find_pr callback.
    wt_pr_find = await manager.create(local_nd, tmp_path / "wt" / "pr_rec_find", RemoteDefaultBranch(fetch=False))
    _write(wt_pr_find.path / "a.txt", "pr-recover-find\nline2\nline3\nline4\nline5\n")
    find_create_calls = {"create": 0, "find": 0}

    async def create_find_cb(_req: PRRequest) -> PRInfo:
        find_create_calls["create"] += 1
        return PRInfo(url="https://github.com/org/repo/pull/303", number=303)

    async def lookup_pr_cb(_req: PRRequest) -> PRInfo | None:
        find_create_calls["find"] += 1
        return PRInfo(url="https://github.com/org/repo/pull/303", number=303)

    note_writes["n"] = 0
    with monkeypatch.context() as mp:
        mp.setattr(os, "replace", fail_final_pr_note)
        with pytest.raises(OSError, match="simulated final PR note write failure"):
            await manager.merge_back(
                wt_pr_find,
                PullRequest(
                    title="Recoverable Find PR",
                    branch="feat/pr-rec-find",
                    remote="origin",
                    base_branch="main",
                    create_pr=create_find_cb,
                    find_pr=lookup_pr_cb,
                ),
            )
    fresh_manager_2 = WorktreeManager()
    rec_find = await fresh_manager_2.merge_back(
        wt_pr_find,
        PullRequest(
            title="Recoverable Find PR",
            branch="feat/pr-rec-find",
            remote="origin",
            base_branch="main",
            create_pr=create_find_cb,
            find_pr=lookup_pr_cb,
        ),
    )
    assert rec_find.status == "landed" and rec_find.pr_number == 303
    assert find_create_calls == {"create": 1, "find": 1}
    await fresh_manager_2.remove(wt_pr_find)

    # 5. Remote pushurl retargeting & remote branch ownership:
    # Pushing to a remote where `feat/unowned` already exists (even as a fast-forward ancestor)
    # must be refused; retargeting origin's pushurl from Repo A to Repo B must push to Repo B and
    # open a fresh PR instead of returning Repo A's cached PR.
    local_rt, bare_a, _other_rt = _make_repo_with_remote(tmp_path / "retarget_test")
    bare_b = tmp_path / "retarget_test" / "bare_b.git"
    _git(tmp_path / "retarget_test", "clone", "--bare", "-q", str(bare_a), str(bare_b))
    # Pre-create `feat/unowned` on bare_a pointing to main (a fast-forward ancestor of new commits).
    main_rt_oid = _git(local_rt, "rev-parse", "refs/heads/main").strip()
    _git(bare_a, "update-ref", "refs/heads/feat/unowned", main_rt_oid)
    wt_unowned = await manager.create(local_rt, tmp_path / "wt" / "rt_unowned", RemoteDefaultBranch(fetch=False))
    _write(wt_unowned.path / "a.txt", "attempt-clobber\nline2\nline3\nline4\nline5\n")
    refused_remote = await manager.merge_back(
        wt_unowned, BranchCommit(branch="feat/unowned", remote="origin", message="should refuse")
    )
    assert refused_remote.status == "failed" and "does not belong to this worktree" in refused_remote.detail
    assert _git(bare_a, "rev-parse", "refs/heads/feat/unowned").strip() == main_rt_oid
    await manager.remove(wt_unowned)

    wt_rt = await manager.create(local_rt, tmp_path / "wt" / "rt_retarget", RemoteDefaultBranch(fetch=False))
    _write(wt_rt.path / "a.txt", "deliver-to-a-then-b\nline2\nline3\nline4\nline5\n")
    rt_prs: list[str] = []

    async def make_rt_pr(req: PRRequest) -> PRInfo:
        idx = len(rt_prs) + 1
        url = f"https://github.com/org/repo-{idx}/pull/{idx}"
        rt_prs.append(url)
        return PRInfo(url=url, number=idx)

    pr_on_a = await manager.merge_back(
        wt_rt, PullRequest(title="PR A", branch="feat/retarget", remote="origin", base_branch="main", create_pr=make_rt_pr)
    )
    assert pr_on_a.status == "landed" and pr_on_a.pr_number == 1
    assert _git(bare_a, "rev-parse", "refs/heads/feat/retarget").strip() == pr_on_a.commit
    # Retarget origin's pushurl to bare_b with no new file changes: must push to bare_b and open PR #2.
    _git(local_rt, "remote", "set-url", "--push", "origin", str(bare_b))
    pr_on_b = await manager.merge_back(
        wt_rt, PullRequest(title="PR B", branch="feat/retarget", remote="origin", base_branch="main", create_pr=make_rt_pr)
    )
    assert pr_on_b.status == "landed", pr_on_b.detail
    assert pr_on_b.pr_number == 2 and len(rt_prs) == 2
    assert _git(bare_b, "rev-parse", "refs/heads/feat/retarget").strip() == pr_on_a.commit
    await manager.remove(wt_rt)

    # 6. _in_the_way must ignore paths under active dependency links without touching disk,
    # while still blocking once a link is replaced with a real directory.
    wt_inway_root = tmp_path / "inway_proj"
    wt_inway_copy = tmp_path / "inway_wt"
    _write(wt_inway_root / "vendor" / "config.json", "{}\n")
    wt_inway_copy.mkdir()
    _copy_trees.make_dir_link(wt_inway_root / "vendor", wt_inway_copy / "vendor")
    assert worktrees._in_the_way(wt_inway_copy, ["vendor/config.json"], set(), set(), {"vendor"}) == []
    _copy_trees.remove_dir_link(wt_inway_copy / "vendor")
    _write(wt_inway_copy / "vendor" / "config.json", '{"real": true}\n')
    assert worktrees._in_the_way(wt_inway_copy, ["vendor/config.json"], set(), set(), set()) == ["vendor/config.json"]
