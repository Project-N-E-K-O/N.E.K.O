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
"""The pytest process neither reports usage nor reaches the real runtime root.

See tests/process_isolation.py. plugin/tests has its own copy of the in-suite
checks (plugin/tests/unit/test_suite_isolation.py) because it is a separate
rootdir with its own conftest.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import process_isolation
from tests.telemetry_capture import exit_flush_requests

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_isolation_ran_before_any_product_module_was_imported():
    # None = conftest never called it; a non-empty tuple = it ran after some
    # module had already built its ConfigManager / logger / tracker.
    assert process_isolation.PRODUCT_MODULES_BEFORE_ISOLATION == ()


def test_the_process_exit_flush_reports_nothing_by_default(monkeypatch):
    assert exit_flush_requests(monkeypatch) == []


def test_a_telemetry_test_can_still_opt_in_against_a_local_collector(monkeypatch):
    from utils.token_tracker import reporting

    monkeypatch.setattr(reporting, "_DO_NOT_TRACK", False)

    assert exit_flush_requests(monkeypatch) == ["/api/v1/telemetry"]


_PROBE = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
from tests import process_isolation
process_isolation.isolate_test_process()

from utils.config_manager import ConfigManager
from utils.logger_config import RobustLoggerConfig
from utils.token_tracker import reporting
from tests import real_root_isolation

print(json.dumps({
    "product_modules_before": list(process_isolation.PRODUCT_MODULES_BEFORE_ISOLATION),
    "do_not_track": reporting._DO_NOT_TRACK,
    "selected_env": os.environ.get("NEKO_STORAGE_SELECTED_ROOT"),
    "anchor_env": os.environ.get("NEKO_STORAGE_ANCHOR_ROOT"),
    "app_docs_dir": str(ConfigManager("N.E.K.O").app_docs_dir),
    "stand_in": str(real_root_isolation.isolated_app_data_root()),
    "log_documents": str(RobustLoggerConfig._get_documents_directory(None)),
    "test_log_root": str(process_isolation.TEST_LOG_ROOT),
}))
"""


def test_a_fresh_interpreter_inheriting_a_real_root_and_no_opt_out_is_isolated(tmp_path):
    # What a shell started from the app looks like: the launcher's layout
    # variables are set and nothing opts out of telemetry. In-suite checks
    # cannot see this because the suite's own process was isolated already.
    inherited_root = tmp_path / "inherited-real-root"
    env = dict(os.environ)
    env.pop("NEKO_DO_NOT_TRACK", None)
    env.pop("DO_NOT_TRACK", None)
    env["NEKO_STORAGE_SELECTED_ROOT"] = str(inherited_root)
    env["NEKO_STORAGE_ANCHOR_ROOT"] = str(inherited_root)

    result = subprocess.run(
        [sys.executable, "-c", _PROBE, str(_REPO_ROOT)],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])

    assert seen["product_modules_before"] == []
    assert seen["do_not_track"] is True
    assert seen["selected_env"] is None
    assert seen["anchor_env"] is None
    assert Path(seen["app_docs_dir"]).is_relative_to(Path(seen["stand_in"]))
    assert seen["log_documents"] == seen["test_log_root"]
    assert not inherited_root.exists()
