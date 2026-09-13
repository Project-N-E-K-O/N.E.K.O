"""Run the media/scene regressions in the Windows unit CI gate."""
import json
import shutil
from pathlib import Path

import pytest
from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('script', sorted(path.name for path in (ROOT / 'tests/frontend').glob('test_watch_together*.mjs')))
def test_watch_together_frontend(script):
    node = shutil.which('node')
    assert node, 'Node is required for the watch-together frontend regression suite'
    # The CommonJS wrapper arms the shared launcher watchdog before importing
    # the ES module, retaining its relative imports and immediate failure exit.
    module_url = (ROOT / 'tests/frontend' / script).as_uri()
    result = run_node_script(
        node, f'import({json.dumps(module_url)}).catch(error => {{console.error(error);process.exit(1);}});', cwd=ROOT,
        capture_output=True, text=True, encoding='utf-8', timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
