#!/usr/bin/env python3
"""List the tests/unit files a unit-test-shards.yml shard has to collect.

Each Python shard in unit-test-shards.yml runs ``pytest -m <marker>``. Given
``tests/unit`` it imports all ~1700 test files (and the application code they
pull in) to keep the few hundred that carry the marker: about five minutes of
cold start on a fresh Windows runner before the first test runs. This script
reads the files as text instead and hands pytest only the ones that can carry
the marker; the shard still passes ``-m <marker>``, so a selected file's
unmarked tests stay deselected exactly as before.

Selection: a tracked test module under tests/unit (pytest's default
``test_*.py`` / ``*_test.py`` names) is selected for ``<marker>`` when its
source contains ``mark.<marker>`` - this covers module ``pytestmark``, class
and function decorators, ``pytest.param(..., marks=...)``, ``from pytest import
mark`` and a marker stored in a variable first. Comments and strings that
happen to match only add a file, never drop one.

What text cannot see - a marker that reaches a test through another module
(an imported base class or decorator, test functions re-exported from another
test file, a conftest hook calling ``add_marker``) - is what ``audit`` is for:
it collects the whole of tests/unit once, collects each shard's selection with
``-m <marker>``, and fails unless the two sets of test ids are identical. It
runs on every push to main (and on demand), next to the full unit suite.

Usage:
  select_shard_files.py select --marker runtime --out shard.args
      Write the selected paths, one per line, for ``pytest @shard.args``.
      Exits 1 when nothing is selected: an empty argument file would make
      pytest fall back to collecting the whole rootdir.
  select_shard_files.py audit
      Compare full collection against every shard's selection (see above).

Standard library only for ``select``; ``audit`` needs the test environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = "tests/unit"
# The markers of unit-test-shards.yml's python-shard matrix. The full unit
# job in unit-tests.yml deselects exactly these.
SHARD_MARKERS = ("unit_fast", "runtime", "integration_serial", "frontend_contract")
# pytest's default python_files; pytest.ini does not override it. An explicit
# path is collected whatever its name, so a helper module must never be passed.
TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")
AUDIT_DIFF_LIMIT = 40


def _tracked_test_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", TEST_ROOT],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout.decode("utf-8")
    files = []
    for rel in out.split("\0"):
        if not rel:
            continue
        name = rel.rsplit("/", 1)[-1]
        if any(fnmatch(name, pattern) for pattern in TEST_FILE_PATTERNS):
            files.append(rel)
    return sorted(files)


def _marker_pattern(marker: str) -> re.Pattern[str]:
    return re.compile(rf"\bmark\s*\.\s*{re.escape(marker)}\b")


def select_files(marker: str, root: Path = REPO_ROOT) -> list[str]:
    pattern = _marker_pattern(marker)
    selected = []
    for rel in _tracked_test_files(root):
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            # Listed by git but missing on disk (sparse checkout, deleted in
            # the working tree): pytest could not collect it either.
            continue
        if pattern.search(text):
            selected.append(rel)
    return selected


def _write_args(path: Path, files: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not write_text: on Windows text mode would write CRLF.
    path.write_bytes(("\n".join(files) + "\n").encode("utf-8"))


def cmd_select(args: argparse.Namespace) -> int:
    files = select_files(args.marker)
    if not files:
        print(f"Marker '{args.marker}' collected no tests: no file under {TEST_ROOT} mentions mark.{args.marker}.")
        return 1
    _write_args(Path(args.out), files)
    print(f"{args.marker}: {len(files)} files -> {args.out}")
    return 0


class _Recorder:
    """pytest plugin: record each collected test id with its shard markers."""

    def __init__(self) -> None:
        self.items: dict[str, list[str]] = {}

    def pytest_collection_finish(self, session) -> None:  # noqa: ANN001 - pytest hook
        for item in session.items:
            names = {mark.name for mark in item.iter_markers()}
            self.items[item.nodeid] = sorted(names.intersection(SHARD_MARKERS))


def cmd_collect(args: argparse.Namespace) -> int:
    """Internal: run one collection in this process and dump the test ids."""
    import pytest

    recorder = _Recorder()
    pytest_args = ["--collect-only", "-qq", "--disable-warnings", "-p", "no:cacheprovider", *args.pytest_args]
    code = pytest.main(pytest_args, plugins=[recorder])
    Path(args.out).write_bytes(json.dumps({"exit": int(code), "items": recorder.items}).encode("utf-8"))
    return 0


def _collect(pytest_args: list[str], workdir: Path, name: str) -> tuple[int, dict[str, list[str]]]:
    # A fresh interpreter per collection: test modules and conftests imported
    # by one collection must not leak into the next.
    out = workdir / f"collect-{name}.json"
    cmd = [sys.executable, str(Path(__file__).resolve()), "_collect", "--out", str(out), "--", *pytest_args]
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=REPO_ROOT, check=True)
    data = json.loads(out.read_bytes().decode("utf-8"))
    return data["exit"], data["items"]


def cmd_audit(args: argparse.Namespace) -> int:
    failed = False
    with tempfile.TemporaryDirectory(prefix="shard-audit-") as tmp:
        workdir = Path(tmp)
        code, full = _collect([TEST_ROOT], workdir, "full")
        if code != 0:
            print(f"Full collection of {TEST_ROOT} failed (pytest exit {code}); cannot audit.")
            return 1
        print(f"full collection: {len(full)} tests")
        for marker in args.markers:
            expected = {nodeid for nodeid, marks in full.items() if marker in marks}
            files = select_files(marker)
            if not files:
                print(f"{marker}: no file selected; full collection has {len(expected)} tests")
                failed = failed or bool(expected)
                continue
            args_file = workdir / f"{marker}.args"
            _write_args(args_file, files)
            code, picked = _collect([f"@{args_file}", "-m", marker], workdir, marker)
            got = set(picked)
            missing = sorted(expected - got)
            extra = sorted(got - expected)
            status = "ok" if code == 0 and not missing and not extra else "MISMATCH"
            print(
                f"{marker}: {status} - {len(files)} files, full={len(expected)} selected={len(got)}"
                f" missing={len(missing)} extra={len(extra)} (pytest exit {code})"
            )
            for label, ids in (("missing (marked, but its file is not selected)", missing), ("extra", extra)):
                for nodeid in ids[:AUDIT_DIFF_LIMIT]:
                    print(f"  {label}: {nodeid}")
                if len(ids) > AUDIT_DIFF_LIMIT:
                    print(f"  ... {len(ids) - AUDIT_DIFF_LIMIT} more {label}")
            if status != "ok":
                failed = True
    if failed:
        print(
            "\nA shard would not run the same tests as `pytest tests/unit -m <marker>`. Usually a marker"
            " reaches tests through another module; write `pytest.mark.<marker>` in the test file itself"
            f" (module pytestmark, class or function) or teach {Path(__file__).name} the new form."
        )
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_select = sub.add_parser("select", help="write a shard's pytest argument file")
    p_select.add_argument("--marker", required=True, choices=SHARD_MARKERS)
    p_select.add_argument("--out", required=True, help="argument file to write, for `pytest @<file>`")
    p_select.set_defaults(func=cmd_select)

    p_audit = sub.add_parser("audit", help="check every selection against a full collection")
    p_audit.add_argument("--markers", nargs="+", default=list(SHARD_MARKERS), choices=SHARD_MARKERS)
    p_audit.set_defaults(func=cmd_audit)

    p_collect = sub.add_parser("_collect", help=argparse.SUPPRESS)
    p_collect.add_argument("--out", required=True)
    p_collect.add_argument("pytest_args", nargs=argparse.REMAINDER)
    p_collect.set_defaults(func=cmd_collect)

    args = parser.parse_args(argv)
    if getattr(args, "pytest_args", None) and args.pytest_args[0] == "--":
        args.pytest_args = args.pytest_args[1:]
    return args.func(args)


if __name__ == "__main__":
    os.chdir(REPO_ROOT)
    sys.exit(main())
