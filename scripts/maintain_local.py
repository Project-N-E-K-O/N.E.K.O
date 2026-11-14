#!/usr/bin/env python3
"""Maintain the local integration branch without discarding working changes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile


OFFICIAL_URL = "https://github.com/Project-N-E-K-O/N.E.K.O.git"
BUILD_OUTPUTS = ("static/react/neko-chat", "frontend/plugin-manager/dist")


def git(repo, *args):
    return os.fsdecode(subprocess.check_output(["git", *args], cwd=repo)).rstrip("\n")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def state_dir(repo):
    path = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "local-maintenance"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def write_json(path, value):
    # Escape surrogate filenames so JSON remains UTF-8 and round-trips POSIX bytes.
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )


def clean_required(repo):
    if git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError(
            "Commit or deliberately preserve working changes first; automatic stashing is disabled."
        )
    for name in (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-merge",
        "rebase-apply",
    ):
        path = Path(git(repo, "rev-parse", "--git-path", name))
        if not path.is_absolute():
            path = repo / path
        if path.exists():
            raise ValueError(f"Finish or abort the existing Git operation: {name}")


def official_required(repo):
    # Validate the configured identity before Git applies transport rewrites.
    url = git(repo, "config", "--get", "remote.origin.url")
    if url.rstrip("/").removesuffix(".git") != OFFICIAL_URL.removesuffix(".git"):
        raise ValueError(f"origin is not the configured official repository: {url}")



def untracked_files(repo):
    """Reject nested repositories that Git reports as a single directory entry."""
    raw = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo
    )
    names = [os.fsdecode(name) for name in raw.split(b"\0") if name]
    for name in names:
        path = repo / name
        if path.is_dir() and not path.is_symlink():
            raise ValueError(
                f"Untracked directory or nested Git repository cannot be backed up: {name}. "
                "Ignore it or back it up separately."
            )
    return names


def backup(repo, destination=None):
    if git(repo, "ls-files", "--unmerged", "-z"):
        raise ValueError("Resolve or abort merge conflicts before creating a backup.")
    names = untracked_files(repo)
    root = Path(destination).resolve() if destination else state_dir(repo) / "backups"
    if destination and root.is_relative_to(repo.resolve()):
        raise ValueError("Keep external backups outside the working tree.")
    folder = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder.mkdir(parents=True, mode=0o700)
    head = git(repo, "rev-parse", "HEAD")
    subprocess.run(
        ["git", "bundle", "create", str(folder / "history.bundle"), "HEAD"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "bundle", "verify", str(folder / "history.bundle")],
        cwd=repo,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    for name, args in (
        ("staged.patch", ["diff", "--cached", "--binary"]),
        ("working.patch", ["diff", "--binary"]),
    ):
        (folder / name).write_bytes(subprocess.check_output(["git", *args], cwd=repo))
    with tarfile.open(folder / "untracked.tar.gz", "w:gz") as archive:
        for name in names:
            archive.add(repo / name, arcname=name, recursive=False)
    write_json(
        folder / "receipt.json",
        {
            "head": head,
            "branch": git(repo, "branch", "--show-current"),
            "status": git(repo, "status", "--short"),
            "untracked": names,
        },
    )
    return folder


def merge_upstream(repo, destination=None):
    clean_required(repo)
    official_required(repo)
    if not git(repo, "branch", "--show-current").startswith("local/"):
        raise ValueError("Merge official updates only on a local/* integration branch.")
    target = git(repo, "rev-parse", "refs/remotes/origin/main^{commit}")
    saved = backup(repo, destination)
    print(f"Backup: {saved}\nPinned upstream: {target}", flush=True)
    # Leave the result reviewable, including a clean automatic merge. Never use
    # -X ours/theirs: either choice can silently drop a local or upstream fix.
    result = subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", target], cwd=repo
    )
    if result.returncode:
        raise ValueError(
            "Merge needs resolution; the index and conflict files are preserved. Use git merge --abort to return."
        )
    print(
        "Review the merge, run verify, then git commit. Nothing has been deployed or pushed."
    )


def output_hashes(repo):
    hashes = {}
    for relative in BUILD_OUTPUTS:
        directory = repo / relative
        files = sorted(p for p in directory.rglob("*") if p.is_file())
        if not files:
            raise ValueError(f"Missing build output: {relative}")
        hashes.update({p.relative_to(repo).as_posix(): digest(p) for p in files})
    return hashes


def build(repo):
    clean_required(repo)
    head = git(repo, "rev-parse", "HEAD")
    for relative, command in (
        ("frontend/plugin-manager", "build-only"),
        ("frontend/react-neko-chat", "build"),
    ):
        cwd = repo / relative
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=cwd, check=True)
        subprocess.run(["npm", "run", command], cwd=cwd, check=True)
    clean_required(repo)
    if git(repo, "rev-parse", "HEAD") != head:
        raise ValueError("Source commit changed while building.")
    write_json(
        state_dir(repo) / "build.json", {"commit": head, "files": output_hashes(repo)}
    )


def verification_suites(repo, policy_path):
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    suites = policy.get("suites") if isinstance(policy, dict) else None
    if not isinstance(suites, list) or not suites:
        raise ValueError("Regression manifest must contain a non-empty suites list.")
    names = set()
    for suite in suites:
        if not isinstance(suite, dict):
            raise ValueError("Each regression suite must be an object.")
        name = suite.get("name")
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*", name
        ):
            raise ValueError(
                "Suite name must be a non-empty filename using letters, digits, _, . or -."
            )
        if name in names:
            raise ValueError(f"Duplicate suite name: {name}")
        names.add(name)
        command = suite.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or not command[0].strip()
        ):
            raise ValueError(
                f"Suite {name} command must be a non-empty list of string arguments."
            )
        cwd = suite.get("cwd", ".")
        if (
            not isinstance(cwd, str)
            or not cwd
            or "\0" in cwd
            or not (repo / cwd).is_dir()
        ):
            raise ValueError(f"Suite {name} cwd must name an existing directory.")
    return suites


def source_snapshot(repo):
    untracked = {}
    for name in untracked_files(repo):
        path = repo / name
        if path.is_symlink():
            untracked[name] = {"symlink": os.readlink(path)}
        else:
            untracked[name] = {
                "sha256": digest(path),
                "mode": path.stat().st_mode,
            }
    return {
        "commit": git(repo, "rev-parse", "HEAD"),
        "index_sha256": hashlib.sha256(
            subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=repo)
        ).hexdigest(),
        "diff_sha256": hashlib.sha256(
            subprocess.check_output(
                ["git", "diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv"],
                cwd=repo,
            )
        ).hexdigest(),
        "untracked": untracked,
    }


def verify(repo, policy_path=None):
    # A failed attempt must invalidate any previous successful verification.
    verified = state_dir(repo) / "verified.json"
    verified.unlink(missing_ok=True)
    if git(repo, "ls-files", "--unmerged"):
        raise ValueError("Resolve merge conflicts before verification.")
    merge_head = Path(git(repo, "rev-parse", "--git-path", "MERGE_HEAD"))
    if (repo / merge_head).exists() and git(repo, "diff", "--name-only"):
        raise ValueError("Stage all tracked changes before verifying a pending merge.")
    if policy_path is None:
        policy_path = repo / "local-desktop-ui/regression.json"
        if not policy_path.is_file():
            policy_path = repo / "local-desktop-ui/regression.example.json"
    suites = verification_suites(repo, policy_path)
    report = state_dir(repo) / (
        "verify-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    )
    report.mkdir()
    before = source_snapshot(repo)
    results = []
    for suite in suites:
        cwd = repo / suite.get("cwd", ".")
        log = report / (suite["name"] + ".log")
        print(f"Running {suite['name']} → {log}", flush=True)
        with log.open("w") as stream:
            result = subprocess.run(
                suite["command"], cwd=cwd, stdout=stream, stderr=subprocess.STDOUT
            )
        results.append(
            {"suite": suite["name"], "returncode": result.returncode, "log": str(log)}
        )
    after = source_snapshot(repo)
    unchanged = before == after
    receipt = {
        "commit": before["commit"],
        "source_unchanged": unchanged,
        "diff_sha256": after["diff_sha256"],
        "source_snapshot": after,
        "results": results,
    }
    write_json(report / "receipt.json", receipt)
    if not unchanged or any(item["returncode"] for item in results):
        raise ValueError(f"Verification failed or source changed. Inspect {report}")
    write_json(verified, receipt)
    print(f"All suites passed: {report}")


def status(repo):
    result = {
        "branch": git(repo, "branch", "--show-current"),
        "commit": git(repo, "rev-parse", "HEAD"),
        "origin": git(repo, "remote", "get-url", "origin"),
        "working_changes": git(repo, "status", "--short"),
    }
    try:
        counts = git(
            repo, "rev-list", "--left-right", "--count", "HEAD...origin/main"
        ).split()
        result.update(
            local_only_commits=int(counts[0]),
            upstream_only_commits=int(counts[1]),
            upstream=git(repo, "rev-parse", "origin/main"),
        )
    except subprocess.CalledProcessError:
        result["upstream"] = "Fetch origin/main first."
    print(json.dumps(result, ensure_ascii=True, indent=2))


def upstream_review(repo):
    """Compare committed paths without merging, stashing or changing the index.

    Overlap is a review queue, not a prediction of textual merge conflicts.
    Renames are expanded to old/new paths so neither side escapes the review.
    """
    official_required(repo)
    head = git(repo, "rev-parse", "HEAD^{commit}")
    upstream = git(repo, "rev-parse", "refs/remotes/origin/main^{commit}")
    base = git(repo, "merge-base", head, upstream)

    def changed_paths(target):
        raw = subprocess.check_output(
            ["git", "diff", "--name-only", "--no-renames", "-z", base, target, "--"],
            cwd=repo,
        )
        return {os.fsdecode(name) for name in raw.split(b"\0") if name}

    local_paths = changed_paths(head)
    upstream_paths = changed_paths(upstream)
    counts = git(
        repo, "rev-list", "--left-right", "--count", f"{head}...{upstream}"
    ).split()
    return {
        "head": head,
        "upstream": upstream,
        "merge_base": base,
        "working_changes": git(repo, "status", "--short"),
        "scope": "Committed trees only; working changes are listed but not included.",
        "local_only_commits": int(counts[0]),
        "upstream_only_commits": int(counts[1]),
        "local_changed_paths": sorted(local_paths),
        "upstream_changed_paths": sorted(upstream_paths),
        "overlap_paths": sorted(local_paths & upstream_paths),
        "upstream_high_risk_paths": sorted(
            path
            for path in upstream_paths
            if path.startswith(("app/", "main_logic/", "memory/"))
            and path.endswith(".py")
        ),
        "note": "No overlap does not imply behavioral compatibility. Review affected tests before merging.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("status", "backup", "fetch", "review", "merge", "build", "verify"),
    )
    parser.add_argument("--backups", type=Path, help="Optional external backup root")
    parser.add_argument(
        "--regression",
        type=Path,
        help="Verification suite manifest; defaults to local policy, then example",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        if args.command == "status":
            status(repo)
        elif args.command == "review":
            print(json.dumps(upstream_review(repo), ensure_ascii=True, indent=2))
        elif args.command == "backup":
            print(backup(repo, args.backups))
        elif args.command == "fetch":
            official_required(repo)
            subprocess.run(
                [
                    "git",
                    "fetch",
                    "--no-tags",
                    "origin",
                    "refs/heads/main:refs/remotes/origin/main",
                ],
                cwd=repo,
                check=True,
            )
            status(repo)
        elif args.command == "merge":
            merge_upstream(repo, args.backups)
        elif args.command == "build":
            build(repo)
        elif args.command == "verify":
            verify(repo, args.regression)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
