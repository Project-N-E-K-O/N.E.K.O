"""Unit tests for the docs dead-link lint in scripts/check_docs_no_relative_paths.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_docs_no_relative_paths", ROOT / "scripts" / "check_docs_no_relative_paths.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "target",
    [
        "/Users/mac/.codex/experiments/run/report.md",
        "/home/dev/notes.md",
        "/private/tmp/report.json",
        "/tmp/report.md",
        "/var/folders/xy/T/report.md",
        "file:///Users/mac/report.md",
        "C:\\Users\\dev\\report.md",
        "D:/work/report.md",
    ],
)
def test_local_filesystem_paths_are_flagged(target):
    # Leading "/" normally means a site-absolute page, but an author's machine
    # path is a dead link on the docs site and leaks local usernames.
    assert _load_checker()._classify(target) == "local-path"


@pytest.mark.parametrize(
    "target",
    ["/logo.jpg", "/design/index", "https://github.com/o/r/blob/main/a.py", "#section", "./page"],
)
def test_site_links_are_not_flagged(target):
    assert _load_checker()._classify(target) is None


def test_docs_tree_has_no_unresolvable_links():
    assert _load_checker().main() == 0
