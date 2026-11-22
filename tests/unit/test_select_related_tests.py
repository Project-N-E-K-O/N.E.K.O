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
"""Selection rules of scripts/ci/select_related_tests.py and the workflow wiring around it.

The PR gate runs only what the selector picks, so a rule that silently stops
selecting is a hole in CI that stays green. Every rule is pinned against a
small synthetic repository laid out like this one: application packages with
a facade ``__init__``, test helpers between tests and application code, a
sub-directory conftest, shard-marked files and tests that read non-Python
files by name.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ci" / "select_related_tests.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "unit-tests.yml"


def _load_selector():
    spec = importlib.util.spec_from_file_location("select_related_tests_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sel = _load_selector()

FILES = {
    "pyproject.toml": "[project]\nname = 'fake'\n",
    "pytest.ini": "[pytest]\n",
    "docs/guide.md": "# guide\n",
    "README.md": "# readme\n",
    "static/widget-panel.js": "export const x = 1;\n",
    "app/__init__.py": "from .core import Engine\nfrom . import heavy\n\nLOCAL = 1\n",
    "app/core.py": "from app.util import helper\n\nclass Engine:\n    pass\n",
    "app/util.py": "def helper():\n    return 1\n",
    "app/heavy.py": "VALUE = 2\n",
    "app/other.py": "VALUE = 3\n",
    "app/patched.py": "VALUE = 4\n",
    "app/dynamic.py": "VALUE = 5\n",
    "tests/__init__.py": "",
    "tests/conftest.py": "from tests.root_helper import guard\n",
    "tests/root_helper.py": "def guard():\n    return None\n",
    "tests/unit/__init__.py": "",
    "tests/unit/conftest.py": "",
    "tests/unit/fakes_helper.py": "from app.other import VALUE\n\ndef make():\n    return VALUE\n",
    "tests/unit/sibling_helper.py": "import app.heavy\n",
    "tests/unit/test_core.py": "from app.core import Engine\n\ndef test_engine():\n    assert Engine\n",
    "tests/unit/test_facade.py": "from app import Engine\n\ndef test_facade():\n    assert Engine\n",
    "tests/unit/test_via_helper.py": (
        "from tests.unit.fakes_helper import make\n\ndef test_make():\n    assert make() == 3\n"
    ),
    "tests/unit/test_sibling.py": "import sibling_helper\n\ndef test_sibling():\n    assert sibling_helper\n",
    "tests/unit/test_static_text.py": (
        "from pathlib import Path\n\n"
        "def test_reads_widget():\n    assert Path('static/widget-panel.js').name\n"
    ),
    "tests/unit/test_monkeypatch_string.py": (
        "def test_patch(monkeypatch):\n    monkeypatch.setattr('app.patched.VALUE', 9)\n"
    ),
    # import_module in application code (outside tests/, where dotted strings
    # are not read as module names) is the only edge to app.dynamic.
    "app/registry.py": "import importlib\n\ndef load():\n    return importlib.import_module('app.dynamic')\n",
    "tests/unit/test_importlib.py": (
        "from app.registry import load\n\ndef test_dynamic():\n    assert load()\n"
    ),
    "tests/unit/test_marked.py": (
        "import pytest\nfrom app.core import Engine\n\n"
        "pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]\n\n"
        "def test_marked():\n    assert Engine\n"
    ),
    "tests/unit/test_function_marked.py": (
        "import pytest\nfrom app.util import helper\n\n"
        "@pytest.mark.unit_fast\ndef test_fast():\n    assert helper\n\n"
        "def test_plain():\n    assert helper\n"
    ),
    "tests/unit/test_unrelated.py": "def test_alone():\n    assert True\n",
    # Names a doc and a workflow: the docs rule must win over the literal rule.
    "tests/unit/test_docs_reader.py": (
        "def test_docs():\n    assert 'docs/guide.md' and 'README.md' and 'workflows/docs.yml'\n"
    ),
    "tests/unit/sub/__init__.py": "",
    "tests/unit/sub/conftest.py": "from tests.unit.sub.sub_helper import fixture_value\n",
    "tests/unit/sub/sub_helper.py": "def fixture_value():\n    return 1\n",
    "tests/unit/sub/test_a.py": "def test_a():\n    assert True\n",
    "tests/unit/sub/test_b.py": "class TestB:\n    def test_one(self):\n        pass\n\n    def test_two(self):\n        pass\n",
}

ALL_CANDIDATES = sorted(
    p for p in FILES if p.startswith("tests/unit/") and Path(p).name.startswith("test_") and p != "tests/unit/test_marked.py"
)


def _write_repo(root: Path) -> None:
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("fake_repo")
    _write_repo(root)
    return root


@pytest.fixture(scope="module")
def index(repo):
    return sel.build_index(repo)


def _selected(index, *changed: str) -> list[str]:
    return sel.select(index, list(changed)).files


def test_transitive_dependency_selects_every_importer(index):
    picked = _selected(index, "app/util.py")
    # test_core -> app.core -> app.util; test_facade reaches app.core through the facade's re-export.
    assert "tests/unit/test_core.py" in picked
    assert "tests/unit/test_facade.py" in picked
    assert "tests/unit/test_unrelated.py" not in picked
    assert "tests/unit/test_via_helper.py" not in picked


def test_dependency_through_a_test_helper_is_followed(index):
    # The nekoclaw regression in #3963 was only visible this way: the test
    # imports a helper under tests/, and the helper imports the changed code.
    picked = _selected(index, "app/other.py")
    assert picked == ["tests/unit/test_via_helper.py"]


def test_changed_test_helper_selects_its_importers(index):
    assert _selected(index, "tests/unit/fakes_helper.py") == ["tests/unit/test_via_helper.py"]


def test_bare_sibling_import_inside_a_test_directory_resolves(index):
    assert "tests/unit/test_sibling.py" in _selected(index, "app/heavy.py")


def test_dotted_string_and_import_module_literals_count_as_imports(index):
    assert _selected(index, "app/patched.py") == ["tests/unit/test_monkeypatch_string.py"]
    assert _selected(index, "app/dynamic.py") == ["tests/unit/test_importlib.py"]


def test_file_name_mentioned_in_a_test_selects_it(index):
    assert _selected(index, "static/widget-panel.js") == ["tests/unit/test_static_text.py"]


def test_file_name_inside_a_longer_name_does_not_match(index):
    # "panel.js" is a suffix of "widget-panel.js" but names a different file.
    assert _selected(index, "static/panel.js") == []


def test_facade_only_carries_what_the_importer_uses(index):
    # app/__init__ imports heavy too, but test_facade only takes Engine from it.
    assert "tests/unit/test_facade.py" not in _selected(index, "app/heavy.py")
    # A change to the facade itself still reaches everything that executes it.
    picked = _selected(index, "app/__init__.py")
    assert {"tests/unit/test_facade.py", "tests/unit/test_core.py"} <= set(picked)


def test_changed_test_file_selects_itself(index):
    assert _selected(index, "tests/unit/test_unrelated.py") == ["tests/unit/test_unrelated.py"]


@pytest.mark.parametrize("path", sorted(sel.GLOBAL_FILES))
def test_global_files_select_the_full_suite(index, path):
    result = sel.select(index, ["app/heavy.py", path])
    assert result.mode == "full"
    assert result.files == ALL_CANDIDATES


def test_helper_of_root_conftest_counts_as_global(index):
    result = sel.select(index, ["tests/root_helper.py"])
    assert result.mode == "full"


def test_sub_directory_conftest_selects_its_directory(index):
    for changed in ("tests/unit/sub/conftest.py", "tests/unit/sub/sub_helper.py"):
        assert _selected(index, changed) == ["tests/unit/sub/test_a.py", "tests/unit/sub/test_b.py"]


def test_shard_marked_files_are_left_to_the_shards(index):
    assert index.tests["tests/unit/test_marked.py"].shard_marked
    assert "tests/unit/test_marked.py" not in _selected(index, "app/core.py")
    assert "tests/unit/test_marked.py" not in _selected(index, "tests/unit/test_marked.py")
    assert "tests/unit/test_marked.py" not in sel.select(index, ["pytest.ini"]).files
    # A marker on one function leaves the rest of the file to this gate; pytest's -m handles the function.
    assert not index.tests["tests/unit/test_function_marked.py"].shard_marked
    assert "tests/unit/test_function_marked.py" in _selected(index, "app/util.py")


@pytest.mark.parametrize(
    "changed",
    [["docs/guide.md"], ["README.md"], ["docs/guide.md", "README.md"]],
)
def test_docs_and_markdown_select_nothing(index, changed):
    result = sel.select(index, changed)
    assert result.mode == "none"
    assert result.files == []


def test_other_workflows_only_select_tests_that_read_them(index):
    assert _selected(index, ".github/workflows/docs.yml") == ["tests/unit/test_docs_reader.py"]
    assert _selected(index, ".github/workflows/analyze.yml") == []


def test_test_weights_count_methods_in_test_classes(index):
    assert index.tests["tests/unit/sub/test_b.py"].weight == 2
    assert index.tests["tests/unit/test_function_marked.py"].weight == 2


# ---- shard planning -------------------------------------------------------


def _weights(n: int) -> dict[str, int]:
    return {f"t{i:03d}.py": 1 + (i * 7) % 23 for i in range(n)}


@pytest.mark.parametrize("share_of_total, shards", [(0.05, 1), (0.29, 1), (0.31, 2), (0.55, 2), (0.61, 3), (0.9, 3), (0.95, 4), (1.0, 4)])
def test_shard_count_follows_selected_share(share_of_total, shards):
    weights = {f"t{i:04d}.py": 1 for i in range(1000)}
    files = sorted(weights)[: int(1000 * share_of_total)]
    plan = sel.plan_shards(files, weights, total_weight=1000)
    assert len(plan) == shards


def test_shards_are_balanced_and_cover_every_file_once():
    weights = _weights(300)
    files = sorted(weights)
    plan = sel.plan_shards(files, weights, total_weight=sum(weights.values()))
    assert len(plan) == sel.MAX_SHARDS
    flat = [f for shard in plan for f in shard]
    assert sorted(flat) == files and len(flat) == len(set(flat))
    loads = [sum(weights[f] for f in shard) for shard in plan]
    # Greedy longest-first: no shard exceeds another by more than the heaviest file.
    assert max(loads) - min(loads) <= max(weights.values())
    assert plan == sel.plan_shards(list(reversed(files)), weights, total_weight=sum(weights.values()))


def test_no_files_means_no_shards():
    assert sel.plan_shards([], {}, total_weight=100) == []


# ---- command line, end to end on a real git history --------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _run_cli(root: Path, out: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), "--out-dir", str(out), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.fixture()
def git_repo(tmp_path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _write_repo(root)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "base")
    _git(root, "checkout", "-q", "-b", "feature")
    (root / "app" / "other.py").write_bytes(b"VALUE = 30\n")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-am", "change")
    # main moves on after the branch point: the diff must come from the merge-base.
    _git(root, "checkout", "-q", "main")
    (root / "app" / "util.py").write_bytes(b"def helper():\n    return 2\n")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-am", "main moves")
    _git(root, "checkout", "-q", "feature")
    return root


def test_cli_writes_args_files_and_step_outputs(git_repo, tmp_path):
    out = tmp_path / "out"
    gh_output = tmp_path / "gh_output"
    summary = tmp_path / "summary.md"
    proc = _run_cli(git_repo, out, "--base", "main", "--head", "HEAD", "--github-output", str(gh_output), "--summary", str(summary))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / "selection.json").read_bytes().decode("utf-8"))
    assert report["changed_files"] == ["app/other.py"]
    assert report["selected_files"] == ["tests/unit/test_via_helper.py"]
    assert (out / "shard-1.args").read_bytes().decode("utf-8").splitlines() == ["tests/unit/test_via_helper.py"]
    outputs = dict(line.split("=", 1) for line in gh_output.read_bytes().decode("utf-8").splitlines())
    assert outputs == {"mode": "partial", "count": "1", "shard_count": "1", "matrix": "[1]"}
    assert "Related unit tests" in summary.read_bytes().decode("utf-8")


def test_cli_falls_back_to_the_full_suite_when_the_diff_fails(git_repo, tmp_path):
    out = tmp_path / "out"
    gh_output = tmp_path / "gh_output"
    proc = _run_cli(git_repo, out, "--base", "no-such-revision", "--github-output", str(gh_output))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((out / "selection.json").read_bytes().decode("utf-8"))
    assert report["mode"] == "full"
    assert report["selected_files"] == ALL_CANDIDATES
    assert "count=0" not in gh_output.read_bytes().decode("utf-8")


def test_cli_reports_nothing_to_run_for_a_docs_change(git_repo, tmp_path):
    (git_repo / "docs" / "guide.md").write_bytes(b"# changed\n")
    _git(git_repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-am", "docs")
    changed = tmp_path / "changed.txt"
    changed.write_bytes(b"docs/guide.md\n")
    out = tmp_path / "out"
    gh_output = tmp_path / "gh_output"
    proc = _run_cli(git_repo, out, "--changed-files", str(changed), "--github-output", str(gh_output))
    assert proc.returncode == 0, proc.stderr
    outputs = dict(line.split("=", 1) for line in gh_output.read_bytes().decode("utf-8").splitlines())
    assert outputs["count"] == "0" and outputs["matrix"] == "[]"
    assert not list(out.glob("shard-*.args"))


# ---- workflow wiring ----------------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_bytes().decode("utf-8"))


def _triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def _step_runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_full_suite_no_longer_runs_on_pull_requests(workflow):
    job = workflow["jobs"]["unit-pytest"]
    assert job["name"] == "Unit pytest (Windows)"
    assert job["if"] == "github.event_name != 'pull_request'"
    triggers = _triggers(workflow)
    assert set(triggers) >= {"push", "workflow_dispatch", "pull_request"}


def test_related_summary_check_always_reports_on_pull_requests(workflow):
    triggers = _triggers(workflow)
    # A paths filter would make the required check disappear from some PRs.
    assert "paths" not in (triggers["pull_request"] or {})
    assert "paths-ignore" not in (triggers["pull_request"] or {})
    summary = workflow["jobs"]["unit-pytest-related"]
    assert summary["name"] == "Unit pytest (related)"
    assert summary["if"].startswith("always()")
    assert set(summary["needs"]) == {"select-related", "unit-pytest-related-shard"}


def test_related_shards_run_exactly_like_the_full_job(workflow):
    full = _step_runs(workflow["jobs"]["unit-pytest"])
    shard_job = workflow["jobs"]["unit-pytest-related-shard"]
    shard = _step_runs(shard_job)
    assert shard_job["runs-on"] == workflow["jobs"]["unit-pytest"]["runs-on"]
    marker_expr = '-m "not unit_fast and not runtime and not integration_serial and not frontend_contract"'
    for snippet in (marker_expr, "-n auto --dist loadfile", "--basetemp \"${{ runner.temp }}/"):
        assert snippet in full and snippet in shard
    for pin in ("uv pip install pyclipper==1.4.0", "uv pip install kaldi-native-fbank==1.22.3"):
        assert pin in full and pin in shard
    # The file list must never be spelled out on the Windows command line.
    assert '"@related-tests/shard-${{ matrix.shard }}.args"' in shard


def test_selection_job_does_not_install_the_app(workflow):
    job = workflow["jobs"]["select-related"]
    assert job["runs-on"] == "ubuntu-latest"
    runs = _step_runs(job)
    assert "scripts/ci/select_related_tests.py" in runs
    assert "uv sync" not in runs and "pip install" not in runs


def test_selector_never_imports_application_code():
    import ast

    tree = ast.parse(SCRIPT.read_bytes().decode("utf-8"))
    stdlib = set(sys.stdlib_module_names)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            names = [node.module]
        else:
            continue
        for name in names:
            assert name.split(".")[0] in stdlib | {"__future__"}, name
