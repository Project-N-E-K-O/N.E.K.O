"""Run the dependency-free OAuth settings behavior harness in the unit gate."""

import os
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_stdin


def test_chatgpt_settings_frontend():
    node = shutil.which('node')
    if not node and not os.environ.get('CI'):
        pytest.skip('Node.js is not installed locally')
    assert node, 'Node.js is required for the frontend behavior tests'
    root = Path(__file__).resolve().parents[2]
    result = run_node_stdin(
        node, "require('./tests/frontend/chatgpt_settings.test.cjs');",
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
