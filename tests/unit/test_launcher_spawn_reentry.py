"""Spawn children re-enter launcher.py; pin what that re-entry must and must not do.

multiprocessing's spawn start method re-runs the parent's main module as
``__mp_main__`` before unpickling the child's target, with the parent's
``sys.path`` copied verbatim. For launcher.py that re-entry must:

* skip the ``launcher_core.runtime`` import chain (only the real entry path
  needs it; every spawned child used to pay for it), and
* still put the repo root first on ``sys.path``: a plugin host inserts plugin
  ``vendor/`` dirs at index 0, and a vendored top-level ``config`` / ``utils``
  package must not shadow the repo's when the target is unpickled.

The probe runs in a subprocess so the re-entry cannot leak modules or
``sys.path`` edits into the test session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import importlib.util, json, os, runpy, sys
repo, vendor = sys.argv[1], sys.argv[2]
rest = [p for p in sys.path if p not in ("", repo, vendor)]
sys.path[:] = [vendor, repo, *rest]
runpy.run_path(os.path.join(repo, "launcher.py"), run_name="__mp_main__")
spec = importlib.util.find_spec("config")
norm = lambda p: os.path.normcase(os.path.abspath(p))
print(json.dumps({
    "runtime_imported": "launcher_core.runtime" in sys.modules,
    "repo_first": norm(sys.path[0]) == norm(repo),
    "config_from_repo": bool(spec and spec.origin and norm(spec.origin).startswith(norm(repo))),
}))
"""


def test_spawn_reentry_skips_runtime_and_pins_repo_root(tmp_path):
    vendor = tmp_path / "vendor"
    (vendor / "config").mkdir(parents=True)
    (vendor / "config" / "__init__.py").write_text(
        "raise ImportError('a plugin vendor config shadowed the repo')\n",
        encoding="utf-8",
    )
    # If the runtime chain ever comes back into the re-entry, keep it off the
    # real user data root.
    env = {**os.environ, "NEKO_STORAGE_SELECTED_ROOT": str(tmp_path / "runtime_root")}

    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(REPO_ROOT), str(vendor)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report == {
        "runtime_imported": False,
        "repo_first": True,
        "config_from_repo": True,
    }
