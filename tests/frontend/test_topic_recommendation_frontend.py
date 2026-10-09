"""Run the shared recommendation panel against the real frontend source."""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


@pytest.mark.frontend
def test_topic_recommendation_controls_node_runtime():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not found")
    script = Path(__file__).with_name("topic_recommendation_controls.test.cjs")
    result = run_node_script(
        node,
        f"require({json.dumps(str(script.resolve()))});",
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
