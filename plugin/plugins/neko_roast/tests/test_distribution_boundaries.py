from __future__ import annotations

import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_plugin_runtime_artifacts_are_ignored_for_distribution():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "plugin/plugins/*/plugin.toml.lock" in gitignore
    assert ".codex-live-screen.png" in gitignore


def test_neko_roast_runtime_lock_is_not_tracked():
    completed = subprocess.run(
        ["git", "ls-files", "plugin/plugins/neko_roast/plugin.toml.lock"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert completed.stdout.strip() == ""
