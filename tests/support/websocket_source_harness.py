import shutil
import subprocess
from pathlib import Path
import pytest
from tests.node_harness import run_node_script


def _run_settings_node_harness(script: str) -> subprocess.CompletedProcess[str]:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node is not installed; skipping app-settings harness test")
    # run_node_script writes the script to a temp file: node -e would put the
    # whole harness on the command line, which Windows refuses past 32767
    # characters and which encodes under the locale codec rather than UTF-8.
    return run_node_script(
        node_path,
        script,
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
