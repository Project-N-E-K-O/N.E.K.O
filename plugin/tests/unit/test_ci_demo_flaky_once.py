# TEMPORARY CI demo of the Windows flaky-check re-run. Removed in the next commit.
import tempfile
from pathlib import Path


def test_demo_flaky_fails_once():
    marker = Path(tempfile.gettempdir()) / "neko-ci-demo-408-flaky"
    first = not marker.exists()
    marker.write_text("seen")
    assert not first, "first run fails on purpose"
