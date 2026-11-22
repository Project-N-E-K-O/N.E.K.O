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

"""Worktrees for side sessions, with real git in temporary repositories.

Skipped where git cannot be run. Every repository here is made below
pytest's temporary directory; git reads no global or system configuration
of the machine (``_isolated_git``), so the results do not depend on it.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nekoclaw.host import worktrees
from nekoclaw.host.paths import HostPaths
from nekoclaw.host.worktrees import (
    BARE_REPOSITORY,
    DISK_SPACE,
    GIT_FAILED,
    GIT_MISSING,
    GIT_TOO_OLD,
    IGNORED_DIRECTORY,
    CONTENT_FILTERS,
    NESTED_REPOSITORY,
    NOT_A_REPOSITORY,
    OPERATION_IN_PROGRESS,
    REF_PREFIX,
    SPARSE_CHECKOUT,
    SUBMODULES,
    FromRef,
    MergeResult,
    Worktree,
    WorktreeError,
    WorktreeManager,
    WorktreeUnavailable,
    _fs,
    parse_git_version,
)

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed on this machine")

WINDOWS = sys.platform == "win32"
USER = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


@pytest.fixture(autouse=True)
def _isolated_git(tmp_path: Path, monkeypatch: Any) -> None:
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Attribute files too: the system's, and the user's default one.
    monkeypatch.setenv("GIT_ATTR_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # And no template of the machine's (an exclude file there would hide files).
    (tmp_path / "git-template").mkdir()
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "git-template"))
    # The same isolation the manager gives its own git calls.
    for name in (*worktrees._REDIRECTING_ENV, *USER):
        monkeypatch.delenv(name, raising=False)
    # Some tests write object names of 40 hex digits.
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha1")


def git(cwd: Path, *args: str, check: bool = True, input: bytes | None = None) -> str:
    env = {**os.environ, **USER, "GIT_OPTIONAL_LOCKS": "0"}
    done = subprocess.run(
        ["git", "-c", "core.longpaths=true", *args],
        cwd=cwd,
        input=input,
        capture_output=True,
        env=env,
        check=False,
    )
    if check and done.returncode != 0:
        raise AssertionError(f"git {args} failed: {done.stderr.decode('utf-8', 'replace')}")
    return done.stdout.decode("utf-8", "surrogateescape")


def git_succeeds(cwd: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False).returncode == 0


def write(path: Path, data: str | bytes) -> None:
    os.makedirs(os.path.dirname(_fs(path)), exist_ok=True)
    with open(_fs(path), "wb") as handle:
        handle.write(data.encode("utf-8") if isinstance(data, str) else data)


def read(path: Path) -> bytes:
    with open(_fs(path), "rb") as handle:
        return handle.read()


def exists(path: Path) -> bool:
    return os.path.lexists(_fs(path))


def digest(root: Path) -> dict[str, tuple[str, bool]]:
    """Every file below ``root`` but ``.git``: content hash and executable bit."""
    found = {}
    top = _fs(root)
    for current, dirs, files in os.walk(top):
        dirs[:] = [name for name in dirs if not (current == top and name == ".git")]
        for name in files:
            full = os.path.join(current, name)
            relative = os.path.relpath(full, top).replace(os.sep, "/")
            if relative == ".git":
                continue
            with open(full, "rb") as handle:
                content = hashlib.sha256(handle.read()).hexdigest()
            found[relative] = (content, bool(os.lstat(full).st_mode & stat.S_IXUSR) and not WINDOWS)
    return found


def registered(repo: Path) -> set[str]:
    """The names of the directories of the repository's worktrees, its own included."""
    listed = git(repo, "worktree", "list", "--porcelain", "-z").split("\0")
    return {Path(entry[len("worktree ") :]).name for entry in listed if entry.startswith("worktree ")}


def repository_state(repo: Path) -> dict[str, Any]:
    git_dir = Path(git(repo, "rev-parse", "--absolute-git-dir").strip())
    return {
        "status": git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--ignored"),
        "head": git(repo, "rev-parse", "HEAD"),
        "symbolic": git(repo, "symbolic-ref", "-q", "HEAD", check=False),
        "branches": git(repo, "branch", "-a", "-v"),
        "stash": git(repo, "stash", "list"),
        "index": (git_dir / "index").read_bytes(),
        "config": (git_dir / "config").read_bytes(),
        "refs": [
            line
            for line in git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
            if not line.startswith(REF_PREFIX)
        ],
        "worktrees": git(repo, "worktree", "list", "--porcelain"),
    }


def make_repo(path: Path, files: dict[str, str | bytes] | None = None, **config: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "core.autocrlf", "false")
    for key, value in config.items():
        git(path, "config", key.replace("_", "."), value)
    for name, data in (files if files is not None else BASE_FILES).items():
        write(path / name, data)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")
    return path


BASE_FILES: dict[str, str | bytes] = {
    "README.md": "# demo\n",
    ".gitignore": "ignored/\n*.log\n",
    "a.txt": "one\ntwo\nthree\nfour\nfive\n",
    "b.txt": "alpha\nbeta\ngamma\n",
    "src/app.py": "print('hello')\n",
    "src/util.py": "def f():\n    return 1\n",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "repo")


@pytest.fixture
def manager() -> WorktreeManager:
    return WorktreeManager()


@pytest.fixture
def base(tmp_path: Path) -> Path:
    return tmp_path / "wt"


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch: Any) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_MIN_FREE_BYTES", 1)


# -- version and probing -------------------------------------------------------


def test_parse_git_version() -> None:
    assert parse_git_version("git version 2.49.0.windows.1") == (2, 49, 0)
    assert parse_git_version("git version 2.40.0") == (2, 40, 0)
    assert parse_git_version("git version 2.39") == (2, 39, 0)
    assert parse_git_version("no version here") is None


async def test_probe_usable_root_and_subdirectory(repo: Path, manager: WorktreeManager) -> None:
    found = await manager.probe(repo)
    assert found.usable and found.reason is None
    assert os.path.samefile(found.repo_root, repo) and found.subpath == ""
    nested = await manager.probe(repo / "src")
    assert nested.usable and nested.subpath == "src"


async def test_probe_git_missing(repo: Path, tmp_path: Path) -> None:
    found = await WorktreeManager(git=str(tmp_path / "no-such-git")).probe(repo)
    assert found.reason == GIT_MISSING and not found.usable


@pytest.mark.parametrize(("version", "usable"), [((2, 39, 9), False), ((2, 40, 0), True)])
async def test_probe_git_version_floor(repo: Path, manager: WorktreeManager, monkeypatch: Any, version: Any, usable: bool) -> None:
    async def fixed() -> tuple[int, int, int]:
        return version

    monkeypatch.setattr(manager, "git_version", fixed)
    found = await manager.probe(repo)
    assert found.usable is usable
    if not usable:
        assert found.reason == GIT_TOO_OLD


async def test_probe_not_a_repository(tmp_path: Path, manager: WorktreeManager) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    found = await manager.probe(plain)
    assert found.reason == NOT_A_REPOSITORY and "in no git repository" in found.detail
    assert (await manager.probe(tmp_path / "missing")).reason == NOT_A_REPOSITORY


async def test_probe_something_not_a_directory_in_a_repository(repo: Path, manager: WorktreeManager) -> None:
    for path in (repo / "a.txt", repo / "missing"):
        found = await manager.probe(path)
        assert found.reason == NOT_A_REPOSITORY and "not a directory" in found.detail


async def test_probe_inside_git_directory(repo: Path, manager: WorktreeManager) -> None:
    assert (await manager.probe(repo / ".git" / "refs")).reason == NOT_A_REPOSITORY


async def test_probe_bare(repo: Path, manager: WorktreeManager) -> None:
    git(repo, "config", "core.bare", "true")
    assert (await manager.probe(repo)).reason == BARE_REPOSITORY


async def test_probe_a_bare_repository_of_its_own(tmp_path: Path, manager: WorktreeManager) -> None:
    bare = tmp_path / "bare.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    assert (await manager.probe(bare)).reason == BARE_REPOSITORY
    plain = tmp_path / "plain"
    plain.mkdir()
    assert (await manager.probe(plain)).reason == NOT_A_REPOSITORY


async def test_probe_beyond_the_search_in_a_repository_is_not_bare(
    repo: Path, manager: WorktreeManager, monkeypatch: Any
) -> None:
    import config.nekoclaw_settings as host_settings

    deep = repo / "a" / "b" / "c"
    deep.mkdir(parents=True)
    monkeypatch.setattr(host_settings, "REPO_ROOT_SEARCH_MAX_LEVELS", 1)  # git finds it, the search does not
    assert (await manager.probe(deep)).reason == NOT_A_REPOSITORY


async def test_probe_git_failed(tmp_path: Path, manager: WorktreeManager) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".git").write_text("gitdir: ./nowhere\n", encoding="utf-8")
    assert (await manager.probe(broken)).reason == GIT_FAILED


async def test_probe_ignored_directory(repo: Path, manager: WorktreeManager) -> None:
    write(repo / "ignored" / "x.txt", "x")
    found = await manager.probe(repo / "ignored")
    assert found.reason == IGNORED_DIRECTORY and found.subpath == "ignored"


async def test_probe_submodules_file(repo: Path, manager: WorktreeManager) -> None:
    write(repo / ".gitmodules", '[submodule "x"]\n\tpath = x\n\turl = ../x\n')
    assert (await manager.probe(repo)).reason == SUBMODULES


async def test_probe_submodule_link_without_file(repo: Path, manager: WorktreeManager) -> None:
    head = git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"160000,{head},vendored")
    assert (await manager.probe(repo)).reason == SUBMODULES


@pytest.mark.parametrize(
    ("marker", "directory"),
    [
        ("MERGE_HEAD", False),
        ("rebase-merge", True),
        ("rebase-apply", True),
        ("CHERRY_PICK_HEAD", False),
        ("REVERT_HEAD", False),
        ("BISECT_LOG", False),
    ],
)
async def test_probe_operation_in_progress(repo: Path, manager: WorktreeManager, marker: str, directory: bool) -> None:
    target = repo / ".git" / marker
    if directory:
        target.mkdir()
    else:
        target.write_text(git(repo, "rev-parse", "HEAD"), encoding="utf-8")
    assert (await manager.probe(repo)).reason == OPERATION_IN_PROGRESS


async def test_probe_real_merge_in_progress(repo: Path, manager: WorktreeManager) -> None:
    git(repo, "checkout", "-q", "-b", "other")
    write(repo / "a.txt", "ONE\n")
    git(repo, "commit", "-qam", "other")
    git(repo, "checkout", "-q", "main")
    write(repo / "a.txt", "uno\n")
    git(repo, "commit", "-qam", "main")
    git(repo, "merge", "other", check=False)
    assert (await manager.probe(repo)).reason == OPERATION_IN_PROGRESS


@pytest.mark.parametrize(
    ("where", "line", "path"),
    [
        ("assets/.gitattributes", "*.bin filter=lfs diff=lfs merge=lfs -text\n", "assets/model.bin"),
        (".gitattributes", "secret/** filter=git-crypt diff=git-crypt\n", "secret/key.txt"),
        (".git/info/attributes", "*.psd filter=lfs\n", "art.psd"),
    ],
    ids=["lfs", "git-crypt", "info-attributes"],
)
async def test_probe_content_filters(repo: Path, manager: WorktreeManager, where: str, line: str, path: str) -> None:
    write(repo / where, line)
    write(repo / path, "content\n")
    found = await manager.probe(repo)
    assert found.reason == CONTENT_FILTERS and path in found.detail


async def test_probe_content_filter_from_the_users_attributes_file(
    repo: Path, manager: WorktreeManager, tmp_path: Path
) -> None:
    attributes = tmp_path / "global-attributes"
    attributes.write_text("*.txt filter=anything\n", encoding="utf-8")
    git(repo, "config", "core.attributesFile", attributes.as_posix())
    assert (await manager.probe(repo)).reason == CONTENT_FILTERS


async def test_probe_filter_unset_or_for_no_file(repo: Path, manager: WorktreeManager) -> None:
    write(repo / ".gitattributes", "*.nothing-here filter=lfs\n*.txt -filter\n")
    assert (await manager.probe(repo)).usable


async def test_probe_attributes_without_lfs(repo: Path, manager: WorktreeManager) -> None:
    write(repo / ".gitattributes", "*.png binary\n\n# filter=lfs is only mentioned here\n")
    assert (await manager.probe(repo)).usable


async def test_probe_sparse_checkout(repo: Path, manager: WorktreeManager) -> None:
    git(repo, "config", "core.sparseCheckout", "true")
    assert (await manager.probe(repo)).reason == SPARSE_CHECKOUT


