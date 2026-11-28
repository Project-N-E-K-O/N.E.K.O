"""Keep the relocated knowledge core discoverable without loading its host.

Wheel/sdist and all three frozen-build entrypoints must agree on the namespace.
The import probe runs in an isolated child so a server or memory import cannot
be hidden by pytest's already-loaded application modules.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib

import pytest
import yaml

from tests.process_isolation import child_process_env

pytestmark = pytest.mark.integration_serial
REPO = Path(__file__).resolve().parents[2]


def test_knowledge_wheel_and_sdist_use_services_namespace():
    """Ship the core through services while retaining legacy corpus exclusions."""
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    targets = config["tool"]["hatch"]["build"]["targets"]
    for target, key in (("wheel", "packages"), ("sdist", "include")):
        assert "services" in targets[target][key]
        assert "knowledge" not in targets[target][key]
        assert {"services/knowledge/data", "services/knowledge/moegirl_knowledge"} <= set(targets[target]["exclude"])
    assert not (REPO / "knowledge" / "__init__.py").is_file()
    assert (REPO / "services" / "knowledge" / "__init__.py").is_file()


@pytest.mark.parametrize("workflow,step_name", [
    ("build-desktop.yml", "Build with Nuitka (Unix)"),
    ("build-desktop.yml", "Build with Nuitka (Windows)"),
    ("build-desktop-linux.yml", "Build with Nuitka"),
])
def test_frozen_build_includes_relocated_knowledge(workflow, step_name):
    """Check actual compiler steps, including the Linux mirror that PR CI skips."""
    config = yaml.safe_load((REPO / ".github" / "workflows" / workflow).read_text(encoding="utf-8"))
    steps = [step for job in config["jobs"].values() for step in job.get("steps", []) if step.get("name") == step_name]
    assert len(steps) == 1
    command = steps[0]["run"]
    assert "--include-package=services.knowledge" in command
    assert not re.search(r"--include-package=knowledge(?:\s|[\"']|$)", command)


def test_core_import_does_not_initialize_memory_or_server(tmp_path):
    """Import only the core from a fresh child with project isolation installed."""
    program = """
import json
import sys
from tests import process_isolation
process_isolation.isolate_test_process()
assert process_isolation.PRODUCT_MODULES_BEFORE_ISOLATION == ()
assert 'memory' not in sys.modules and 'app.memory_server' not in sys.modules
import services.knowledge.service as core
assert 'memory' not in sys.modules and 'app.memory_server' not in sys.modules
print(json.dumps({'module': core.__name__, 'file': core.__file__}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program], cwd=REPO,
        env={**child_process_env(tmp_path / "runtime"), "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout.splitlines()[-1])
    assert report["module"] == "services.knowledge.service"
    assert Path(report["file"]).resolve() == REPO / "services" / "knowledge" / "service.py"
