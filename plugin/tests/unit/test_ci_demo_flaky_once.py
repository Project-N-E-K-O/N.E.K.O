# TEMPORARY CI demo for task: per-test timeout / `--lf` flaky check.
# Removed again in the next commit.
import tempfile
import time  # noqa: F401
from pathlib import Path


def _first_time(name: str) -> bool:
    marker = Path(tempfile.gettempdir()) / f"neko-ci-demo-408-{name}"
    if marker.exists():
        return False
    marker.write_text("seen")
    return True


def test_demo_flaky_fails_once():
    assert not _first_time("flaky"), "first run fails on purpose"
