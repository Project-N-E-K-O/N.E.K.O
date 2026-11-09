"""Run the dependency-free OAuth settings behavior harness in the unit gate."""

import shutil
import subprocess
from pathlib import Path


def test_chatgpt_settings_frontend():
    node = shutil.which('node')
    assert node, 'Node.js is required for the frontend behavior tests'
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [node, '--test', 'tests/frontend/chatgpt_settings.test.cjs'],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
