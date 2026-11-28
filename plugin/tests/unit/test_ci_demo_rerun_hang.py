# TEMPORARY CI demo of the Windows flaky-check re-run. Removed in the next commit.
# Only in the demo job (NEKO_DEMO_MODE=kill): fails fast in the full run, then
# hangs in the re-run so the re-run's hard stop has to kill pytest.
import os
import tempfile
import time
from pathlib import Path


def test_demo_hangs_in_the_rerun():
    if os.environ.get("NEKO_DEMO_MODE") != "kill":
        return
    marker = Path(tempfile.gettempdir()) / "neko-ci-demo-408-hang"
    if not marker.exists():
        marker.write_text("seen")
        raise AssertionError("first run fails on purpose")
    time.sleep(200)
