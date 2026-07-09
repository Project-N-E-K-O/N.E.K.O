from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_plugin_runtime_artifacts_are_ignored_for_distribution():
    tracked = subprocess.run(
        ["git", "ls-files", "plugin/plugins/neko_roast/plugin.toml.lock", "plugin/plugins/neko_roast/.codex-live-screen.png"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert tracked.stdout.strip() == ""

def test_neko_roast_runtime_lock_is_not_tracked():
    completed = subprocess.run(
        ["git", "ls-files", "plugin/plugins/neko_roast/plugin.toml.lock"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout.strip() == ""
