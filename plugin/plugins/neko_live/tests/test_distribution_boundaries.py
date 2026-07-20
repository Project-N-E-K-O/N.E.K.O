from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]


def _require_git_checkout() -> str:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git executable not found")
    if not (REPO_ROOT / ".git").exists():
        pytest.skip("requires Git checkout metadata to inspect tracked files")
    return git


def test_plugin_runtime_artifacts_are_ignored_for_distribution():
    git = _require_git_checkout()

    for path in (
        "plugin/plugins/neko_live/plugin.toml.lock",
        ".codex-live-screen.png",
    ):
        completed = subprocess.run(
            [git, "check-ignore", "--no-index", "--quiet", path],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert completed.returncode == 0, completed.stderr or path


def test_neko_live_runtime_lock_is_not_tracked():
    git = _require_git_checkout()

    completed = subprocess.run(
        [git, "ls-files", "plugin/plugins/neko_live/plugin.toml.lock"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout.strip() == ""
