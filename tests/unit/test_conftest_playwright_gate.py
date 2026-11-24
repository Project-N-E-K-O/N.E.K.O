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
"""When tests/conftest.py checks for (and installs) the Playwright Chromium.

The check runs in pytest_configure; on a fresh CI runner it downloads
Chromium for ~170 s. A run that cannot reach a browser skips it, so a wrong
"skip" shows up as a browser test failing to launch, and a wrong "check" as
minutes of startup in every unit-test-shards.yml job. Not shard-marked on
purpose: this file names Playwright, so a shard holding it would pay for the
install it is checking.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def gate(request):
    root_conftest = (REPO_ROOT / "tests" / "conftest.py").resolve()
    plugin = next(
        (
            p
            for p in request.config.pluginmanager.get_plugins()
            if Path(getattr(p, "__file__", "") or ".").resolve() == root_conftest
        ),
        None,
    )
    assert plugin is not None, "tests/conftest.py is not loaded as a plugin"
    return plugin._run_may_need_playwright


def _shard_selector():
    path = REPO_ROOT / "scripts" / "ci" / "select_shard_files.py"
    spec = importlib.util.spec_from_file_location("select_shard_files_for_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_no_arguments_or_a_directory_keeps_the_check(gate):
    assert gate([], REPO_ROOT)
    assert gate(["tests/unit"], REPO_ROOT)
    assert gate(["tests"], REPO_ROOT)
    assert gate([str(REPO_ROOT / "tests" / "unit")], REPO_ROOT)


def test_a_unit_file_that_drives_a_browser_keeps_the_check(gate):
    browser_file = "tests/unit/test_character_profile_ui.py"
    assert "sync_playwright" in (REPO_ROOT / browser_file).read_text(encoding="utf-8")
    assert gate([browser_file], REPO_ROOT)
    assert gate(["tests/unit/test_select_shard_files.py", browser_file], REPO_ROOT)
    assert gate([f"{browser_file}::test_anything"], REPO_ROOT)


def test_files_outside_tests_unit_keep_the_check(gate):
    # tests/frontend gets browsers through pytest-playwright fixtures it need not name.
    frontend = sorted((REPO_ROOT / "tests" / "frontend").glob("test_*.py"))
    assert frontend
    assert gate([str(frontend[0])], REPO_ROOT)
    assert gate(["tests/integration/test_voice_enrollment_duration_real.py"], REPO_ROOT)


def test_missing_or_non_python_arguments_keep_the_check(gate):
    assert gate(["tests/unit/test_does_not_exist.py"], REPO_ROOT)
    assert gate(["tests/unit/README_human_like_eval.md"], REPO_ROOT)


def test_browserless_unit_files_skip_it_relative_or_absolute(gate, tmp_path):
    rel = "tests/unit/test_select_shard_files.py"
    assert gate([rel], REPO_ROOT) is False
    assert gate([f"{rel}::test_selection_is_per_marker"], REPO_ROOT) is False
    assert gate([str(REPO_ROOT / rel)], tmp_path) is False


def test_every_shard_selection_skips_it(gate):
    # unit-test-shards.yml passes these lists through @file; each would pay
    # ~170 s on a fresh runner if one of its files named Playwright.
    selector = _shard_selector()
    for marker in selector.SHARD_MARKERS:
        files = selector.select_files(marker)
        needing = [f for f in files if gate([f], REPO_ROOT)]
        assert not needing, (
            f"shard {marker} would install Chromium for {needing}; if that file really drives a"
            " browser, keep it out of the shard or accept the startup cost and update this test"
        )
