import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = Path(__file__).with_name("test_chat_drawing_guess_visibility_runtime.js")


@pytest.mark.frontend
def test_chat_drawing_guess_visibility_node_runtime():
    """Preserve the chat BrowserWindow visibility across Drawing Guess."""
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    try:
        result = run_node_script(
            node_path,
            f"require({json.dumps(str(SCRIPT_PATH.resolve()))});",
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"{SCRIPT_PATH.name} timed out: {exc}")

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    assert result.returncode == 0, output or f"{SCRIPT_PATH.name} exited with {result.returncode}"