async def test_unusable_directory_is_refused(tmp_path: Path, manager: WorktreeManager, base: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.create(plain, base / "w1")
    assert raised.value.reason == NOT_A_REPOSITORY
    with pytest.raises(WorktreeUnavailable):
        await manager.snapshot(plain)


# -- snapshots -----------------------------------------------------------------


def dirty(repo: Path) -> None:
    """Uncommitted changes of every kind, a staged one, an ignored file and a stash entry."""
    write(repo / "README.md", "# changed then stashed\n")
    git(repo, "stash", "-q")
    write(repo / "a.txt", "one\ntwo\nTHREE\nfour\nfive\n")
    write(repo / "b.txt", "alpha\nBETA\ngamma\n")
    git(repo, "add", "b.txt")
    (repo / "src" / "util.py").unlink()
    write(repo / "new" / "fresh.txt", "fresh\n")
    write(repo / "ignored" / "secret.env", "TOKEN=1\n")
    write(repo / "debug.log", "log\n")


def tree_files(repo: Path, commit: str) -> set[str]:
    return set(git(repo, "ls-tree", "-r", "--name-only", "-z", commit).split("\0")) - {""}


async def test_snapshot_changes_nothing_the_user_sees(repo: Path, manager: WorktreeManager) -> None:
    dirty(repo)
    before = repository_state(repo)
    files_before = digest(repo)
    shot = await manager.snapshot(repo)
    assert repository_state(repo) == before
    assert digest(repo) == files_before
    names = tree_files(repo, shot.commit)
    assert {"a.txt", "b.txt", "new/fresh.txt", "README.md"} <= names
    assert "src/util.py" not in names
    assert not any(name.startswith("ignored/") or name.endswith(".log") for name in names)
    assert git(repo, "show", f"{shot.commit}:a.txt") == "one\ntwo\nTHREE\nfour\nfive\n"
    assert git(repo, "rev-parse", f"{shot.commit}^").strip() == git(repo, "rev-parse", "HEAD").strip()


async def test_snapshot_identity_and_no_signing(repo: Path, manager: WorktreeManager) -> None:
    git(repo, "config", "commit.gpgSign", "true")
    git(repo, "config", "gpg.program", "no-such-gpg-program")
    shot = await manager.snapshot(repo)
    assert git(repo, "log", "-1", "--format=%an <%ae>|%cn", shot.commit).strip() == "NekoClaw <nekoclaw@localhost>|NekoClaw"


async def test_snapshot_ignores_redirecting_environment(repo: Path, tmp_path: Path, manager: WorktreeManager, monkeypatch: Any) -> None:
    other = make_repo(tmp_path / "other", {"elsewhere.txt": "x\n"})
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    shot = await manager.snapshot(repo)
    monkeypatch.delenv("GIT_DIR")
    monkeypatch.delenv("GIT_WORK_TREE")
    assert "a.txt" in tree_files(repo, shot.commit)


async def test_snapshot_of_a_repository_without_commits(tmp_path: Path, manager: WorktreeManager, base: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    write(empty / "first.txt", "first\n")
    shot = await manager.snapshot(empty)
    assert tree_files(empty, shot.commit) == {"first.txt"}
    tree = await manager.create(empty, base / "w1")
    write(tree.workdir / "second.txt", "second\n")
    write(empty / "mine.txt", "mine\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed"
    assert read(empty / "second.txt") == b"second\n" and read(empty / "mine.txt") == b"mine\n"
    assert git(empty, "rev-parse", "--verify", "-q", "HEAD", check=False) == ""


# -- making a worktree ---------------------------------------------------------


async def test_worktree_holds_uncommitted_and_new_files_not_ignored(repo: Path, manager: WorktreeManager, base: Path) -> None:
    dirty(repo)
    before = repository_state(repo)
    tree = await manager.create(repo, base / "w1")
    assert tree.id == "w1" and tree.subpath == "" and tree.workdir == tree.path
    assert read(tree.path / "a.txt") == b"one\ntwo\nTHREE\nfour\nfive\n"
    assert read(tree.path / "new" / "fresh.txt") == b"fresh\n"
    assert not exists(tree.path / "src" / "util.py")
    assert not exists(tree.path / "ignored") and not exists(tree.path / "debug.log")
    after = repository_state(repo)
    assert {key: value for key, value in after.items() if key != "worktrees"} == {
        key: value for key, value in before.items() if key != "worktrees"
    }
    assert git(repo, "branch", "-a", "--format=%(refname)").split() == ["refs/heads/main"]
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    assert git(tree.path, "rev-parse", "HEAD").strip() == tree.start
    assert git(tree.path, "symbolic-ref", "-q", "HEAD", check=False) == ""


async def test_worktree_does_not_run_the_users_hooks(repo: Path, manager: WorktreeManager, base: Path, tmp_path: Path) -> None:
    marker = tmp_path / "hook-ran"
    hook = repo / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text(f"#!/bin/sh\necho ran > '{marker.as_posix()}'\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    tree = await manager.create(repo, base / "w1")
    assert exists(tree.path / "a.txt")
    assert not marker.exists()


async def test_worktree_from_a_ref(repo: Path, manager: WorktreeManager, base: Path) -> None:
    git(repo, "branch", "earlier")
    write(repo / "a.txt", "later\n")
    git(repo, "commit", "-qam", "later")
    write(repo / "b.txt", "uncommitted\n")
    tree = await manager.create(repo, base / "w1", FromRef("earlier"))
    assert read(tree.path / "a.txt") == BASE_FILES["a.txt"].encode()
    assert read(tree.path / "b.txt") == BASE_FILES["b.txt"].encode()
    with pytest.raises(WorktreeError):
        await manager.create(repo, base / "w2", FromRef("no-such-ref"))
    assert not exists(base / "w2")


async def test_create_refuses_bad_targets(repo: Path, manager: WorktreeManager, base: Path) -> None:
    with pytest.raises(WorktreeError):
        await manager.create(repo, repo / "inside")
    occupied = base / "occupied"
    write(occupied / "x.txt", "x")
    with pytest.raises(WorktreeError) as raised:
        await manager.create(repo, occupied)
    # Refused before git is asked, so nothing was registered or pinned.
    assert type(raised.value) is WorktreeError
    assert read(occupied / "x.txt") == b"x"
    assert git(repo, "for-each-ref", REF_PREFIX) == ""
    (base / "empty").mkdir(parents=True)
    assert (await manager.create(repo, base / "empty")).id == "empty"
    with pytest.raises(ValueError):
        await manager.create(repo, base / "bad name.")
    first = await manager.create(repo, base / "twice")
    await asyncio.to_thread(shutil.rmtree, _fs(first.path))
    with pytest.raises(WorktreeError) as raised:
        await manager.create(repo, base / "twice")
    # The first one's start is still pinned: what it holds can still be found.
    assert type(raised.value) is WorktreeError
    assert git(repo, "rev-parse", f"{REF_PREFIX}twice/start").strip() == first.start


async def test_create_checks_disk_space(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    free = shutil.disk_usage(repo).free
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_MIN_FREE_BYTES", free * 4)
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.create(repo, base / "w1")
    assert raised.value.reason == DISK_SPACE
    assert not exists(base / "w1")
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


async def test_disk_check_numbers(repo: Path, manager: WorktreeManager, monkeypatch: Any) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_MIN_FREE_BYTES", 100)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_FREE_SPACE_FACTOR", 2.0)
    small = await manager.check_disk(repo / "not" / "made" / "yet", 10)
    assert small.needed == 100 and small.free > 0 and small.enough
    large = await manager.check_disk(repo, 1000)
    assert large.needed == 2000
    huge = await manager.check_disk(repo, shutil.disk_usage(repo).free)
    assert not huge.enough
    exact = worktrees.DiskCheck(free=2000, needed=2000)
    assert exact.enough and not worktrees.DiskCheck(free=1999, needed=2000).enough


async def test_failed_worktree_add_leaves_nothing(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    original = manager._git

    async def failing(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("worktree", "add"):
            # Half made, as a checkout that stopped part way leaves it.
            target = Path(args[3])
            if not exists(target):
                write(target / "partial.txt", "partial\n")
            raise worktrees.GitError(args, 128, b"simulated")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", failing)
    with pytest.raises(worktrees.GitError):
        await manager.create(repo, base / "w1")
    assert git(repo, "for-each-ref", REF_PREFIX) == ""
    assert not exists(base / "w1")
    # A directory that was there (empty) before stays.
    (base / "given").mkdir()
    with pytest.raises(worktrees.GitError):
        await manager.create(repo, base / "given")
    assert (base / "given").is_dir()
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


async def test_create_refuses_a_file_as_target(repo: Path, manager: WorktreeManager, base: Path) -> None:
    write(base / "file", "x")
    with pytest.raises(WorktreeError):
        await manager.create(repo, base / "file")


async def test_git_version_is_asked_once(repo: Path, manager: WorktreeManager, monkeypatch: Any) -> None:
    asked = []
    original = manager._run

    async def counting(cwd: Any, args: list[str], **kwargs: Any) -> Any:
        if args == ["--version"]:
            asked.append(1)
        return await original(cwd, args, **kwargs)

    monkeypatch.setattr(manager, "_run", counting)
    await manager.probe(repo)
    await manager.probe(repo)
    assert len(asked) == 1


async def test_checkout_size(repo: Path, manager: WorktreeManager) -> None:
    head = git(repo, "rev-parse", "HEAD").strip()
    expected = sum(len(data if isinstance(data, bytes) else data.encode()) for data in BASE_FILES.values())
    assert await manager._checkout_size(repo, head) == expected
    # A submodule link has no size; it is not counted.
    listing = git(repo, "ls-tree", head) + f"160000 commit {head}\tvendored\n"
    tree = git(repo, "mktree", input=listing.encode()).strip()
    linked = git(repo, "commit-tree", tree, "-m", "linked").strip()
    assert await manager._checkout_size(repo, linked) == expected


def test_worktree_dir_path(tmp_path: Path) -> None:
    paths = HostPaths(tmp_path)
    assert paths.worktrees_dir() == tmp_path / "wt"
    assert paths.worktree_dir("abc123") == tmp_path / "wt" / "abc123"
    with pytest.raises(ValueError):
        paths.worktree_dir("../up")


# -- landing -------------------------------------------------------------------


async def test_clean_merge_of_different_files(repo: Path, manager: WorktreeManager, base: Path) -> None:
    write(repo / "src" / "app.py", "print('hello')\nprint('mine, uncommitted')\n")
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "one\ntwo\nthree\nfour\nfive\nsix\n")
    write(tree.path / "added.txt", "made in the worktree\n")
    # The user keeps working while the side session does.
    write(repo / "b.txt", "alpha\nbeta\ngamma\ndelta\n")
    write(repo / "notes.txt", "a new file of the user's\n")
    head, index = git(repo, "rev-parse", "HEAD"), (repo / ".git" / "index").read_bytes()
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert read(repo / "a.txt") == b"one\ntwo\nthree\nfour\nfive\nsix\n"
    assert read(repo / "added.txt") == b"made in the worktree\n"
    assert read(repo / "b.txt") == b"alpha\nbeta\ngamma\ndelta\n"
    assert read(repo / "notes.txt") == b"a new file of the user's\n"
    assert read(repo / "src" / "app.py") == b"print('hello')\nprint('mine, uncommitted')\n"
    assert {(change.path, change.status) for change in landed.files} == {("a.txt", "modified"), ("added.txt", "added")}
    assert (landed.lines_added, landed.lines_deleted) == (2, 0)
    assert git(repo, "rev-parse", "HEAD") == head
    assert (repo / ".git" / "index").read_bytes() == index
    assert "?? added.txt" in git(repo, "status", "--porcelain")


async def test_nothing_to_land(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(repo / "b.txt", "changed by the user\n")
    before = digest(repo)
    result = await manager.merge_back(tree)
    assert result.status == "unchanged" and result.files == ()
    assert digest(repo) == before


async def test_same_line_conflict_changes_no_byte(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "one\ntwo\nworktree\nfour\nfive\n")
    write(tree.path / "other.txt", "would land if there were no conflict\n")
    write(repo / "a.txt", "one\ntwo\nproject\nfour\nfive\n")
    before = digest(repo)
    state = repository_state(repo)
    result = await manager.merge_back(tree)
    assert result.status == "conflict"
    assert result.conflicts == ("a.txt",)
    assert digest(repo) == before
    assert repository_state(repo) == state


async def test_conflict_prepared_resolved_and_landed(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "one\ntwo\nworktree\nfour\nfive\n")
    write(tree.path / "extra.txt", "extra\n")
    write(repo / "a.txt", "one\ntwo\nproject\nfour\nfive\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    prepared = await manager.prepare_conflicts(tree, conflict)
    assert prepared.start == conflict.current != tree.start
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == conflict.current
    marked = read(tree.path / "a.txt").decode()
    assert "<<<<<<<" in marked and "project" in marked and "worktree" in marked
    assert read(tree.path / "extra.txt") == b"extra\n"
    write(tree.path / "a.txt", "one\ntwo\nproject and worktree\nfour\nfive\n")
    landed = await manager.merge_back(prepared)
    assert landed.status == "landed", landed.detail
    assert read(repo / "a.txt") == b"one\ntwo\nproject and worktree\nfour\nfive\n"
    assert read(repo / "extra.txt") == b"extra\n"
    reopened = await manager.open(repo, tree.path)
    assert reopened == replace(prepared, start=landed.result)  # the landing moved it on


async def test_prepare_conflicts_refuses_a_changed_worktree(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    write(tree.path / "late.txt", "written after the merge was tried\n")
    with pytest.raises(WorktreeError):
        await manager.prepare_conflicts(tree, conflict)
    assert read(tree.path / "a.txt") == b"worktree\n"



async def test_prepare_conflicts_refuses_a_landing(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed"
    with pytest.raises(WorktreeError):
        await manager.prepare_conflicts(tree, landed)
    # The landing moved the start on to what landed; preparing did not move it.
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == landed.result


async def test_kinds_of_change(repo: Path, manager: WorktreeManager, base: Path) -> None:
    write(repo / "tool.sh", "#!/bin/sh\necho hi\n")
    write(repo / "image.bin", bytes(range(256)) * 4)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "more")
    tree = await manager.create(repo, base / "w1")
    w = tree.path
    os.remove(w / "b.txt")
    os.rename(w / "src" / "util.py", w / "src" / "helpers.py")
    write(w / "pkg" / "deep" / "mod.py", "x = 1\n")
    write(w / "image.bin", bytes(reversed(range(256))) * 4 + b"\0tail")
    write(w / "blob.dat", b"\x00\x01\x02\xff" * 100)
    write(w / "empty.txt", b"")
    write(w / "spaced.txt", "trailing spaces   \n\ttab\n")
    git(repo, "config", "apply.whitespace", "error")
    if not WINDOWS:
        os.chmod(w / "tool.sh", 0o755)
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert not exists(repo / "b.txt") and not exists(repo / "src" / "util.py")
    assert read(repo / "src" / "helpers.py") == BASE_FILES["src/util.py"].encode()
    assert read(repo / "pkg" / "deep" / "mod.py") == b"x = 1\n"
    assert read(repo / "image.bin") == read(w / "image.bin")
    assert read(repo / "blob.dat") == read(w / "blob.dat")
    assert exists(repo / "empty.txt") and read(repo / "empty.txt") == b""
    assert read(repo / "spaced.txt") == b"trailing spaces   \n\ttab\n"
    statuses = {change.path: change for change in landed.files}
    assert statuses["b.txt"].status == "deleted" and statuses["src/helpers.py"].status == "added"
    assert statuses["image.bin"].added is None and statuses["image.bin"].status == "modified"
    if not WINDOWS:
        assert os.stat(repo / "tool.sh").st_mode & stat.S_IXUSR
        assert statuses["tool.sh"].status == "modified"
    assert digest(repo) == digest(w)


@pytest.mark.parametrize(
    ("config", "attributes", "touched_ending"),
    [
        ({}, None, b"\n"),
        ({"core.autocrlf": "true"}, None, b"\r\n"),
        ({"core.autocrlf": "input"}, None, b"\n"),
        ({}, "*.txt text eol=crlf\n", b"\r\n"),
        ({"core.autocrlf": "true"}, "*.txt text eol=lf\n", b"\n"),
    ],
    ids=["plain", "autocrlf", "autocrlf-input", "eol-crlf", "eol-lf"],
)
async def test_line_endings(
    tmp_path: Path, manager: WorktreeManager, base: Path, config: dict[str, str], attributes: str | None, touched_ending: bytes
) -> None:
    repo = make_repo(tmp_path / "repo", {"seed.txt": "seed\n"})
    for key, value in config.items():
        git(repo, "config", key, value)
    if attributes:
        write(repo / ".gitattributes", attributes)
    # Written the way a user on either system might have them, then committed.
    write(repo / "crlf.txt", b"one\r\ntwo\r\n")
    write(repo / "lf.txt", b"one\ntwo\n")
    write(repo / "touched.txt", b"keep\nchange\n")
    write(repo / "raw.bin", b"bin\r\nary\n\0")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "endings")
    # Uncommitted, with endings git would turn around on its next write.
    write(repo / "mixed.txt", b"mixed\r\nendings\n")
    write(repo / "crlf.txt", b"one\r\ntwo\r\nthree\r\n")
    # Git would refuse to add some of these itself; a snapshot must not.
    git(repo, "config", "core.safecrlf", "true")
    before = digest(repo)
    tree = await manager.create(repo, base / "w1")
    content = read(tree.path / "touched.txt").replace(b"change", b"changed")
    write(tree.path / "touched.txt", content)
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert [change.path for change in landed.files] == ["touched.txt"]
    after = digest(repo)
    assert {name: value for name, value in after.items() if name != "touched.txt"} == {
        name: value for name, value in before.items() if name != "touched.txt"
    }
    assert read(repo / "touched.txt") == b"keep" + touched_ending + b"changed" + touched_ending


async def test_unusual_names(repo: Path, manager: WorktreeManager, base: Path) -> None:
    write(repo / "中文 目录" / "文件 一.txt", "你好\n")
    tree = await manager.create(repo, base / "w1")
    assert read(tree.path / "中文 目录" / "文件 一.txt") == "你好\n".encode()
    write(tree.path / "中文 目录" / "文件 一.txt", "你好，世界\n")
    write(tree.path / "with space.txt", "spaced\n")
    write(tree.path / "引号\"和'.txt" if not WINDOWS else tree.path / "引号'和.txt", "quotes\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert read(repo / "中文 目录" / "文件 一.txt") == "你好，世界\n".encode()
    assert read(repo / "with space.txt") == b"spaced\n"
    assert "中文 目录/文件 一.txt" in {change.path for change in landed.files}


async def test_long_paths(repo: Path, manager: WorktreeManager, base: Path) -> None:
    deep = Path(*[f"level-{index:02d}-with-a-long-name" for index in range(10)])
    assert len(str(repo / deep)) > 260
    write(repo / deep / "kept.txt", "kept\n")
    tree = await manager.create(repo, base / "w1")
    assert read(tree.path / deep / "kept.txt") == b"kept\n"
    write(tree.path / deep / "kept.txt", "changed\n")
    write(tree.path / deep / "more" / "new.txt", "new\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert read(repo / deep / "kept.txt") == b"changed\n"
    assert read(repo / deep / "more" / "new.txt") == b"new\n"
    await manager.remove(tree)
    assert not exists(tree.path)


async def test_project_in_a_subdirectory(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo / "src", base / "w1")
    assert tree.subpath == "src" and tree.workdir == tree.path / "src"
    assert os.path.samefile(tree.repo_root, repo)
    write(tree.workdir / "app.py", "print('changed in the worktree')\n")
    write(repo / "README.md", "# changed outside the project directory\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed"
    assert [change.path for change in landed.files] == ["src/app.py"]
    assert read(repo / "src" / "app.py") == b"print('changed in the worktree')\n"
    assert read(repo / "README.md") == b"# changed outside the project directory\n"
    reopened = await manager.open(repo / "src", tree.path)
    assert reopened == replace(tree, start=landed.result)


async def test_large_untracked_files_left_out(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    big = b"B" * 1001
    write(repo / "data" / "big.bin", big)
    write(repo / "data" / "small.bin", b"s" * 1000)
    write(repo / "a.txt", "x" * 5000)  # tracked: always in
    shot = await manager.snapshot(repo)
    assert shot.skipped == ("data/big.bin",)
    assert "data/small.bin" in tree_files(repo, shot.commit)
    hashed = git(repo, "hash-object", "--stdin", input=big).strip()
    assert not git_succeeds(repo, "cat-file", "-e", hashed)
    tree = await manager.create(repo, base / "w1")
    assert not exists(tree.path / "data" / "big.bin")
    assert read(tree.path / "a.txt") == b"x" * 5000
    write(tree.path / "data" / "made.bin", b"M" * 2000)
    write(tree.path / "b.txt", "changed\n")
    big_before = read(repo / "data" / "big.bin")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed"
    assert landed.skipped == ("data/made.bin",)
    assert not exists(repo / "data" / "made.bin")
    assert read(repo / "data" / "big.bin") == big_before
    assert [change.path for change in landed.files] == ["b.txt"]


async def test_two_worktrees_at_once_land_as_one_after_the_other(tmp_path: Path, manager: WorktreeManager, base: Path) -> None:
    async def run(repo: Path, together: bool) -> dict[str, tuple[str, bool]]:
        first = await manager.create(repo, base / f"{repo.name}-1")
        second = await manager.create(repo, base / f"{repo.name}-2")
        write(first.path / "a.txt", "one\ntwo\nthree\nfour\nfive\nfrom the first\n")
        write(first.path / "first.txt", "first\n")
        write(second.path / "b.txt", "from the second\nalpha\nbeta\ngamma\n")
        write(second.path / "second.txt", "second\n")
        if together:
            results = await asyncio.gather(manager.merge_back(first), manager.merge_back(second))
        else:
            results = [await manager.merge_back(first), await manager.merge_back(second)]
        assert [result.status for result in results] == ["landed", "landed"]
        return digest(repo)

    together = await run(make_repo(tmp_path / "together"), True)
    apart = await run(make_repo(tmp_path / "apart"), False)
    assert together == apart
    assert together["first.txt"] and together["second.txt"]


async def test_landings_on_one_repository_do_not_overlap(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    first = await manager.create(repo, base / "w1")
    second = await manager.create(repo, base / "w2")
    write(first.path / "one.txt", "1\n")
    write(second.path / "two.txt", "2\n")
    running, most = 0, 0
    original = manager._land_on_working_files

    async def counted(*args: Any) -> MergeResult:
        nonlocal running, most
        running += 1
        most = max(most, running)
        try:
            await asyncio.sleep(0.05)
            return await original(*args)
        finally:
            running -= 1

    monkeypatch.setattr(manager, "_land_on_working_files", counted)
    results = await asyncio.gather(manager.merge_back(first), manager.merge_back(second))
    assert [result.status for result in results] == ["landed", "landed"]
    assert most == 1


# -- a landing that fails half way ---------------------------------------------


async def test_failed_write_puts_everything_back(repo: Path, manager: WorktreeManager, base: Path) -> None:
    write(repo / "z" / "locked.txt", "locked\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "locked")
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "changed first\n")
    write(tree.path / "new" / "deeper" / "made.txt", "new\n")
    write(tree.path / "z" / "locked.txt", "cannot be written\n")
    write(repo / "README.md", "# the user's, uncommitted\n")
    before = digest(repo)
    if WINDOWS:
        import msvcrt  # noqa: F401  (Windows only)

        holder = open(repo / "z" / "locked.txt", "rb")  # noqa: SIM115
    else:
        if os.geteuid() == 0:
            pytest.skip("root writes into read-only directories")
        os.chmod(repo / "z", 0o555)
    try:
        result = await manager.merge_back(tree)
    finally:
        if WINDOWS:
            holder.close()
        else:
            os.chmod(repo / "z", 0o755)
    assert result.status == "failed", result
    assert result.left_changed == ()
    assert digest(repo) == before
    assert not exists(repo / "new")


async def test_mismatch_after_writing_puts_everything_back(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    os.remove(tree.path / "b.txt")
    write(tree.path / "fresh" / "new.txt", "new\n")
    before = digest(repo)
    original = manager._apply

    async def corrupting(root: Path, patch: bytes) -> Any:
        done = await original(root, patch)
        write(root / "a.txt", "not what was merged\n")
        return done

    monkeypatch.setattr(manager, "_apply", corrupting)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and "read back" in result.detail
    assert digest(repo) == before
    assert not exists(repo / "fresh")


async def test_deleted_file_still_there_is_a_mismatch(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    tree = await manager.create(repo, base / "w1")
    os.remove(tree.path / "b.txt")
    before = digest(repo)
    original = manager._apply

    async def resurrecting(root: Path, patch: bytes) -> Any:
        done = await original(root, patch)
        write(root / "b.txt", "back again\n")
        return done

    monkeypatch.setattr(manager, "_apply", resurrecting)
    result = await manager.merge_back(tree)
    assert result.status == "failed"
    assert digest(repo) == before


async def test_files_that_cannot_be_put_back_are_reported(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged a\n")
    write(tree.path / "b.txt", "merged b\n")

    async def failing(root: Path, patch: bytes) -> Any:
        write(root / "a.txt", "half written\n")
        write(root / "b.txt", "half written\n")
        return worktrees._Run(1, b"", b"simulated failure")

    real_restore = worktrees._restore_one

    def stuck_on_b(root: Path, entry: Any) -> bool:
        return False if entry.name == "b.txt" else real_restore(root, entry)

    monkeypatch.setattr(manager, "_apply", failing)
    monkeypatch.setattr(worktrees, "_restore_one", stuck_on_b)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and "simulated failure" in result.detail
    assert result.left_changed == ("b.txt",)
    assert read(repo / "a.txt") == BASE_FILES["a.txt"].encode()
    assert read(repo / "b.txt") == b"half written\n"
    # The copy of what b.txt held is kept, and said where.
    kept = Path(result.kept_copies)
    manifest = json.loads((kept / "manifest.json").read_text(encoding="utf-8"))
    (index,) = [key for key, name in manifest.items() if name == "b.txt"]
    assert read(kept / index) == BASE_FILES["b.txt"].encode()
    shutil.rmtree(kept)


async def test_changes_that_no_longer_apply_change_nothing(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(tree.path / "c.txt", "new\n")
    original = manager._snapshot

    async def user_types_meanwhile(root: Path, message: str, start: Any = None) -> Any:
        shot = await original(root, message, start)
        if "before landing" in message:
            write(repo / "c.txt", "the user made it first\n")
        return shot

    monkeypatch.setattr(manager, "_snapshot", user_types_meanwhile)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and "do not apply" in result.detail
    assert read(repo / "c.txt") == b"the user made it first\n"
    assert read(repo / "a.txt") == BASE_FILES["a.txt"].encode()


# -- changes, removal, reconciling ---------------------------------------------


async def test_has_changes(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    assert not await manager.has_changes(tree)
    write(repo / "a.txt", "the project changing is not the worktree changing\n")
    assert not await manager.has_changes(tree)
    write(tree.path / "new.txt", "new\n")
    assert await manager.has_changes(tree)
    await asyncio.to_thread(shutil.rmtree, _fs(tree.path))
    assert not await manager.has_changes(tree)


async def test_remove(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "unlanded.txt", "x\n")
    if not WINDOWS:
        os.chmod(tree.path / "unlanded.txt", 0o444)
    await manager.remove(tree)
    assert not exists(tree.path)
    assert "w1" not in registered(repo)
    assert git(repo, "for-each-ref", REF_PREFIX) == ""
    await manager.remove(tree)  # again: nothing left, no error


async def test_remove_after_the_directory_was_deleted(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    await asyncio.to_thread(shutil.rmtree, _fs(tree.path))
    await manager.remove(tree)
    assert "w1" not in registered(repo)
    assert not list((repo / ".git" / "worktrees").glob("*")) if (repo / ".git" / "worktrees").exists() else True
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


async def test_remove_with_a_read_only_file(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    tree = await manager.create(repo, base / "w1")
    other = await manager.create(repo, base / "w2")
    write(tree.path / "read-only.txt", "x\n")
    os.chmod(_fs(tree.path / "read-only.txt"), stat.S_IREAD)
    await manager.remove(tree)
    assert not exists(tree.path)
    assert "w1" not in registered(repo) and "w2" in registered(repo) and exists(other.path)
    assert git(other.path, "status", "--porcelain") == ""


async def test_reconcile(repo: Path, manager: WorktreeManager, base: Path, tmp_path: Path) -> None:
    known = await manager.create(repo, base / "known")
    clean = await manager.create(repo, base / "clean")
    changed = await manager.create(repo, base / "changed")
    gone = await manager.create(repo, base / "gone")
    elsewhere = await manager.create(repo, tmp_path / "elsewhere" / "other")
    write(changed.path / "work.txt", "not landed\n")
    write(known.path / "work.txt", "not landed\n")
    await asyncio.to_thread(shutil.rmtree, _fs(gone.path))
    git(repo, "update-ref", f"{REF_PREFIX}orphan/start", "HEAD")
    git(repo, "update-ref", f"{REF_PREFIX}remembered/start", "HEAD")
    stranger = base / "stranger"
    write(stranger / "x.txt", "not a worktree of this repository\n")
    report = await manager.reconcile(repo, base, ["known", "remembered"])
    assert sorted(report.removed) == ["clean", "gone"]
    assert [path.name for path in report.kept] == ["changed"]
    assert report.refs_removed == ["orphan"]
    assert exists(known.path) and exists(changed.path) and exists(elsewhere.path) and exists(stranger)
    assert not exists(clean.path)
    assert registered(repo) == {"repo", "known", "changed", "other"}
    refs = git(repo, "for-each-ref", "--format=%(refname)", REF_PREFIX).split()
    assert sorted(refs) == sorted(f"{REF_PREFIX}{name}/start" for name in ("known", "changed", "other", "remembered"))
    again = await manager.reconcile(repo, base, ["known", "remembered"])
    assert again.removed == [] and [path.name for path in again.kept] == ["changed"]


async def test_reconcile_leaves_a_worktree_without_its_ref_alone(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    git(repo, "update-ref", "-d", f"{REF_PREFIX}w1/start")
    report = await manager.reconcile(repo, base, [])
    assert report.kept == [] and report.removed == [] and exists(tree.path / "a.txt")
    assert "w1" in registered(repo)


async def test_git_timeout_kills_the_command(repo: Path, manager: WorktreeManager, monkeypatch: Any) -> None:
    started = []
    spawn = asyncio.create_subprocess_exec

    async def hanging(*command: Any, **kwargs: Any) -> Any:
        # Whatever was asked, a process that would not finish by itself.
        process = await spawn(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", hanging)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_WORK_SECONDS", 0.5)
    began = asyncio.get_running_loop().time()
    with pytest.raises(worktrees.GitError) as raised:
        await manager._git(repo, "status")
    assert raised.value.returncode is None
    assert asyncio.get_running_loop().time() - began < 30
    assert started and started[0].returncode is not None  # killed and waited for


def test_long_path_form() -> None:
    if WINDOWS:
        assert _fs("C:\\x\\y") == "\\\\?\\C:\\x\\y"
        assert _fs("\\\\server\\share\\x") == "\\\\?\\UNC\\server\\share\\x"
        assert _fs("\\\\?\\C:\\x") == "\\\\?\\C:\\x"
    else:
        assert _fs("/x/y") == "/x/y"


def test_worktree_handle() -> None:
    root = Path("/repo")
    tree = Worktree("w1", root, Path("/wt/w1"), "", "abc")
    assert tree.workdir == Path("/wt/w1")
    assert Worktree("w1", root, Path("/wt/w1"), "src/sub", "abc").workdir == Path("/wt/w1/src/sub")


def make_nested(path: Path) -> None:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    write(path / "inside.txt", "a repository of its own\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "nested")


async def test_probe_nested_repository(repo: Path, manager: WorktreeManager) -> None:
    make_nested(repo / "vendor" / "lib")
    found = await manager.probe(repo)
    assert found.reason == NESTED_REPOSITORY and "vendor/lib/" in found.detail


async def test_ignored_nested_repository_is_fine(repo: Path, manager: WorktreeManager, base: Path) -> None:
    make_nested(repo / "ignored" / "lib")
    assert (await manager.probe(repo)).usable
    tree = await manager.create(repo, base / "w1")
    assert not exists(tree.path / "ignored")
    assert not any(name.startswith("ignored/") for name in tree_files(repo, tree.start))


async def test_repository_made_in_a_worktree_is_left_out_and_reported(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    make_nested(tree.path / "cloned")
    write(tree.path / "a.txt", "changed too\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert landed.skipped == ("cloned/",)
    assert [change.path for change in landed.files] == ["a.txt"]
    assert not exists(repo / "cloned")
    assert "cloned" not in tree_files(repo, landed.result)


async def test_probe_work_tree_elsewhere(repo: Path, tmp_path: Path, manager: WorktreeManager) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    git(repo, "config", "core.worktree", elsewhere.as_posix())
    assert (await manager.probe(repo)).reason == NOT_A_REPOSITORY


async def test_configuration_from_the_environment_is_not_taken(repo: Path, manager: WorktreeManager, monkeypatch: Any) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.bare")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    assert (await manager.probe(repo)).usable


async def test_merge_that_git_cannot_do_raises(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "changed\n")
    before = digest(repo)
    git(repo, "update-ref", "-d", f"{REF_PREFIX}w1/start")  # so the handle's start is used
    with pytest.raises(worktrees.GitError):
        await manager.merge_back(Worktree(tree.id, tree.repo_root, tree.path, "", "0" * 40))
    assert digest(repo) == before


async def test_files_that_cannot_be_copied_aside_change_nothing(repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "changed\n")
    before = digest(repo)

    def failing(*args: Any) -> Any:
        raise PermissionError("simulated")

    monkeypatch.setattr(worktrees, "_back_up", failing)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and "copied aside" in result.detail
    assert digest(repo) == before


async def test_only_deletions_land(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    os.remove(tree.path / "b.txt")
    os.remove(tree.path / "src" / "app.py")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert not exists(repo / "b.txt") and not exists(repo / "src" / "app.py")
    assert (landed.lines_added, landed.lines_deleted) == (0, 4)


def test_putting_back_whatever_a_failed_write_left(tmp_path: Path) -> None:
    root = tmp_path / "project"
    backup = tmp_path / "backup"
    backup.mkdir()
    write(root / "becomes_dir.txt", "was a file\n")
    write(root / "unchanged.txt", "never written\n")
    write(root / "changed.txt", "before\n")
    names = ["becomes_dir.txt", "unchanged.txt", "changed.txt", "new/deep/made.txt", "fresh.txt"]
    if not WINDOWS:
        write(root / "target.txt", "the link's target\n")
        os.symlink("target.txt", root / "link")
        write(root / "plain.txt", "plain\n")
        write(root / "mode.sh", "#!/bin/sh\n")
        os.chmod(root / "mode.sh", 0o755)
        names += ["link", "plain.txt", "mode.sh"]
    before = digest(root)
    saved = worktrees._back_up(root, names, backup)
    # What a write that stopped part way might leave.
    os.remove(root / "becomes_dir.txt")
    write(root / "becomes_dir.txt" / "inner.txt", "x\n")
    write(root / "changed.txt", "after\n")
    write(root / "new" / "deep" / "made.txt", "made\n")
    write(root / "fresh.txt", "fresh\n")
    # Left as it was, and could not be written to: it needs no putting back.
    os.chmod(root / "unchanged.txt", stat.S_IREAD)
    os.chmod(next(entry.copy for entry in saved if entry.name == "unchanged.txt"), stat.S_IREAD)
    if not WINDOWS:
        os.remove(root / "link")
        write(root / "link", "a file where the link was\n")
        os.remove(root / "plain.txt")
        os.symlink("target.txt", root / "plain.txt")
        os.chmod(root / "mode.sh", 0o644)
    left = worktrees._restore(root, saved)
    os.chmod(root / "unchanged.txt", stat.S_IREAD | stat.S_IWRITE)
    assert left == []
    assert digest(root) == before
    assert not exists(root / "new") and not exists(root / "fresh.txt")
    if not WINDOWS:
        assert os.readlink(root / "link") == "target.txt"
        assert not os.path.islink(root / "plain.txt")
        assert read(root / "target.txt") == b"the link's target\n"


def test_relative_paths() -> None:
    root = Path(os.path.abspath("project"))
    assert worktrees._relative(root, root) == ""
    assert worktrees._relative(root / "a" / "b", root) == "a/b"
    assert worktrees._relative(root.parent, root) is None
    assert worktrees._relative(root.parent / "projectile", root) is None
    if WINDOWS:
        assert worktrees._relative(Path("Q:/elsewhere"), Path("C:/project")) is None


async def test_a_git_command_that_fails_raises(repo: Path, manager: WorktreeManager, base: Path) -> None:
    write(base / "a-file", "x")
    with pytest.raises(worktrees.GitError) as raised:
        await manager.create(repo, base / "a-file" / "w1")
    assert raised.value.returncode not in (0, None) and raised.value.git_args[:2] == ("worktree", "add")
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


@pytest.mark.parametrize("mark", ["--assume-unchanged", "--skip-worktree"])
async def test_snapshot_sees_files_the_index_is_told_to_ignore(repo: Path, manager: WorktreeManager, mark: str) -> None:
    git(repo, "update-index", mark, "a.txt")
    write(repo / "a.txt", "changed behind the index's back\n")
    index = (repo / ".git" / "index").read_bytes()
    shot = await manager.snapshot(repo)
    assert git(repo, "show", f"{shot.commit}:a.txt") == "changed behind the index's back\n"
    assert (repo / ".git" / "index").read_bytes() == index


async def test_snapshot_of_a_split_index_leaves_no_files(repo: Path, manager: WorktreeManager) -> None:
    git(repo, "config", "core.splitIndex", "true")
    git(repo, "config", "splitIndex.maxPercentChange", "0")  # every write makes a new shared part
    git(repo, "update-index", "--split-index")
    write(repo / "a.txt", "changed\n")
    write(repo / "new.txt", "new\n")
    before = sorted(path.name for path in (repo / ".git").iterdir())
    shot = await manager.snapshot(repo)
    assert sorted(path.name for path in (repo / ".git").iterdir()) == before
    assert {"a.txt", "new.txt"} <= tree_files(repo, shot.commit)
    assert git(repo, "show", f"{shot.commit}:a.txt") == "changed\n"


async def test_snapshot_without_an_index_file(repo: Path, manager: WorktreeManager) -> None:
    os.remove(repo / ".git" / "index")
    write(repo / "a.txt", "changed\n")
    shot = await manager.snapshot(repo)
    assert git(repo, "show", f"{shot.commit}:a.txt") == "changed\n"
    assert "b.txt" in tree_files(repo, shot.commit)
    assert not (repo / ".git" / "index").exists()


# -- review round 1 -------------------------------------------------------------


async def test_error_after_writing_began_puts_everything_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    write(tree.path / "fresh" / "new.txt", "new\n")
    before = digest(repo)

    async def failing(*args: Any) -> bool:
        raise worktrees.GitError(["add"], 128, b"simulated")

    monkeypatch.setattr(manager, "_reads_back", failing)
    with pytest.raises(worktrees.GitError):
        await manager.merge_back(tree)
    assert digest(repo) == before
    assert not exists(repo / "fresh")


async def test_cancelled_after_writing_began_puts_everything_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    write(tree.path / "fresh" / "new.txt", "new\n")
    before = digest(repo)
    written = asyncio.Event()

    async def slow(*args: Any) -> bool:
        written.set()
        await asyncio.sleep(30)
        return True

    monkeypatch.setattr(manager, "_reads_back", slow)
    landing = asyncio.ensure_future(manager.merge_back(tree))
    await asyncio.wait_for(written.wait(), 30)
    assert read(repo / "a.txt") == b"merged\n"  # written, not checked yet
    landing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await landing
    assert digest(repo) == before
    assert not exists(repo / "fresh")


async def test_uninterrupted_finishes_then_passes_the_cancellation_on() -> None:
    import threading
    import time

    started = threading.Event()
    finished = []

    def work(value: int) -> int:
        started.set()
        time.sleep(0.3)
        finished.append(value)
        return value

    task = asyncio.ensure_future(worktrees._to_the_end(asyncio.to_thread(work, 7)))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [7]
    assert await worktrees._to_the_end(asyncio.to_thread(work, 8)) == 8


async def test_large_files_made_in_a_worktree_count_as_changes(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "model.bin", b"M" * 2000)
    assert await manager.has_changes(tree)
    report = await manager.reconcile(repo, base, [])
    assert [path.name for path in report.kept] == ["w1"] and exists(tree.path / "model.bin")


def swap_file_and_directory(tree: Worktree) -> None:
    shutil.rmtree(_fs(tree.path / "src"))
    write(tree.path / "src", "now a file\n")
    os.remove(tree.path / "b.txt")
    write(tree.path / "b.txt" / "inside.txt", "now a directory\n")


async def test_a_directory_becomes_a_file_and_back(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    await asyncio.to_thread(swap_file_and_directory, tree)
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert read(repo / "src") == b"now a file\n"
    assert read(repo / "b.txt" / "inside.txt") == b"now a directory\n"
    assert digest(repo) == digest(tree.path)


@pytest.mark.parametrize("written", [True, False], ids=["after-writing", "before-writing"])
async def test_a_failed_swap_is_put_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any, written: bool
) -> None:
    tree = await manager.create(repo, base / "w1")
    await asyncio.to_thread(swap_file_and_directory, tree)
    before = digest(repo)
    original = manager._apply

    async def then_fail(root: Path, patch: bytes) -> Any:
        if written:
            await original(root, patch)
        return worktrees._Run(1, b"", b"simulated")

    monkeypatch.setattr(manager, "_apply", then_fail)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and result.left_changed == ()
    assert digest(repo) == before
    assert (repo / "src").is_dir() and (repo / "b.txt").is_file()


async def test_marks_cleared_without_losing_staged_files(
    repo: Path, manager: WorktreeManager, monkeypatch: Any
) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    write(repo / "forced.log", "staged although ignored\n")
    git(repo, "add", "-f", "forced.log")
    write(repo / "staged-big.bin", b"B" * 2000)
    git(repo, "add", "staged-big.bin")
    git(repo, "update-index", "--assume-unchanged", "a.txt")
    write(repo / "a.txt", "changed behind the index\n")
    shot = await manager.snapshot(repo)
    names = tree_files(repo, shot.commit)
    assert {"forced.log", "staged-big.bin"} <= names
    assert shot.skipped == ()
    assert git(repo, "show", f"{shot.commit}:a.txt") == "changed behind the index\n"
    assert git(repo, "ls-files", "-v", "a.txt").startswith("h ")


async def test_one_lock_for_all_checkouts_of_a_repository(
    repo: Path, manager: WorktreeManager, base: Path, tmp_path: Path
) -> None:
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "--detach", str(linked))
    assert await manager._lock(repo) is await manager._lock(linked)
    results = await asyncio.gather(
        manager.create(repo, base / "same"), manager.create(linked, base / "same"), return_exceptions=True
    )
    made = [result for result in results if isinstance(result, Worktree)]
    refused = [result for result in results if isinstance(result, WorktreeError)]
    assert len(made) == 1 and len(refused) == 1
    assert git(repo, "rev-parse", f"{REF_PREFIX}same/start").strip() == made[0].start
    assert exists(made[0].path / "a.txt") and "same" in registered(repo)


async def test_open_and_remove_only_what_is_registered(
    repo: Path, manager: WorktreeManager, base: Path, tmp_path: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    stranger = tmp_path / "elsewhere" / "w1"
    write(stranger / "keep.txt", "not a worktree\n")
    with pytest.raises(WorktreeError):
        await manager.open(repo, stranger)
    await manager.remove(replace(tree, path=stranger))
    assert read(stranger / "keep.txt") == b"not a worktree\n"
    assert exists(tree.path / "a.txt")
    reopened = await manager.open(repo, tree.path)  # its refs were left alone
    assert reopened.start == tree.start


async def test_landed_work_is_no_longer_a_change(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    other = await manager.create(repo, base / "w2")
    write(tree.path / "a.txt", "landed\n")
    assert (await manager.merge_back(tree)).status == "landed"
    assert not await manager.has_changes(tree)
    write(tree.path / "b.txt", "after the landing\n")
    assert await manager.has_changes(tree)
    write(other.path / "a.txt", "landed\n")  # the same change, landed by the first already
    assert (await manager.merge_back(other)).status == "unchanged"
    report = await manager.reconcile(repo, base, [])
    assert report.removed == ["w2"] and [path.name for path in report.kept] == ["w1"]


async def test_remove_when_its_registration_went(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    shutil.rmtree(_fs(repo / ".git" / "worktrees" / "w1"))  # its registration gone, its .git file still there
    assert exists(tree.path / ".git") and "w1" not in registered(repo)
    await manager.remove(tree)
    assert not exists(tree.path)
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


# -- review round 2 -------------------------------------------------------------


@pytest.mark.parametrize("name", ["release notes", "a.b", "x.lock", "-dash", "a" * 65])
async def test_worktree_names_must_make_a_ref(repo: Path, manager: WorktreeManager, base: Path, name: str) -> None:
    with pytest.raises(ValueError):
        await manager.create(repo, base / name)
    assert git(repo, "for-each-ref", REF_PREFIX) == ""
    assert (await manager.create(repo, base / "Ok_name-1")).id == "Ok_name-1"


async def test_cancelled_right_after_the_ref_was_written(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    original = manager._git

    async def cancelled_after(cwd: Path, *args: str, **kwargs: Any) -> Any:
        done = await original(cwd, *args, **kwargs)
        if args[:1] == ("update-ref",) and len(args) == 3:
            raise asyncio.CancelledError
        return done

    monkeypatch.setattr(manager, "_git", cancelled_after)
    with pytest.raises(asyncio.CancelledError):
        await manager.create(repo, base / "w1")
    monkeypatch.setattr(manager, "_git", original)
    assert git(repo, "for-each-ref", REF_PREFIX) == ""
    assert (await manager.create(repo, base / "w1")).id == "w1"


async def test_two_repositories_making_at_one_target(manager: WorktreeManager, base: Path, tmp_path: Path) -> None:
    first = make_repo(tmp_path / "first")
    second = make_repo(tmp_path / "second", {"other.txt": "other\n"})
    results = await asyncio.gather(
        manager.create(first, base / "same"), manager.create(second, base / "same"), return_exceptions=True
    )
    made = [result for result in results if isinstance(result, Worktree)]
    assert len(made) == 1 and sum(isinstance(result, WorktreeError) for result in results) == 1
    assert exists(made[0].path / ("a.txt" if made[0].repo_root.name == "first" else "other.txt"))
    assert "same" in registered(made[0].repo_root)


async def test_prepare_conflicts_puts_the_worktree_back_when_the_ref_cannot_move(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(tree.path / "extra.txt", "extra\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    before = digest(tree.path)
    original = manager._git

    async def ref_locked(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start"):
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", ref_locked)
    with pytest.raises(worktrees.GitError):
        await manager.prepare_conflicts(tree, conflict)
    monkeypatch.setattr(manager, "_git", original)
    assert digest(tree.path) == before
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    prepared = await manager.prepare_conflicts(tree, conflict)
    assert "<<<<<<<" in read(prepared.path / "a.txt").decode()


async def test_the_main_checkout_is_never_taken_for_a_worktree(
    repo: Path, manager: WorktreeManager, tmp_path: Path
) -> None:
    git(repo, "update-ref", f"{REF_PREFIX}repo/start", "HEAD")
    before = digest(repo)
    report = await manager.reconcile(repo, tmp_path, [])
    assert report.removed == [] and report.kept == []
    await manager.remove(Worktree("repo", repo, repo, "", git(repo, "rev-parse", "HEAD").strip()))
    assert digest(repo) == before
    with pytest.raises(WorktreeError):
        await manager.open(repo, repo)


async def test_probe_git_failing_later_on(repo: Path, manager: WorktreeManager) -> None:
    (repo / ".git" / "index").write_bytes(b"not an index")
    found = await manager.probe(repo)
    assert found.reason == GIT_FAILED and found.repo_root is not None


async def test_probe_partial_clone(repo: Path, manager: WorktreeManager) -> None:
    git(repo, "config", "core.repositoryformatversion", "1")
    git(repo, "config", "extensions.partialClone", "origin")
    assert (await manager.probe(repo)).reason == worktrees.PARTIAL_CLONE


async def test_pathspec_settings_from_the_environment_are_not_taken(
    repo: Path, manager: WorktreeManager, monkeypatch: Any
) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    write(repo / "big.bin", b"B" * 2000)
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    shot = await manager.snapshot(repo)
    assert shot.skipped == ("big.bin",) and "big.bin" not in tree_files(repo, shot.commit)


# -- review round 3 -------------------------------------------------------------


async def test_reconcile_goes_on_past_a_broken_worktree(repo: Path, manager: WorktreeManager, base: Path) -> None:
    broken = await manager.create(repo, base / "w1")
    clean = await manager.create(repo, base / "w2")
    os.remove(broken.path / ".git")
    report = await manager.reconcile(repo, base, [])
    assert report.removed == ["w2"] and not exists(clean.path)
    assert [path.name for path, _why in report.failed] == ["w1"] and report.kept == []
    assert exists(broken.path / "a.txt")


async def test_patch_and_attribute_settings_from_the_environment_are_not_taken(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=0")
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "one\ntwo\nTHREE\nfour\nfive\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    write(repo / ".gitattributes", "*.txt filter=crypt\n")
    monkeypatch.setenv("GIT_ATTR_SOURCE", "4b825dc642cb6eb9a060e54bf8d69288fbee4904")  # the empty tree
    assert (await manager.probe(repo)).reason == CONTENT_FILTERS


# -- review round 4 -------------------------------------------------------------


async def test_landing_twice_merges_from_what_landed(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "one\ntwo\nB\nfour\nfive\n")
    first = await manager.merge_back(tree)
    assert first.status == "landed"
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == first.result
    write(tree.path / "a.txt", "one\ntwo\nC\nfour\nfive\n")
    write(repo / "b.txt", "the user, meanwhile\n")
    second = await manager.merge_back(tree)  # the old handle: the ref is what counts
    assert second.status == "landed", second.conflicts
    assert read(repo / "a.txt") == b"one\ntwo\nC\nfour\nfive\n"
    assert read(repo / "b.txt") == b"the user, meanwhile\n"
    assert (await manager.merge_back(tree)).status == "unchanged"


async def test_a_filter_set_up_in_a_worktree_is_refused_at_landing(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / ".gitattributes", "*.secret filter=crypt\n")
    write(tree.path / "key.secret", "hidden\n")
    before = digest(repo)
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.merge_back(tree)
    assert raised.value.reason == CONTENT_FILTERS and "key.secret" in raised.value.detail
    assert digest(repo) == before


async def test_a_ref_whose_tree_has_a_filter_is_refused(repo: Path, manager: WorktreeManager, base: Path) -> None:
    git(repo, "checkout", "-q", "-b", "filtered")
    write(repo / ".gitattributes", "*.bin filter=lfs\n")
    write(repo / "model.bin", "pointer\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "filtered")
    git(repo, "checkout", "-q", "main")
    assert (await manager.probe(repo)).usable
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.create(repo, base / "w1", FromRef("filtered"))
    assert raised.value.reason == CONTENT_FILTERS
    assert not exists(base / "w1") and git(repo, "for-each-ref", REF_PREFIX) == ""
    assert (await manager.create(repo, base / "w2", FromRef("main"))).id == "w2"


async def test_cancelled_while_the_start_moves_on_puts_the_start_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    before = digest(repo)
    original = manager._git
    moved = []

    async def cancelled_after_moving(cwd: Path, *args: str, **kwargs: Any) -> Any:
        done = await original(cwd, *args, **kwargs)
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and not moved:
            moved.append(args[2])
            raise asyncio.CancelledError
        return done

    monkeypatch.setattr(manager, "_git", cancelled_after_moving)
    with pytest.raises(asyncio.CancelledError):
        await manager.merge_back(tree)
    monkeypatch.setattr(manager, "_git", original)
    assert moved and moved[0] != tree.start
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    assert digest(repo) == before
    assert await manager.has_changes(tree)
    assert (await manager.merge_back(tree)).status == "landed"


# -- review round 5 -------------------------------------------------------------


@pytest.mark.parametrize(
    ("attributes", "config", "usable"),
    [
        ("*.txt merge=ours-driver\n", {"merge.ours-driver.driver": "cat %A"}, False),
        ("", {"merge.default": "ours-driver", "merge.ours-driver.driver": "cat %A"}, False),
        # Named but not configured here: git merges these as text.
        ("*.txt merge=ours-driver\n", {}, True),
        ("", {"merge.default": "ours-driver"}, True),
        ("*.txt merge=union\n", {}, True),
        ("*.txt -merge\n", {"merge.default": "binary"}, True),
        ("*.txt merge=union\n", {"merge.union.driver": "cat %A"}, False),
        ("", {"merge.default": "union", "merge.union.driver": "cat %A"}, False),
        # A driver with a name and no command: git stops the merge.
        ("*.txt merge=demo\n", {"merge.demo.name": "Demo"}, False),
        ("", {"merge.default": "demo", "merge.demo.name": "Demo"}, False),
        # A name with a space in it.
        ("", {"merge.default": "my drv", "merge.my drv.driver": "cat %A"}, False),
    ],
    ids=[
        "attribute",
        "default",
        "attribute-unconfigured",
        "default-unconfigured",
        "union",
        "built-in",
        "union-overridden",
        "default-overridden",
        "attribute-name-only",
        "default-name-only",
        "name-with-space",
    ],
)
async def test_probe_merge_drivers(
    repo: Path, manager: WorktreeManager, attributes: str, config: dict[str, str], usable: bool
) -> None:
    if attributes:
        write(repo / ".gitattributes", attributes)
    for key, value in config.items():
        git(repo, "config", key, value)
    found = await manager.probe(repo)
    assert found.usable is usable
    if not usable:
        assert found.reason == worktrees.MERGE_DRIVERS


async def test_a_conflict_from_before_a_landing_is_not_prepared(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    stale = await manager.merge_back(tree)
    assert stale.status == "conflict"
    write(repo / "a.txt", "worktree\n")  # the user took the worktree's
    assert (await manager.merge_back(tree)).status == "unchanged"
    with pytest.raises(WorktreeError):
        await manager.prepare_conflicts(tree, stale)
    assert read(tree.path / "a.txt") == b"worktree\n"


async def test_preparing_does_not_overwrite_what_only_the_worktree_has(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "data.bin", b"W" * 2000)
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "data.bin", b"p" * 10)
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict" and conflict.skipped == ("data.bin",)
    with pytest.raises(WorktreeError) as raised:
        await manager.prepare_conflicts(tree, conflict)
    assert "data.bin" in str(raised.value)
    assert read(tree.path / "data.bin") == b"W" * 2000 and read(tree.path / "a.txt") == b"worktree\n"


async def test_a_landed_file_that_grew_is_not_taken_for_deleted(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "data.bin", b"s" * 500)
    assert (await manager.merge_back(tree)).status == "landed"
    write(tree.path / "data.bin", b"L" * 2000)
    grown = await manager.merge_back(tree)
    assert grown.status == "landed" and grown.skipped == ()
    assert read(repo / "data.bin") == b"L" * 2000


async def test_a_filter_meeting_its_file_only_in_the_merge_is_refused(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(repo / ".gitattributes", "*.dat filter=crypt\n")
    write(tree.path / "sample.dat", "data\n")
    before = digest(repo)
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.merge_back(tree)
    assert raised.value.reason == CONTENT_FILTERS and "sample.dat" in raised.value.detail
    assert digest(repo) == before


async def test_a_directory_not_removed_entirely_keeps_what_finds_it_again(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    original_remove = worktrees._remove_tree
    shutil.rmtree(_fs(repo / ".git" / "worktrees" / "w1"))  # its registration gone (removed by hand)
    monkeypatch.setattr(worktrees, "_remove_tree", lambda path: None)  # a file still held
    with pytest.raises(WorktreeError):
        await manager.remove(tree)
    assert exists(tree.path / "a.txt")
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    monkeypatch.setattr(worktrees, "_remove_tree", original_remove)
    await manager.remove(tree)  # found again by its .git file
    assert not exists(tree.path)
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


async def test_registration_is_kept_while_the_directory_stays(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    monkeypatch.setattr(worktrees, "_remove_tree", lambda path: None)
    with pytest.raises(WorktreeError):
        await manager.remove(tree)
    assert "w1" in registered(repo)


async def test_probe_partial_clone_by_its_remote(repo: Path, manager: WorktreeManager) -> None:
    git(repo, "config", "remote.origin.url", "https://example.invalid/x.git")
    git(repo, "config", "remote.origin.promisor", "true")
    assert (await manager.probe(repo)).reason == worktrees.PARTIAL_CLONE
    git(repo, "config", "remote.origin.promisor", "false")
    assert (await manager.probe(repo)).usable


async def test_a_ref_with_submodules_is_refused(repo: Path, manager: WorktreeManager, base: Path) -> None:
    head = git(repo, "rev-parse", "HEAD").strip()
    listing = git(repo, "ls-tree", head) + f"160000 commit {head}\tvendored\n"
    tree = git(repo, "mktree", input=listing.encode()).strip()
    linked = git(repo, "commit-tree", tree, "-p", head, "-m", "linked").strip()
    git(repo, "update-ref", "refs/heads/linked", linked)
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.create(repo, base / "w1", FromRef("linked"))
    assert raised.value.reason == SUBMODULES
    assert not exists(base / "w1")


async def test_preparing_cancelled_after_the_start_moved_puts_both_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    before = digest(tree.path)
    original = manager._git
    moved = []

    async def cancelled_after_moving(cwd: Path, *args: str, **kwargs: Any) -> Any:
        done = await original(cwd, *args, **kwargs)
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and not moved:
            moved.append(args[2])
            raise asyncio.CancelledError
        return done

    monkeypatch.setattr(manager, "_git", cancelled_after_moving)
    with pytest.raises(asyncio.CancelledError):
        await manager.prepare_conflicts(tree, conflict)
    monkeypatch.setattr(manager, "_git", original)
    assert moved == [conflict.current]
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    assert digest(tree.path) == before
    prepared = await manager.prepare_conflicts(tree, conflict)
    assert "<<<<<<<" in read(prepared.path / "a.txt").decode()


# -- review round 6 -------------------------------------------------------------


async def test_a_checkout_failing_part_way_while_preparing_is_put_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    before = digest(tree.path)
    original = manager._git

    async def half_checked_out(cwd: Path, *args: str, **kwargs: Any) -> Any:
        done = await original(cwd, *args, **kwargs)
        if args[:1] == ("checkout",) and args[-1] != conflict.result:
            raise worktrees.GitError(args, 1, b"unable to write z.txt")
        return done

    monkeypatch.setattr(manager, "_git", half_checked_out)
    with pytest.raises(worktrees.GitError):
        await manager.prepare_conflicts(tree, conflict)
    monkeypatch.setattr(manager, "_git", original)
    assert digest(tree.path) == before
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    prepared = await manager.prepare_conflicts(tree, conflict)
    assert "<<<<<<<" in read(prepared.path / "a.txt").decode()


async def test_a_start_that_cannot_be_set_back_is_said(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    original = manager._git
    calls = []

    async def ref_stuck(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start"):
            calls.append(args[2])
            if len(calls) == 1:
                await original(cwd, *args, **kwargs)
                raise asyncio.CancelledError
            return worktrees._Run(1, b"", b"cannot lock ref")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", ref_stuck)
    with pytest.raises(asyncio.CancelledError) as raised:
        await manager.merge_back(tree)
    assert calls[1] == tree.start
    assert any("could not be set back" in note for note in getattr(raised.value, "__notes__", []))


# -- review round 7 -------------------------------------------------------------


def slow_restore(monkeypatch: Any, started: Any) -> None:
    import time

    real = worktrees._restore

    def slow(root: Path, saved: Any) -> Any:
        started.set()
        time.sleep(0.5)
        return real(root, saved)

    monkeypatch.setattr(worktrees, "_restore", slow)


async def test_putting_back_after_a_failed_write_is_not_cut_short(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading

    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    write(tree.path / "fresh" / "new.txt", "new\n")
    before = digest(repo)
    original = manager._apply

    async def then_fail(root: Path, patch: bytes) -> Any:
        await original(root, patch)
        return worktrees._Run(1, b"", b"simulated")

    monkeypatch.setattr(manager, "_apply", then_fail)
    started = threading.Event()
    slow_restore(monkeypatch, started)
    landing = asyncio.ensure_future(manager.merge_back(tree))
    began = await asyncio.to_thread(started.wait, 30)
    assert began  # written, failed, putting back
    assert read(repo / "a.txt") == b"merged\n"
    landing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await landing
    assert digest(repo) == before and not exists(repo / "fresh")


async def test_a_second_cancellation_does_not_keep_the_start_from_going_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading

    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    before = digest(repo)
    original = manager._git
    moved = asyncio.Event()

    async def moved_then_wait(cwd: Path, *args: str, **kwargs: Any) -> Any:
        done = await original(cwd, *args, **kwargs)
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and args[2] != tree.start:
            moved.set()
            await asyncio.sleep(30)  # cancelled here
        return done

    monkeypatch.setattr(manager, "_git", moved_then_wait)
    started = threading.Event()
    slow_restore(monkeypatch, started)
    landing = asyncio.ensure_future(manager.merge_back(tree))
    await asyncio.wait_for(moved.wait(), 30)
    landing.cancel()  # the first: putting back begins
    began = await asyncio.to_thread(started.wait, 30)
    assert began
    landing.cancel()  # the second, while putting back
    with pytest.raises(asyncio.CancelledError):
        await landing
    monkeypatch.setattr(manager, "_git", original)
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    assert digest(repo) == before
    assert await manager.has_changes(tree)


async def test_open_and_reconcile_while_the_project_is_mid_merge(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    kept = await manager.create(repo, base / "w1")
    orphan = await manager.create(repo, base / "w2")
    (repo / ".git" / "MERGE_HEAD").write_text(git(repo, "rev-parse", "HEAD"), encoding="utf-8")
    assert (await manager.probe(repo)).reason == OPERATION_IN_PROGRESS
    assert (await manager.open(repo, kept.path)).id == "w1"
    report = await manager.reconcile(repo, base, ["w1"])
    assert report.removed == ["w2"] and not exists(orphan.path)


async def test_no_landing_while_the_user_is_mid_merge(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged\n")
    (repo / ".git" / "rebase-merge").mkdir()
    before = digest(repo)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and "rebase" in result.detail
    assert digest(repo) == before
    assert await manager.has_changes(tree)


async def test_reconcile_reports_a_ref_it_could_not_remove(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    git(repo, "update-ref", f"{REF_PREFIX}orphan/start", "HEAD")
    git(repo, "update-ref", f"{REF_PREFIX}other/start", "HEAD")
    original = manager._delete_refs

    async def stuck(repo_root: Path, worktree_id: str) -> None:
        if worktree_id == "orphan":
            raise worktrees.GitError(["update-ref"], 128, b"cannot lock ref")
        await original(repo_root, worktree_id)

    monkeypatch.setattr(manager, "_delete_refs", stuck)
    report = await manager.reconcile(repo, base, [])
    assert report.refs_removed == ["other"]
    assert [str(path).replace(os.sep, "/") for path, _why in report.failed] == [f"{REF_PREFIX}orphan"]


async def test_an_empty_directory_given_stays_when_making_is_cut_short(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    given = base / "given"
    given.mkdir(parents=True)
    original = manager._git

    async def made_then_cancelled(cwd: Path, *args: str, **kwargs: Any) -> Any:
        done = await original(cwd, *args, **kwargs)
        if args[:2] == ("worktree", "add"):
            raise asyncio.CancelledError
        return done

    monkeypatch.setattr(manager, "_git", made_then_cancelled)
    with pytest.raises(asyncio.CancelledError):
        await manager.create(repo, given)
    assert given.is_dir() and not any(given.iterdir())
    assert "given" not in registered(repo)
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


def test_worktree_dir_takes_only_worktree_names(tmp_path: Path) -> None:
    paths = HostPaths(tmp_path)
    for bad in ("bad name", "a.b", "x" * 65, "-x"):
        with pytest.raises(ValueError):
            paths.worktree_dir(bad)
    assert paths.worktree_dir("ok-1_A").name == "ok-1_A"


# -- review round 8 -------------------------------------------------------------


async def test_a_large_landed_file_changed_again_lands_again(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "data.bin", b"s" * 500)
    assert (await manager.merge_back(tree)).status == "landed"
    write(tree.path / "data.bin", b"L" * 2000)
    assert (await manager.merge_back(tree)).status == "landed"
    write(tree.path / "data.bin", b"M" * 2000)
    third = await manager.merge_back(tree)
    assert third.status == "landed", third.conflicts
    assert read(repo / "data.bin") == b"M" * 2000


async def test_preparing_does_not_replace_a_directory_holding_what_only_the_worktree_has(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_UNTRACKED_MAX_BYTES", 1000)
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "data" / "blob.bin", b"W" * 2000)
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "data", "a file where the worktree has a directory\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    with pytest.raises(WorktreeError) as raised:
        await manager.prepare_conflicts(tree, conflict)
    assert "data" in str(raised.value)
    assert read(tree.path / "data" / "blob.bin") == b"W" * 2000


def test_what_a_checkout_would_overwrite(tmp_path: Path) -> None:
    write(tmp_path / "file.txt", "x")
    write(tmp_path / "data" / "blob.bin", "x")
    write(tmp_path / "plain", "a file")
    write(tmp_path / "saved" / "one.txt", "x")
    write(tmp_path / "kept", "a file the snapshot has")
    write(tmp_path / "sub" / "own.txt", "a repository's own")
    held = {"saved/one.txt", "kept"}

    def hits(written: list[str], nested: frozenset[str] = frozenset()) -> list[str]:
        return worktrees._in_the_way(tmp_path, written, held, set(nested))

    # Not the snapshot's: in the way.
    assert hits(["file.txt", "data", "plain/inside.txt", "new/thing.txt", "database"]) == ["data", "file.txt", "plain"]
    assert hits(["data/new.txt"]) == []  # beside what is there, in a directory
    # The snapshot's: replaced as the merge says.
    assert hits(["saved"]) == []  # a directory of saved files becomes a file
    assert hits(["kept/inside.txt"]) == []  # a saved file becomes a directory
    write(tmp_path / "saved" / "extra.log", "ignored, not saved")
    assert hits(["saved"]) == ["saved"]
    # Inside a repository of its own: always in the way.
    assert hits(["sub/new.txt"], frozenset({"sub"})) == ["sub/"]
    assert worktrees._parents("a/b/c") == ["a", "a/b"] and worktrees._parents("a") == []


async def test_cancelled_while_putting_back_says_what_could_not_be(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading
    import time

    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "merged a\n")
    write(tree.path / "b.txt", "merged b\n")
    original = manager._apply

    async def then_fail(root: Path, patch: bytes) -> Any:
        await original(root, patch)
        return worktrees._Run(1, b"", b"simulated")

    real_restore_one = worktrees._restore_one
    started = threading.Event()

    def slow_and_stuck_on_b(root: Path, entry: Any) -> bool:
        started.set()
        time.sleep(0.3)
        return False if entry.name == "b.txt" else real_restore_one(root, entry)

    monkeypatch.setattr(manager, "_apply", then_fail)
    monkeypatch.setattr(worktrees, "_restore_one", slow_and_stuck_on_b)
    landing = asyncio.ensure_future(manager.merge_back(tree))
    began = await asyncio.to_thread(started.wait, 30)
    assert began
    landing.cancel()
    with pytest.raises(asyncio.CancelledError) as raised:
        await landing
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "b.txt" in notes and "copies are in" in notes
    kept = notes.split("copies are in ")[1].strip()
    assert (Path(kept) / "manifest.json").exists()
    shutil.rmtree(kept)


@pytest.mark.parametrize("name", ["CON", "nul", "COM1", "Aux"])
def test_worktree_names_are_not_devices(name: str) -> None:
    from nekoclaw.host.paths import worktree_name

    with pytest.raises(ValueError):
        worktree_name(name)


# -- review round 9 -------------------------------------------------------------


async def test_preparing_does_not_check_out_a_filter_met_in_the_merge(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(tree.path / "sample.dat", "data\n")
    write(repo / "a.txt", "project\n")
    write(repo / ".gitattributes", "*.dat filter=crypt\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    before = digest(tree.path)
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.prepare_conflicts(tree, conflict)
    assert raised.value.reason == CONTENT_FILTERS
    assert digest(tree.path) == before


async def test_reconcile_keeps_the_ref_of_a_directory_whose_registration_went(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    await asyncio.to_thread(shutil.rmtree, _fs(repo / ".git" / "worktrees"))  # git gave up half way
    git(repo, "update-ref", f"{REF_PREFIX}orphan/start", "HEAD")
    report = await manager.reconcile(repo, base, [])
    assert [path.name for path, _why in report.failed] == ["w1"]
    assert report.refs_removed == ["orphan"]
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start
    await manager.remove(tree)
    assert not exists(tree.path) and git(repo, "for-each-ref", REF_PREFIX) == ""


async def test_a_relative_git_file_is_read_from_where_it_is(
    repo: Path, manager: WorktreeManager, base: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    admin = repo / ".git" / "worktrees" / "w1"
    relative = os.path.relpath(admin, tree.path).replace(os.sep, "/")
    os.remove(tree.path / ".git")  # hidden on Windows: not written over
    (tree.path / ".git").write_text(f"gitdir: {relative}\n", encoding="utf-8")
    await asyncio.to_thread(shutil.rmtree, _fs(repo / ".git" / "worktrees"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    await manager.remove(tree)
    assert not exists(tree.path) and git(repo, "for-each-ref", REF_PREFIX) == ""


def test_a_relative_registration_is_matched_from_where_it_is(tmp_path: Path) -> None:
    common = tmp_path / "repo" / ".git"
    entry = common / "worktrees" / "w1"
    entry.mkdir(parents=True)
    path = tmp_path / "wt" / "w1"
    path.mkdir(parents=True)
    (entry / "gitdir").write_text(os.path.relpath(path / ".git", entry).replace(os.sep, "/") + "\n", encoding="utf-8")
    worktrees._drop_registration(common, path)
    assert not entry.exists()


def test_pointers_that_are_not_utf8_are_not_ours(tmp_path: Path) -> None:
    path = tmp_path / "wt" / "w1"
    path.mkdir(parents=True)
    (path / ".git").write_bytes(b"gitdir: /some/where\xff/worktrees/w1\n")
    assert worktrees._points_into(path, tmp_path / "repo" / ".git") is False
    common = tmp_path / "repo" / ".git"
    entry = common / "worktrees" / "w1"
    entry.mkdir(parents=True)
    (entry / "gitdir").write_bytes(b"/else\xffwhere/.git\n")
    worktrees._drop_registration(common, path)
    assert entry.exists()


async def test_a_driver_name_that_is_not_utf8_does_not_break_probing(repo: Path, manager: WorktreeManager) -> None:
    config = repo / ".git" / "config"
    config.write_bytes(config.read_bytes() + b'[merge "odd\xff"]\n\tdriver = cat %A\n')
    assert (await manager.probe(repo)).usable


# -- review round 10 ------------------------------------------------------------

# The interpreter itself: on Windows a virtual environment's python.exe is a
# launcher that runs it as a child, which would change the tree under test.
PYTHON = getattr(sys, "_base_executable", sys.executable)
HOLDS_PIPES = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
    "time.sleep(100)\n"
)


def spawn_one_that_holds_its_pipes(monkeypatch: Any) -> list[Any]:
    """Whatever git command is asked for, start a process whose child keeps its pipes open."""
    started: list[Any] = []
    spawn = asyncio.create_subprocess_exec

    async def holding(*command: Any, **kwargs: Any) -> Any:
        process = await spawn(PYTHON, "-c", HOLDS_PIPES, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", holding)
    return started


async def test_a_timeout_is_not_held_up_by_what_git_started(
    repo: Path, manager: WorktreeManager, monkeypatch: Any
) -> None:
    spawn_one_that_holds_its_pipes(monkeypatch)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_WORK_SECONDS", 1.0)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_STOP_SECONDS", 20.0)
    began = asyncio.get_running_loop().time()
    with pytest.raises(worktrees.GitError):
        await manager._git(repo, "status")
    # What it started was killed with it: no waiting for its pipes at all.
    assert asyncio.get_running_loop().time() - began < 8


async def test_the_wait_after_killing_has_an_end(repo: Path, manager: WorktreeManager, monkeypatch: Any) -> None:
    started = spawn_one_that_holds_its_pipes(monkeypatch)
    monkeypatch.setattr(worktrees, "_kill_tree", lambda pid: None)  # as if a child got away
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_WORK_SECONDS", 1.0)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_STOP_SECONDS", 1.0)
    began = asyncio.get_running_loop().time()
    with pytest.raises(worktrees.GitError):
        await manager._git(repo, "status")
    assert asyncio.get_running_loop().time() - began < 8
    assert started[0]._transport.is_closing()  # its pipes let go of, not left open


async def test_a_cancellation_is_not_held_up_by_what_git_started(
    repo: Path, manager: WorktreeManager, monkeypatch: Any
) -> None:
    started = spawn_one_that_holds_its_pipes(monkeypatch)
    running = asyncio.ensure_future(manager._git(repo, "status"))
    while not started:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.5)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_STOP_SECONDS", 20.0)
    began = asyncio.get_running_loop().time()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert asyncio.get_running_loop().time() - began < 8


async def test_a_worktree_whose_registration_went_can_be_opened_and_removed(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    await asyncio.to_thread(shutil.rmtree, _fs(repo / ".git" / "worktrees"))  # git gave up half way
    restarted = WorktreeManager()
    found = await restarted.open(repo, tree.path)
    assert found.id == "w1" and found.start == tree.start
    await restarted.remove(found)
    assert not exists(tree.path) and git(repo, "for-each-ref", REF_PREFIX) == ""


async def test_stopping_git_is_not_cut_short_by_another_cancellation(
    repo: Path, manager: WorktreeManager, monkeypatch: Any
) -> None:
    import threading
    import time

    started = spawn_one_that_holds_its_pipes(monkeypatch)
    real_kill_tree = worktrees._kill_tree
    killing = threading.Event()

    def slow_kill_tree(pid: int) -> None:
        killing.set()
        time.sleep(0.5)
        real_kill_tree(pid)

    monkeypatch.setattr(worktrees, "_kill_tree", slow_kill_tree)
    running = asyncio.ensure_future(manager._git(repo, "status"))
    while not started:
        await asyncio.sleep(0.05)
    running.cancel()  # the first: stopping begins
    began = await asyncio.to_thread(killing.wait, 30)
    assert began
    running.cancel()  # the second, while the tree is being killed
    with pytest.raises(asyncio.CancelledError):
        await running
    assert started[0].returncode is not None  # killed and reaped all the same


@pytest.mark.skipif(not WINDOWS, reason="on POSIX the group is killed: its id is not reused while it has members")
async def test_a_reaped_process_is_not_killed_by_its_old_id(monkeypatch: Any) -> None:
    # Its id may belong to another process by now: nothing is killed by it.
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "pass", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    killed = []
    monkeypatch.setattr(worktrees, "_kill_tree", killed.append)
    await worktrees._stop(process)
    assert killed == []


async def test_killing_the_tree_takes_what_git_started(monkeypatch: Any) -> None:
    process = await asyncio.create_subprocess_exec(
        PYTHON,
        "-c",
        HOLDS_PIPES,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **({} if WINDOWS else {"start_new_session": True}),
    )
    import psutil

    parent = psutil.Process(process.pid)
    children: list[Any] = []
    for _ in range(200):
        children = parent.children(recursive=True)
        if any("sleep(30)" in " ".join(child.cmdline()) for child in children):
            break
        await asyncio.sleep(0.05)
    assert any("sleep(30)" in " ".join(child.cmdline()) for child in children)
    await asyncio.to_thread(worktrees._kill_tree, process.pid)
    await asyncio.wait_for(process.communicate(), 10)  # its pipes close: nothing holds them now
    _gone, alive = await asyncio.to_thread(psutil.wait_procs, children, 10)
    # Killed; one nobody reaps (a container without an init) stays a zombie.
    for child in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            assert child.status() == psutil.STATUS_ZOMBIE


@pytest.mark.parametrize(
    ("extra", "usable"),
    [
        (b'[merge ""]\n\tdriver = cat %A\n[merge]\n\tdefault = ""\n', False),
        (b'[merge ""]\n\tdriver = cat %A\n', True),  # configured, not chosen
        (b'[merge " demo "]\n\tdriver = cat %A\n[merge]\n\tdefault = " demo "\n', False),
        (b'[merge " demo "]\n\tdriver = cat %A\n[merge]\n\tdefault = demo\n', True),  # another name
    ],
    ids=["empty-name-chosen", "empty-name-not-chosen", "spaces-kept", "spaces-differ"],
)
async def test_probe_merge_driver_names_as_git_reads_them(
    repo: Path, manager: WorktreeManager, extra: bytes, usable: bool
) -> None:
    config = repo / ".git" / "config"
    config.write_bytes(config.read_bytes() + extra)
    found = await manager.probe(repo)
    assert found.usable is usable
    if not usable:
        assert found.reason == worktrees.MERGE_DRIVERS


@pytest.mark.skipif(WINDOWS, reason="on Windows a child does not inherit the pipes")
async def test_what_git_left_holding_its_pipes_is_killed_after_git_exited(
    repo: Path, manager: WorktreeManager, tmp_path: Path, monkeypatch: Any
) -> None:
    import psutil

    marker = tmp_path / "child.pid"
    script = (
        "import subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"open({str(marker)!r}, 'w').write(str(child.pid))\n"
    )
    spawn = asyncio.create_subprocess_exec

    async def exits_leaving_a_child(*command: Any, **kwargs: Any) -> Any:
        return await spawn(PYTHON, "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", exits_leaving_a_child)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_WORK_SECONDS", 1.0)
    monkeypatch.setattr("config.nekoclaw_settings.WORKTREE_GIT_STOP_SECONDS", 20.0)
    began = asyncio.get_running_loop().time()
    with pytest.raises(worktrees.GitError):
        await manager._git(repo, "status")
    assert asyncio.get_running_loop().time() - began < 8
    child = int(marker.read_text())
    for _ in range(100):
        if not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE:
            break
        await asyncio.sleep(0.05)
    assert not psutil.pid_exists(child) or psutil.Process(child).status() == psutil.STATUS_ZOMBIE


async def test_preparing_does_not_overwrite_an_ignored_file_of_the_worktree(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "debug.log", "the worktree's own, ignored\n")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "debug.log", "the project's, force-added\n")
    git(repo, "add", "-f", "debug.log")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    with pytest.raises(WorktreeError) as raised:
        await manager.prepare_conflicts(tree, conflict)
    assert "debug.log" in str(raised.value)
    assert read(tree.path / "debug.log") == b"the worktree's own, ignored\n"
    assert read(tree.path / "a.txt") == b"worktree\n"
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == tree.start


async def test_a_cancellation_while_putting_back_carries_what_went_wrong(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    original = manager._git
    putting_back = asyncio.Event()

    async def failing_both_ways(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:1] == ("checkout",) and args[-1] == conflict.result:
            putting_back.set()
            await asyncio.sleep(0.5)  # cancelled meanwhile, and goes on
            return worktrees._Run(1, b"", b"a file is held")
        if args[:1] == ("checkout",):
            raise worktrees.GitError(args, 1, b"unable to write a.txt")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", failing_both_ways)
    preparing = asyncio.ensure_future(manager.prepare_conflicts(tree, conflict))
    await asyncio.wait_for(putting_back.wait(), 30)
    preparing.cancel()
    with pytest.raises(asyncio.CancelledError) as raised:
        await preparing
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "could not be put back" in notes and "a file is held" in notes
    assert isinstance(raised.value.__cause__, worktrees.GitError)


async def test_a_landed_file_ignored_since_is_not_taken_for_deleted(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "note.txt", "first\n")
    assert (await manager.merge_back(tree)).status == "landed"
    write(tree.path / ".gitignore", "ignored/\n*.log\nnote.txt\n")
    write(tree.path / "note.txt", "second\n")
    landed = await manager.merge_back(tree)
    assert landed.status == "landed", landed.detail
    assert read(repo / "note.txt") == b"second\n"
    assert read(repo / ".gitignore") == b"ignored/\n*.log\nnote.txt\n"
    assert "note.txt" not in [change.path for change in landed.files if change.status == "deleted"]


async def test_cleaning_up_after_a_failed_make_is_not_cut_short(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading
    import time

    original = manager._git
    real = worktrees._remove_tree
    removing = threading.Event()

    def slow_to_clean(path: Path) -> None:
        removing.set()
        time.sleep(0.5)  # cancelled again meanwhile
        real(path)

    async def cancelled(cwd: Path, *args: str, **kwargs: Any) -> Any:
        done = await original(cwd, *args, **kwargs)
        if args[:2] == ("worktree", "add"):
            raise asyncio.CancelledError
        return done

    monkeypatch.setattr(manager, "_git", cancelled)
    monkeypatch.setattr(worktrees, "_remove_tree", slow_to_clean)
    making = asyncio.ensure_future(manager.create(repo, base / "w1"))
    assert await asyncio.to_thread(removing.wait, 30)
    making.cancel()
    with pytest.raises(asyncio.CancelledError):
        await making
    monkeypatch.setattr(manager, "_git", original)
    assert not exists(base / "w1")
    assert "w1" not in registered(repo)
    assert git(repo, "for-each-ref", REF_PREFIX) == ""


async def test_a_landed_file_ignored_since_does_not_stop_preparing(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "note.txt", "the worktree's\n")
    assert (await manager.merge_back(tree)).status == "landed"
    write(tree.path / ".gitignore", "ignored/\n*.log\nnote.txt\n")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    prepared = await manager.prepare_conflicts(tree, conflict)
    assert "<<<<<<<" in read(prepared.path / "a.txt").decode()
    assert read(prepared.path / "note.txt") == b"the worktree's\n"


async def test_a_cleanup_that_falls_short_says_so(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    original = manager._git

    async def add_fails(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("worktree", "add"):
            raise worktrees.GitError(args, 128, b"simulated")
        return await original(cwd, *args, **kwargs)

    async def forget_fails(*args: Any, **kwargs: Any) -> None:
        raise worktrees.GitError(["worktree", "list"], None, b"no answer")

    monkeypatch.setattr(manager, "_git", add_fails)
    monkeypatch.setattr(manager, "_forget", forget_fails)
    with pytest.raises(worktrees.GitError) as raised:
        await manager.create(repo, base / "w1")
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "cleaning up fell short" in notes and f"{REF_PREFIX}w1/start" in notes


async def test_has_changes_after_a_removal_queued_first(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "work.txt", "x\n")
    removing = asyncio.ensure_future(manager.remove(tree))
    asking = asyncio.ensure_future(manager.has_changes(tree))
    await removing
    assert await asking is False


# -- review round 21 -------------------------------------------------------------


@pytest.mark.parametrize("direction", ["directory-to-file", "file-to-directory"])
async def test_a_swap_in_the_project_does_not_stop_preparing(
    repo: Path, manager: WorktreeManager, base: Path, direction: str
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    if direction == "directory-to-file":
        shutil.rmtree(_fs(repo / "src"))
        write(repo / "src", "now a file\n")
    else:
        os.remove(repo / "b.txt")
        write(repo / "b.txt" / "inside.txt", "now a directory\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    prepared = await manager.prepare_conflicts(tree, conflict)
    assert "<<<<<<<" in read(prepared.path / "a.txt").decode()
    if direction == "directory-to-file":
        assert read(prepared.path / "src") == b"now a file\n"
    else:
        assert read(prepared.path / "b.txt" / "inside.txt") == b"now a directory\n"


async def test_preparing_writes_nothing_into_a_repository_of_its_own(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    make_nested(tree.path / "sub")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "sub" / "new.txt", "the project's\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    before = digest(tree.path / "sub")
    with pytest.raises(WorktreeError) as raised:
        await manager.prepare_conflicts(tree, conflict)
    assert "sub/" in str(raised.value)
    assert digest(tree.path / "sub") == before and not exists(tree.path / "sub" / "new.txt")


async def test_probe_filter_named_like_no_filter(repo: Path, manager: WorktreeManager) -> None:
    write(repo / ".gitattributes", "*.txt filter=unspecified\n")
    git(repo, "config", "filter.unspecified.clean", "cat")
    assert (await manager.probe(repo)).reason == CONTENT_FILTERS


async def test_a_submodule_link_staged_in_a_worktree_is_refused_and_kept(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    make_nested(tree.path / "embedded")
    git(tree.path, "add", "embedded")  # staged as a submodule link
    before = digest(repo)
    with pytest.raises(WorktreeUnavailable) as raised:
        await manager.merge_back(tree)
    assert raised.value.reason == SUBMODULES and "embedded" in raised.value.detail
    assert digest(repo) == before
    report = await manager.reconcile(repo, base, [])
    assert [path.name for path, _why in report.failed] == ["w1"] and exists(tree.path / "embedded" / "inside.txt")


# -- review round 22 -------------------------------------------------------------


@pytest.mark.parametrize("how", ["cherry-pick", "unmerged"])
async def test_no_landing_while_the_worktree_is_mid_operation(
    repo: Path, manager: WorktreeManager, base: Path, how: str
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n<<<<<<< HEAD\n")
    if how == "cherry-pick":
        git_dir = Path(git(tree.path, "rev-parse", "--absolute-git-dir").strip())
        (git_dir / "CHERRY_PICK_HEAD").write_text(tree.start, encoding="utf-8")
    else:
        ours = git(tree.path, "hash-object", "-w", "--stdin", input=b"ours\n").strip()
        theirs = git(tree.path, "hash-object", "-w", "--stdin", input=b"theirs\n").strip()
        entries = f"0 {'0' * 40}\tx.txt\n100644 {ours} 2\tx.txt\n100644 {theirs} 3\tx.txt\n"
        git(tree.path, "update-index", "--index-info", input=entries.encode())
    before = digest(repo)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and "in the worktree" in result.detail
    assert digest(repo) == before


async def test_a_failed_preparing_puts_the_worktrees_own_head_and_index_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "staged\n")
    git(tree.path, "add", "a.txt")
    write(tree.path / "a.txt", "working\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    head = git(tree.path, "rev-parse", "HEAD").strip()
    original = manager._git

    async def ref_locked(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start"):
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", ref_locked)
    with pytest.raises(worktrees.GitError):
        await manager.prepare_conflicts(tree, conflict)
    monkeypatch.setattr(manager, "_git", original)
    assert git(tree.path, "rev-parse", "HEAD").strip() == head
    assert git(tree.path, "show", ":a.txt") == "staged\n"
    assert read(tree.path / "a.txt") == b"working\n"


def test_output_lines_end_at_line_feeds_only() -> None:
    run = worktrees._Run(0, "C:/a\u2028b\nnext\r\n\n".encode(), b"")
    assert run.lines() == ["C:/a\u2028b", "next"]


# -- review round 23 -------------------------------------------------------------


async def test_preparing_removes_what_the_merge_deletes(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "note.txt", "landed from the worktree\n")
    assert (await manager.merge_back(tree)).status == "landed"
    os.remove(repo / "note.txt")  # the user deleted it since
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    prepared = await manager.prepare_conflicts(tree, conflict)
    assert not exists(prepared.path / "note.txt")
    write(prepared.path / "a.txt", "both\n")
    landed = await manager.merge_back(prepared)
    assert landed.status == "landed", landed.detail
    assert not exists(repo / "note.txt") and read(repo / "a.txt") == b"both\n"


async def test_a_passing_failure_to_ask_git_is_not_remembered(
    repo: Path, manager: WorktreeManager, monkeypatch: Any
) -> None:
    original = manager._run
    calls = []

    async def busy_once(cwd: Any, args: list[str], **kwargs: Any) -> Any:
        if args == ["--version"]:
            calls.append(1)
            if len(calls) == 1:
                raise worktrees.GitError(args, None, b"no answer within 30 seconds")
        return await original(cwd, args, **kwargs)

    monkeypatch.setattr(manager, "_run", busy_once)
    assert (await manager.probe(repo)).reason == GIT_MISSING
    assert (await manager.probe(repo)).usable
    await manager.probe(repo)
    assert len(calls) == 2


async def test_git_not_there_is_remembered(repo: Path, tmp_path: Path, monkeypatch: Any) -> None:
    manager = WorktreeManager(git=str(tmp_path / "no-such-git"))
    spawn = asyncio.create_subprocess_exec
    spawned = []

    async def counting(*command: Any, **kwargs: Any) -> Any:
        spawned.append(command)
        return await spawn(*command, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", counting)
    assert (await manager.probe(repo)).reason == GIT_MISSING
    assert (await manager.probe(repo)).reason == GIT_MISSING
    assert len(spawned) == 1


async def test_a_worktree_directory_gone_gives_a_git_error(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    await asyncio.to_thread(shutil.rmtree, _fs(tree.path))
    with pytest.raises(worktrees.GitError) as raised:
        await manager.merge_back(tree)
    assert "could not be started" in str(raised.value)


async def test_an_index_that_cannot_be_put_back_is_kept_and_said(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "staged\n")
    git(tree.path, "add", "a.txt")
    write(tree.path / "a.txt", "working\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    original = manager._git

    async def ref_locked(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start"):
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        return await original(cwd, *args, **kwargs)

    def held(copy: Path, index: Path) -> None:
        raise PermissionError("the index is held")

    monkeypatch.setattr(manager, "_git", ref_locked)
    monkeypatch.setattr(worktrees, "_put_index_back", held)
    with pytest.raises(worktrees.GitError) as raised:
        await manager.prepare_conflicts(tree, conflict)
    notes = getattr(raised.value, "__notes__", [])
    said = [note for note in notes if "index could not be put back" in note]
    assert said
    kept = Path(said[0].split("what it was is kept in ", 1)[1])
    assert kept.exists()
    shutil.rmtree(kept.parent)


def test_removing_does_not_go_through_a_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    write(outside / "app.py", "not the worktree's\n")
    root = tmp_path / "wt"
    root.mkdir()
    try:
        os.symlink(outside, root / "src", target_is_directory=True)
    except OSError:
        pytest.skip("links to directories cannot be made here")
    write(root / "kept" / "gone.txt", "removed\n")
    write(root / "plain", "a file now\n")
    worktrees._remove_files(root, ["src/app.py", "kept/gone.txt", "plain/old.txt"])
    assert read(outside / "app.py") == b"not the worktree's\n"
    assert not exists(root / "kept" / "gone.txt")


async def test_removing_finishes_before_putting_back(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading
    import time

    tree = await manager.create(repo, base / "w1")
    write(tree.path / "note.txt", "landed from the worktree\n")
    assert (await manager.merge_back(tree)).status == "landed"
    os.remove(repo / "note.txt")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    real = worktrees._remove_files
    removing = threading.Event()
    finished = []

    def slow(root: Path, names: list[str]) -> None:
        removing.set()
        time.sleep(0.5)
        real(root, names)
        finished.append(time.monotonic())

    monkeypatch.setattr(worktrees, "_remove_files", slow)
    preparing = asyncio.ensure_future(manager.prepare_conflicts(tree, conflict))
    began = await asyncio.to_thread(removing.wait, 30)
    assert began
    preparing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await preparing
    assert finished  # done before putting back began
    assert read(tree.path / "note.txt") == b"landed from the worktree\n"  # put back, not removed after
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == conflict.base


async def test_a_head_that_cannot_be_put_back_does_not_stop_the_rest(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "staged\n")
    git(tree.path, "add", "a.txt")
    write(tree.path / "a.txt", "working\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    original = manager._git

    async def locked(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and args[2] == conflict.current:
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        if args[:3] == ("update-ref", "--no-deref", "HEAD"):
            if kwargs.get("check", True):
                raise worktrees.GitError(args, 128, b"HEAD.lock exists")
            return worktrees._Run(128, b"", b"HEAD.lock exists")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", locked)
    with pytest.raises(worktrees.GitError) as raised:
        await manager.prepare_conflicts(tree, conflict)
    monkeypatch.setattr(manager, "_git", original)
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "HEAD could not be put back" in notes
    assert git(tree.path, "show", ":a.txt") == "staged\n"  # the index went back all the same
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == conflict.base  # and the start


async def test_no_landing_while_the_project_has_unmerged_entries(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "b.txt", "worktree\n")
    ours = git(repo, "hash-object", "-w", "--stdin", input=b"ours\n").strip()
    theirs = git(repo, "hash-object", "-w", "--stdin", input=b"theirs\n").strip()
    entries = f"0 {'0' * 40}\ta.txt\n100644 {ours} 2\ta.txt\n100644 {theirs} 3\ta.txt\n"
    git(repo, "update-index", "--index-info", input=entries.encode())  # as a stash pop leaves it
    write(repo / "a.txt", "<<<<<<< Updated upstream\nours\n=======\ntheirs\n>>>>>>> Stashed changes\n")
    before = digest(repo)
    result = await manager.merge_back(tree)
    assert result.status == "failed" and "in the project" in result.detail
    assert digest(repo) == before


async def test_a_failed_prepare_puts_the_worktree_back_on_its_branch(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    git(tree.path, "switch", "-q", "-c", "side")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    tip = git(tree.path, "rev-parse", "side").strip()
    original = manager._git

    async def ref_locked(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and args[2] == conflict.current:
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", ref_locked)
    with pytest.raises(worktrees.GitError):
        await manager.prepare_conflicts(tree, conflict)
    monkeypatch.setattr(manager, "_git", original)
    assert git(tree.path, "symbolic-ref", "HEAD").strip() == "refs/heads/side"
    assert git(tree.path, "rev-parse", "side").strip() == tip
    assert read(tree.path / "a.txt") == b"worktree\n"


async def test_a_put_back_checkout_that_never_answers_does_not_stop_the_rest(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "staged\n")
    git(tree.path, "add", "a.txt")
    write(tree.path / "a.txt", "working\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    head = git(tree.path, "rev-parse", "HEAD").strip()
    original = manager._git

    async def stuck(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and args[2] == conflict.current:
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        if args[:1] == ("checkout",) and args[-1] == conflict.result:
            raise worktrees.GitError(args, None, b"no answer within 600 seconds")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", stuck)
    with pytest.raises(worktrees.GitError) as raised:
        await manager.prepare_conflicts(tree, conflict)
    monkeypatch.setattr(manager, "_git", original)
    notes = " ".join(getattr(raised.value, "__notes__", []))
    assert "the worktree could not be put back: git checkout --force timed out" in notes
    assert git(tree.path, "show", ":a.txt") == "staged\n"
    assert git(tree.path, "rev-parse", "HEAD").strip() == head
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == conflict.base


async def test_no_preparing_a_worktree_mid_merge(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    git_dir = Path(git(tree.path, "rev-parse", "--absolute-git-dir").strip())
    head = git(tree.path, "rev-parse", "HEAD").strip()
    (git_dir / "MERGE_HEAD").write_text(head + "\n", encoding="utf-8")  # a merge begun there, not committed
    with pytest.raises(WorktreeError, match="in progress in the worktree"):
        await manager.prepare_conflicts(tree, conflict)
    assert (git_dir / "MERGE_HEAD").exists()
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == conflict.base


async def test_a_failed_prepare_leaves_no_index_where_there_was_none(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    index = Path(git(tree.path, "rev-parse", "--path-format=absolute", "--git-path", "index").strip())
    os.remove(index)
    original = manager._git

    async def ref_locked(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and args[2] == conflict.current:
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", ref_locked)
    with pytest.raises(worktrees.GitError):
        await manager.prepare_conflicts(tree, conflict)
    assert not index.exists()
    assert read(tree.path / "a.txt") == b"worktree\n"


async def test_removing_leaves_a_directory_put_where_the_worktree_was(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    for how in ("moved", "deleted"):
        tree = await manager.create(repo, base / f"w-{how}")
        noted = worktrees._directory_identity(tree.path) is not None
        if how == "moved":
            os.rename(tree.path, base / "moved")
        else:
            shutil.rmtree(tree.path)  # its number may well be given to the next
        write(tree.path / "keep.txt", "someone else's\n")  # git still has it registered
        if noted:
            await manager.remove(tree)
            assert tree.path.name not in registered(repo)
            assert not git(repo, "for-each-ref", f"{REF_PREFIX}{tree.id}/").strip()
        else:  # nothing tells it from the worktree: everything stays
            with pytest.raises(WorktreeError, match="no longer known to be this worktree"):
                await manager.remove(tree)
            assert tree.path.name in registered(repo)
        assert read(tree.path / "keep.txt") == b"someone else's\n"


async def test_removing_finishes_before_the_lock_is_let_go(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading
    import time

    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    real = worktrees._remove_tree
    removing = threading.Event()
    finished = []

    def slow(path: Path) -> None:
        if _same(path, tree.path):
            removing.set()
            time.sleep(0.5)
        real(path)
        if _same(path, tree.path):
            finished.append(True)

    monkeypatch.setattr(worktrees, "_remove_tree", slow)
    removal = asyncio.ensure_future(manager.remove(tree))
    assert await asyncio.to_thread(removing.wait, 30)
    removal.cancel()
    with pytest.raises(asyncio.CancelledError):
        await removal
    assert finished  # done before the lock went
    assert not await manager.has_changes(tree)


def _same(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


async def test_a_failed_prepare_puts_files_back_byte_for_byte(
    tmp_path: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    repo = make_repo(tmp_path / "eol", {".gitattributes": "*.txt text eol=crlf\n", "a.txt": "base\r\n", "b.txt": "b\r\n"})
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", b"worktree\nmixed\r\n")  # bytes the eol rule would not write
    write(tree.path / "b.txt", b"changed\n")
    write(repo / "a.txt", b"project\r\n")
    conflict = await manager.merge_back(tree)
    assert conflict.status == "conflict"
    original = manager._git

    async def ref_locked(cwd: Path, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("update-ref", f"{REF_PREFIX}w1/start") and args[2] == conflict.current:
            raise worktrees.GitError(args, 128, b"cannot lock ref")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", ref_locked)
    with pytest.raises(worktrees.GitError):
        await manager.prepare_conflicts(tree, conflict)
    assert read(tree.path / "a.txt") == b"worktree\nmixed\r\n"
    assert read(tree.path / "b.txt") == b"changed\n"


async def test_removing_a_worktree_whose_git_file_went(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "work.txt", "made there\n")
    os.remove(tree.path / ".git")
    if worktrees._directory_identity(tree.path) is None:
        with pytest.raises(WorktreeError, match="no longer known to be this worktree"):
            await manager.remove(tree)
        assert exists(tree.path / "work.txt")
        return
    await manager.remove(tree)
    assert not exists(tree.path)
    assert "w1" not in registered(repo)


async def test_a_prepare_cancelled_while_copying_leaves_no_copies(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading
    import time

    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "worktree\n")
    write(repo / "a.txt", "project\n")
    conflict = await manager.merge_back(tree)
    real = worktrees._back_up
    copying = threading.Event()
    where: list[Path] = []
    finished = []

    def slow(root: Path, names: list[str], backup: Path) -> Any:
        where.append(backup)
        copying.set()
        time.sleep(0.5)
        saved = real(root, names, backup)
        finished.append(True)
        return saved

    monkeypatch.setattr(worktrees, "_back_up", slow)
    preparing = asyncio.ensure_future(manager.prepare_conflicts(tree, conflict))
    assert await asyncio.to_thread(copying.wait, 30)
    preparing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await preparing
    assert finished  # the copy ran to its end before the copies went
    assert not exists(where[0].parent)
    assert read(tree.path / "a.txt") == b"worktree\n"
    assert git(repo, "rev-parse", f"{REF_PREFIX}w1/start").strip() == conflict.base


def test_when_a_directory_was_made() -> None:
    class Linux:  # what os.stat gives there: a change time, no birth time
        st_ctime_ns = 5

    class Windows:
        st_ctime_ns = 7  # when it was made, there

    class Newer:
        st_birthtime_ns = 9
        st_ctime_ns = 7

    class Mac:
        st_birthtime = 1.5
        st_ctime_ns = 7

    assert worktrees._birth_of(Linux(), "linux") is None  # type: ignore[arg-type]
    assert worktrees._birth_of(Windows(), "win32") == 7  # type: ignore[arg-type]
    assert worktrees._birth_of(Newer(), "win32") == 9  # type: ignore[arg-type]
    assert worktrees._birth_of(Mac(), "darwin") == 1_500_000_000  # type: ignore[arg-type]


def test_a_directory_without_a_birth_time_is_not_noted(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(worktrees, "_birth_of", lambda found: None)
    assert worktrees._directory_identity(tmp_path) is None


def test_a_noted_directory_is_told_by_its_birth_too(tmp_path: Path, monkeypatch: Any) -> None:
    common = tmp_path / "common"
    entry = common / "worktrees" / "w1"
    entry.mkdir(parents=True)
    path = tmp_path / "w1"
    path.mkdir()
    (entry / "gitdir").write_text(str(path / ".git"), encoding="utf-8")
    (entry / worktrees._DIRECTORY_NOTE).write_text('{"dev": 1, "ino": 2, "birth": 3}', encoding="utf-8")
    monkeypatch.setattr(worktrees, "_directory_identity", lambda where: {"dev": 1, "ino": 2, "birth": 3})
    assert worktrees._whose(path, common) == "ours"
    monkeypatch.setattr(worktrees, "_directory_identity", lambda where: {"dev": 1, "ino": 2, "birth": 4})
    assert worktrees._whose(path, common) == "theirs"  # the same number, made later
    monkeypatch.setattr(worktrees, "_directory_identity", lambda where: None)
    assert worktrees._whose(path, common) == "unknown"
    (entry / worktrees._DIRECTORY_NOTE).write_text('{"dev": 1, "ino": 2}', encoding="utf-8")
    monkeypatch.setattr(worktrees, "_directory_identity", lambda where: {"dev": 1, "ino": 2, "birth": 3})
    assert worktrees._whose(path, common) == "unknown"  # a note without a birth time tells nothing


async def test_removing_leaves_what_nothing_tells_from_the_worktree(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(worktrees, "_birth_of", lambda found: None)  # as on Linux: nothing is noted
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "work.txt", "made there\n")
    os.remove(tree.path / ".git")
    with pytest.raises(WorktreeError, match="no longer known to be this worktree"):
        await manager.remove(tree)
    assert read(tree.path / "work.txt") == b"made there\n"
    assert "w1" in registered(repo)
    assert git(repo, "for-each-ref", f"{REF_PREFIX}w1/").strip()
    shutil.rmtree(tree.path)  # by hand, as it says
    await manager.remove(tree)
    assert "w1" not in registered(repo)
    assert not git(repo, "for-each-ref", f"{REF_PREFIX}w1/").strip()


async def test_a_removal_stopped_half_way_keeps_what_finds_it_again(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "locked.txt", "held by an editor\n")
    real = worktrees._remove_tree

    def held(path: Path) -> None:  # .git first (it sorts first), then all but the held file
        for entry in sorted(os.listdir(path)):
            if entry != "locked.txt":
                target = path / entry
                real(target) if target.is_dir() else os.remove(target)

    original = manager._git

    async def as_git_does(cwd: Path, *args: str, **kwargs: Any) -> Any:
        # Not called by the module: here so that calling `git worktree
        # remove` again would fail this test. With a file held it removes
        # the rest, drops the registration all the same, and exits 255.
        if args[:2] == ("worktree", "remove"):
            await asyncio.to_thread(held, tree.path)
            await asyncio.to_thread(shutil.rmtree, _fs(repo / ".git" / "worktrees" / "w1"))
            return worktrees._Run(255, b"", b"error: failed to delete")
        return await original(cwd, *args, **kwargs)

    monkeypatch.setattr(manager, "_git", as_git_does)
    monkeypatch.setattr(worktrees, "_remove_tree", held)
    with pytest.raises(WorktreeError, match="its registration and refs are kept"):
        await manager.remove(tree)
    monkeypatch.setattr(manager, "_git", original)
    assert "w1" in registered(repo)  # as it says
    assert git(repo, "for-each-ref", f"{REF_PREFIX}w1/").strip()
    monkeypatch.setattr(worktrees, "_remove_tree", real)
    if worktrees._directory_identity(tree.path) is None:  # nothing tells it: by hand, as it would say
        shutil.rmtree(tree.path)
    await manager.remove(tree)
    assert not exists(tree.path)
    assert "w1" not in registered(repo)
    assert not git(repo, "for-each-ref", f"{REF_PREFIX}w1/").strip()


async def test_a_staged_version_undone_in_the_file_is_a_change(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    original = read(tree.path / "a.txt")
    write(tree.path / "a.txt", "staged only\n")
    git(tree.path, "add", "a.txt")
    write(tree.path / "a.txt", original)  # the file back as it was; the staged version only in the index
    assert await manager.has_changes(tree)
    report = await manager.reconcile(repo, base, [])
    assert [path.name for path in report.kept] == ["w1"] and exists(tree.path)


async def test_an_index_left_as_it_was_checked_out_is_no_change(
    repo: Path, manager: WorktreeManager, base: Path
) -> None:
    tree = await manager.create(repo, base / "w1")
    write(tree.path / "a.txt", "landed\n")
    assert (await manager.merge_back(tree)).status == "landed"
    landed = await manager.open(repo, tree.path)
    # Its index still holds a.txt as checked out, not as landed: no change.
    assert not await manager.has_changes(landed)
    git(tree.path, "add", "a.txt")
    assert not await manager.has_changes(landed)  # staged as it is in the file: no change either


async def test_open_waits_for_a_worktree_being_made(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    import threading
    import time

    noting = threading.Event()

    def fails_late(admin: Path, path: Path) -> None:
        noting.set()
        time.sleep(0.5)
        raise OSError("the disk went away")

    monkeypatch.setattr(worktrees, "_note_directory", fails_late)
    making = asyncio.ensure_future(manager.create(repo, base / "w1"))
    assert await asyncio.to_thread(noting.wait, 30)
    with pytest.raises(WorktreeError):  # seen once cleaned up, not half made
        await manager.open(repo, base / "w1")
    with pytest.raises(OSError):
        await making


async def test_a_registration_not_removed_entirely_keeps_the_refs(
    repo: Path, manager: WorktreeManager, base: Path, monkeypatch: Any
) -> None:
    tree = await manager.create(repo, base / "w1")
    real = worktrees._remove_tree
    admin = repo / ".git" / "worktrees"

    def held(path: Path) -> None:
        if _same(path.parent, admin):
            return  # a file in it held
        real(path)

    monkeypatch.setattr(worktrees, "_remove_tree", held)
    with pytest.raises(WorktreeError, match="the refs are kept"):
        await manager.remove(tree)
    assert git(repo, "for-each-ref", f"{REF_PREFIX}w1/").strip()
    monkeypatch.setattr(worktrees, "_remove_tree", real)
    await manager.remove(tree)
    assert "w1" not in registered(repo)
    assert not git(repo, "for-each-ref", f"{REF_PREFIX}w1/").strip()


async def test_an_index_with_unmerged_entries_is_a_change(repo: Path, manager: WorktreeManager, base: Path) -> None:
    tree = await manager.create(repo, base / "w1")
    blob = git(tree.path, "rev-parse", "HEAD:a.txt").strip()
    zero = "0" * 40
    entries = f"0 {zero}\ta.txt\n100644 {blob} 1\ta.txt\n100644 {blob} 2\ta.txt\n100644 {blob} 3\ta.txt\n"
    git(tree.path, "update-index", "--index-info", input=entries.encode())  # its file as it was
    assert await manager.has_changes(tree)
