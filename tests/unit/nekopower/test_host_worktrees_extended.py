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
import json
import os
import shutil
import subprocess
import sys
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
    REF_PREFIX,
    SUBMODULES,
    BranchCommit,
    PRInfo,
    PRRequest,
    PullRequest,
    RemoteDefaultBranch,
    WorktreeError,
    WorktreeManager,
    WorktreeUnavailable,
    parse_git_version,
)
from tests.unit.nekopower.fakes import ScriptedLLM, call, finish, say
from tests.unit.nekopower.host_fakes import FakeDecisionLLM, fake_model, make_host, scripted_runtime, wait_until
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


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    env = {**os.environ, **USER_ENV, "GIT_OPTIONAL_LOCKS": "0"}
    done = subprocess.run(
        ["git", "-c", "core.longpaths=true", *args],
        cwd=cwd,
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
        return [call("report", {"status": "ready", "text": text}), finish("tool_calls")]

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

    def pick(spec: Any) -> ScriptedLLM:
        if "create alpha.txt" in spec.system_prompt or "alpha" in str(getattr(spec, "name", "")):
            return worker_a
        return leader_llm

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
