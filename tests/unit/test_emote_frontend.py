"""pytest entry point for the emote maker's browser-side suites.

The suites live in ``tests/frontend``:

* ``*.test.cjs`` — node:test suites that ``require`` the real modules under
  ``static/js``. They are loaded by absolute path rather than inlined, because
  several of them resolve the project root from ``__dirname`` (see
  ``spine_runtime_loader.test.cjs``) and ``run_node_script`` stages its script
  into a temp file, which would move ``__dirname`` out of the repo.
* ``test_emote_template*.mjs`` — ES modules loaded through a ``file://`` URL so
  their own relative imports keep resolving from the real file location.

``tests/unit/*.test.js`` used to hold the first group. Nothing in this repo
collects that suffix — pytest only gathers ``test_*.py``, and no runner matches
``*.test.js`` — so those files were dead weight that read like coverage. Moving
them here, with the wrappers below, is what makes them execute. Same migration
``tests/unit/test_vrm_lipsync_formant_static_contracts.py`` documents for the
formant suite.

Note the deliberate difference from that precedent: it inlines the suite text
into ``run_node_script`` and the suite rebuilds the repo root itself, which
forces every ``require`` in the suite to go through a ``PROJECT_ROOT`` shim.
These suites use plain relative ``require('../../static/...')`` and ``__dirname``
resolution, so they are loaded by absolute path from a one-line launcher
instead. Both forms still go through the shared launcher, which is what
``tests/unit/test_node_harness_contract.py`` enforces (temp-file form + UTF-8 in
both directions); neither hand-rolls a subprocess.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "tests" / "frontend"

CJS_SUITES = sorted(path.name for path in FRONTEND.glob("emote_*.test.cjs")) \
    + sorted(path.name for path in FRONTEND.glob("spine_runtime_loader.test.cjs"))
MJS_SUITES = sorted(path.name for path in FRONTEND.glob("test_emote_template*.mjs"))


def _node() -> str:
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("node not found")
    return node_path


@pytest.mark.parametrize("script", CJS_SUITES)
def test_emote_frontend_cjs(script: str) -> None:
    from tests.node_harness import run_node_script

    target = FRONTEND / script
    assert target.is_file(), f"missing suite {target}"
    result = run_node_script(
        _node(),
        f"require({json.dumps(target.as_posix())});",
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("script", MJS_SUITES)
def test_emote_frontend_mjs(script: str) -> None:
    from tests.node_harness import run_node_script

    target = FRONTEND / script
    assert target.is_file(), f"missing suite {target}"
    # Imported (not inlined) so the module's own relative imports resolve.
    result = run_node_script(
        _node(),
        f"import({json.dumps(target.as_uri())}).catch(error => {{console.error(error);process.exit(1);}});",
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
