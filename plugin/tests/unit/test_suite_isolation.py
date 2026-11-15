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
"""The plugin suite neither reports usage nor reaches the real runtime root.

plugin/tests is its own rootdir, so tests/conftest.py never runs here; its
conftest calls the same tests/process_isolation.py. The helper itself is
covered in tests/unit/test_pytest_process_isolation.py; these check that this
conftest wires it in, and early enough.
"""

import pytest

from tests import process_isolation, real_root_isolation, storage_root_env_guard
from tests.telemetry_capture import exit_flush_requests

pytestmark = pytest.mark.plugin_unit


def test_isolation_ran_before_any_product_module_was_imported():
    # Collecting this suite builds the ConfigManager singleton (and runs its
    # migration) through conftest's own imports, so "installed somewhere in
    # conftest" is not enough: it has to precede all of them.
    assert process_isolation.PRODUCT_MODULES_BEFORE_ISOLATION == ()


def test_the_process_exit_flush_reports_nothing_by_default(monkeypatch):
    assert exit_flush_requests(monkeypatch) == []


def test_no_config_manager_can_reach_a_real_documents_tree():
    from utils.config_manager import ConfigManager

    config_manager = ConfigManager("N.E.K.O")

    assert config_manager._get_standard_data_directory_candidates() == [
        real_root_isolation.isolated_app_data_root()
    ]
    assert config_manager._get_legacy_storage_candidates() == []
    assert config_manager._get_legacy_document_candidates() == []
    assert config_manager.get_legacy_app_root_candidates() == []


def test_logs_do_not_fall_back_to_the_real_documents_tree():
    from utils.logger_config import RobustLoggerConfig

    assert RobustLoggerConfig._get_documents_directory(None) == process_isolation.TEST_LOG_ROOT


def test_the_storage_root_env_guard_wraps_every_test(request):
    hook = request.config.pluginmanager.hook
    for name in ("pytest_runtest_setup", "pytest_runtest_teardown", "pytest_sessionstart"):
        functions = [impl.function for impl in getattr(hook, name).get_hookimpls()]
        assert getattr(storage_root_env_guard, name) in functions, name
