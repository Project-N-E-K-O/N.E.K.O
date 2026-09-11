"""Exercise the soccer provider through the public SDK and trusted host."""
import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script


@pytest.mark.frontend
@pytest.mark.parametrize("script", [
    "test_soccer_avatar_host_runtime.js",
    "test_soccer_avatar_speech_integration.js",
    "test_soccer_sdk_migration_runtime.js",
    "test_soccer_host_adapter_runtime.js",
])
def test_soccer_avatar_speech_runtime(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not found")
    root = Path(__file__).resolve().parents[2]
    source = Path(__file__).with_name(script)
    result = run_node_script(
        node, f"require({json.dumps(str(source))});", cwd=root,
        capture_output=True, text=True, check=False, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
