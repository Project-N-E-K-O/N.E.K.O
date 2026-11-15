"""Run 3D screen projection coverage with the shipped Three.js implementation."""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_model_screen_bounds_node_suite():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for 3D screen projection tests")
    suite = PROJECT_ROOT / "tests" / "frontend" / "model_screen_bounds.test.cjs"
    result = run_node_script(
        node,
        f"require({json.dumps(str(suite))});",
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
