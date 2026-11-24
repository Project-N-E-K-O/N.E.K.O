# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""File selection of scripts/ci/select_shard_files.py and its workflow wiring.

A shard collects only the files this selector picks, so a marker form it
stops recognising is a shard that silently runs fewer tests. The audit job on
main catches that after merge; these tests pin the forms used today before it.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "select_shard_files.py"
SHARDS_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "unit-test-shards.yml"
UNIT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "unit-tests.yml"


def _load_selector():
    spec = importlib.util.spec_from_file_location("select_shard_files_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sel = _load_selector()

SELECTED_FOR_RUNTIME = {
    "tests/unit/test_module_mark.py": "import pytest\n\npytestmark = pytest.mark.runtime\n",
    "tests/unit/test_mark_list.py": "import pytest\n\npytestmark = [pytest.mark.asyncio, pytest.mark.runtime]\n",
    "tests/unit/sub/test_function_mark.py": "import pytest\n\n@pytest.mark.runtime\ndef test_a():\n    pass\n",
    "tests/unit/test_class_mark.py": "import pytest\n\n@pytest.mark.runtime\nclass TestA:\n    def test_a(self):\n        pass\n",
    "tests/unit/test_param_mark.py": (
        "import pytest\n\n@pytest.mark.parametrize('x', [1, pytest.param(2, marks=pytest.mark.runtime)])\n"
        "def test_a(x):\n    pass\n"
    ),
    "tests/unit/test_bare_mark.py": "from pytest import mark\n\n@mark.runtime\ndef test_a():\n    pass\n",
    "tests/unit/test_alias_mark.py": "import pytest\n\nRUNTIME = pytest.mark.runtime\n\n@RUNTIME\ndef test_a():\n    pass\n",
    "tests/unit/test_spaced_mark.py": "import pytest\n\npytestmark = pytest.mark . runtime\n",
    "tests/unit/sub/legacy_test.py": "import pytest\n\npytestmark = pytest.mark.runtime\n",
}
NOT_SELECTED_FOR_RUNTIME = {
    # A longer marker name sharing the prefix.
    "tests/unit/test_other_mark.py": "import pytest\n\npytestmark = pytest.mark.runtime_slow\n",
    # A plain word, not a marker.
    "tests/unit/test_word.py": "def test_a():\n    runtime = 1\n    assert runtime\n",
    # Not a test module by name: passing it explicitly would make pytest collect it anyway.
    "tests/unit/runtime_helpers.py": "import pytest\n\nRUNTIME = pytest.mark.runtime\n",
    "tests/unit/conftest.py": "import pytest\n\nRUNTIME = pytest.mark.runtime\n",
    # Outside tests/unit.
    "tests/integration/test_outside.py": "import pytest\n\npytestmark = pytest.mark.runtime\n",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    for rel, text in {**SELECTED_FOR_RUNTIME, **NOT_SELECTED_FOR_RUNTIME}.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    _git(tmp_path, "add", "-A")
    # Untracked: a CI checkout would not have it.
    (tmp_path / "tests/unit/test_untracked.py").write_bytes(b"import pytest\n\npytestmark = pytest.mark.runtime\n")
    return tmp_path


def test_selects_every_direct_marker_form(fake_repo: Path):
    assert set(sel.select_files("runtime", fake_repo)) == set(SELECTED_FOR_RUNTIME)


def test_selection_is_per_marker(fake_repo: Path):
    assert sel.select_files("unit_fast", fake_repo) == []


def test_select_refuses_to_write_an_empty_argument_file(tmp_path: Path, monkeypatch, capsys):
    # pytest given an empty @file would collect the whole rootdir instead.
    monkeypatch.setattr(sel, "select_files", lambda marker: [])
    out = tmp_path / "shard.args"
    assert sel.main(["select", "--marker", "runtime", "--out", str(out)]) == 1
    assert not out.exists()
    assert "Marker 'runtime' collected no tests" in capsys.readouterr().out


def test_select_writes_one_path_per_line_without_crlf(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sel, "select_files", lambda marker: ["tests/unit/test_a.py", "tests/unit/test_b.py"])
    out = tmp_path / "nested" / "shard.args"
    assert sel.main(["select", "--marker", "runtime", "--out", str(out)]) == 0
    assert out.read_bytes() == b"tests/unit/test_a.py\ntests/unit/test_b.py\n"


def test_this_repository_selects_files_for_every_shard_marker():
    for marker in sel.SHARD_MARKERS:
        files = sel.select_files(marker)
        assert files, marker
        assert all(f.startswith("tests/unit/") for f in files)
    assert "tests/unit/test_select_shard_files.py" in sel.select_files("unit_fast")


def test_shard_markers_match_the_workflows():
    workflow = yaml.safe_load(SHARDS_WORKFLOW.read_text(encoding="utf-8"))
    matrix = workflow["jobs"]["python-shard"]["strategy"]["matrix"]["include"]
    assert {entry["marker"] for entry in matrix} == set(sel.SHARD_MARKERS)
    # The full unit job deselects exactly the shard markers; anything else
    # would leave tests that no job runs, or run some twice.
    full_job = UNIT_WORKFLOW.read_text(encoding="utf-8")
    expr = re.search(r'-m "(not unit_fast[^"]*)"', full_job).group(1)
    assert set(re.findall(r"not (\w+)", expr)) == set(sel.SHARD_MARKERS)


def test_shard_job_runs_only_the_selection_with_the_marker_filter():
    workflow = yaml.safe_load(SHARDS_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["python-shard"]["steps"]
    select = next(s for s in steps if "select_shard_files.py select" in s.get("run", ""))
    args_file = re.search(r'--out "([^"]+)"', select["run"]).group(1)
    run = next(s for s in steps if s.get("name", "").startswith("Run ${{ matrix.name }}"))
    pytest_line = next(line for line in run["run"].splitlines() if "uv run pytest" in line)
    assert f'"@{args_file}"' in pytest_line
    # Selected files still hold unmarked tests: -m must stay.
    assert '-m "${{ matrix.marker }}"' in pytest_line
    assert "tests/unit " not in pytest_line
    # An empty shard still fails loudly.
    assert "$code -eq 5" in run["run"] and "collected no tests" in run["run"]
    assert steps.index(select) < steps.index(run)


def test_audit_job_runs_after_merge_with_the_shard_dependencies():
    workflow = yaml.safe_load(SHARDS_WORKFLOW.read_text(encoding="utf-8"))
    assert "push" in workflow[True] and "workflow_dispatch" in workflow[True]
    audit = workflow["jobs"]["shard-selection-audit"]
    assert audit["if"] == "github.event_name != 'pull_request'"
    runs = "\n".join(s.get("run", "") for s in audit["steps"])
    assert "select_shard_files.py audit" in runs
    shard_runs = "\n".join(s.get("run", "") for s in workflow["jobs"]["python-shard"]["steps"])
    for pin in re.findall(r"[\w-]+==[\d.]+", shard_runs):
        assert pin in runs, pin


pytestmark = pytest.mark.unit_fast
