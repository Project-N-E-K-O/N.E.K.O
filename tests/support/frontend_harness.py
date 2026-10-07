"""Shared Node harness utilities for PR #3078 frontend contract tests."""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


def run_settings_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    """Run a settings harness from a temporary UTF-8 Node script."""

    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping app-settings harness test")
    return run_node_script(
        node_path,
        script,
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
