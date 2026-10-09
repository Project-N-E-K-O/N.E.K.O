"""Drive queue ownership, failure recovery, and decoder boundaries through Node."""

import json
import shutil
import subprocess
from pathlib import Path

from tests.node_harness import run_node_script


def test_audio_queue_and_decoder_lifecycle():
    """Cancellation and close paths preserve the next owner's audio and progress."""
    root = Path(__file__).resolve().parents[2]
    node = shutil.which("node")
    assert node, "Node is required for the frontend audio lifecycle regressions"
    source = (root / "tests/frontend/assistant-audio-lifecycle.test.cjs").read_text(encoding="utf-8")
    # The shared launcher stages the script outside the repo; resolve its assets
    # from the explicit test cwd rather than the launcher's temporary directory.
    source = source.replace("path.resolve(__dirname, '../..')", "process.cwd()")
    result: subprocess.CompletedProcess[str] = run_node_script(
        node, source, cwd=root, capture_output=True, check=False, timeout=30,
    )
    assert result.returncode == 0, f"audio lifecycle harness failed:\n{result.stdout}\n{result.stderr}"
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["count"] == 29
    assert "cancel-wakes-receive-queue-before-old-owner-finishes" in report["checks"]
    assert "cancel-wakes-outer-blob-resume-and-reset-waits" in report["checks"]
