import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NODE_RUNTIME_TESTS = (
    "test_neko_minigame_sdk_runtime.js",
    "test_neko_minigame_lifecycle_runtime.js",
    "test_neko_minigame_same_origin_host_runtime.js",
    "test_neko_minigame_avatar_host_runtime.js",
    "test_neko_minigame_drawing_avatar_host_runtime.js",
    "test_drawing_guess_sdk_integration.js",
)


@pytest.mark.frontend
@pytest.mark.parametrize("script_name", NODE_RUNTIME_TESTS)
def test_neko_minigame_sdk_node_runtime(script_name: str):
    """Keep the dependency-free SDK harnesses inside the default pytest tree."""
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")

    script_path = Path(__file__).with_name(script_name)
    try:
        result = run_node_script(
            node_path,
            f"require({json.dumps(str(script_path.resolve()))});",
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"{script_name} timed out: {exc}")

    output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
    assert result.returncode == 0, output or f"{script_name} exited with {result.returncode}"
