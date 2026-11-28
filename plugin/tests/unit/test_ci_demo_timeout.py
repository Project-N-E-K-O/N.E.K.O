# TEMPORARY CI demo for task: per-test timeout / `--lf` flaky check.
# Removed again in the next commit.
import tempfile
import time
from pathlib import Path


def _first_time(name: str) -> bool:
    marker = Path(tempfile.gettempdir()) / f"neko-ci-demo-408-{name}"
    if marker.exists():
        return False
    marker.write_text("seen")
    return True


def test_demo_timeout_sleeps_past_the_limit():
    # No timeout marker: the 300 s limit from plugin/tests/pytest.ini applies.
    if _first_time("sleep"):
        time.sleep(330)
