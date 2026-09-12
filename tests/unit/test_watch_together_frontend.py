"""Run the media/scene regressions in the Windows unit CI gate."""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('script', sorted(path.name for path in (ROOT / 'tests/frontend').glob('test_watch_together*.mjs')))
def test_watch_together_frontend(script):
    node = shutil.which('node')
    assert node, 'Node is required for the watch-together frontend regression suite'
    result = subprocess.run(
        [node, str(ROOT / 'tests/frontend' / script)], cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
