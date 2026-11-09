"""Execute the frontend capture bridge with deterministic native-provider doubles."""

from pathlib import Path
import shutil
import subprocess
import pytest


def test_screen_inspection_bridge():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the capture bridge tests")
    result = subprocess.run(
        [node, str(Path(__file__).with_name("screen_inspection.mjs"))],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
