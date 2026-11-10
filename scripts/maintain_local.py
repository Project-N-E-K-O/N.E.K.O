#!/usr/bin/env python3
"""Maintain the local integration branch without discarding working changes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


OFFICIAL_URL = "https://github.com/Project-N-E-K-O/N.E.K.O.git"
BUILD_OUTPUTS = ("static/react/neko-chat", "frontend/plugin-manager/dist")


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo).decode().strip()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def state_dir(repo):
    path = Path(git(repo, "rev-parse", "--absolute-git-dir")) / "local-maintenance"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


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
    url = git(repo, "remote", "get-url", "origin")
    if url.rstrip("/").removesuffix(".git") != OFFICIAL_URL.removesuffix(".git"):
        raise ValueError(f"origin is not the configured official repository: {url}")


def backup(repo, destination=None):
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
    raw = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=repo
    )
    names = [name.decode() for name in raw.split(b"\0") if name]
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


def verify(repo, policy_path=None):
    if git(repo, "ls-files", "--unmerged"):
        raise ValueError("Resolve merge conflicts before verification.")
    if policy_path is None:
        policy_path = repo / "local-desktop-ui/regression.json"
        if not policy_path.is_file():
            policy_path = repo / "local-desktop-ui/regression.example.json"
    policy = json.loads(Path(policy_path).read_text())
    report = state_dir(repo) / (
        "verify-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    )
    report.mkdir()
    head = git(repo, "rev-parse", "HEAD")
    before = subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=repo)
    results = []
    for suite in policy["suites"]:
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
    after = subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=repo)
    unchanged = head == git(repo, "rev-parse", "HEAD") and before == after
    receipt = {
        "commit": head,
        "source_unchanged": unchanged,
        "diff_sha256": hashlib.sha256(after).hexdigest(),
        "results": results,
    }
    write_json(report / "receipt.json", receipt)
    if not unchanged or any(item["returncode"] for item in results):
        raise ValueError(f"Verification failed or source changed. Inspect {report}")
    write_json(state_dir(repo) / "verified.json", receipt)
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
    print(json.dumps(result, ensure_ascii=False, indent=2))


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
        return {name.decode() for name in raw.split(b"\0") if name}

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
            print(json.dumps(upstream_review(repo), ensure_ascii=False, indent=2))
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
