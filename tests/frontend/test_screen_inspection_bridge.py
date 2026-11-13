"""Execute the frontend capture bridge with deterministic native-provider doubles."""

from pathlib import Path
import json
import shutil
import pytest

from tests.node_harness import run_node_script


def test_screen_inspection_bridge():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the capture bridge tests")
    module_url = Path(__file__).with_name("screen_inspection.mjs").resolve().as_uri()
    # A CommonJS entry arms the shared watchdog before importing the ESM suite.
    result = run_node_script(
        node,
        f"import({json.dumps(module_url)}).catch(error => {{ console.error(error); process.exitCode = 1; }});",
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
