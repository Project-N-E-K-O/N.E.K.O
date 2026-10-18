"""Run the state machine against controlled out-of-order frontend requests."""
import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


@pytest.mark.unit
@pytest.mark.parametrize("component", ["state", "image", "editor", "cropper"])
def test_chat_avatar_frontend(component):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend state-machine tests")
    suite = Path(__file__).with_name(f"chat_avatar_{component}.test.cjs")
    result = run_node_script(
        node,
        "require(" + json.dumps(suite.as_posix()) + ");",
        cwd=str(suite.parents[2]),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
