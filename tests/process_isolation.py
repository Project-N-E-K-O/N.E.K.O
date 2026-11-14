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
"""What a pytest process must do before it imports any product module.

Shared by ``tests/conftest.py`` and ``plugin/tests/conftest.py`` (two
separate rootdirs, so neither sees the other's conftest). Each calls
:func:`isolate_test_process` first thing, right after putting the repo root on
``sys.path``. Order matters for every step:

- **Telemetry opt-out.** ``utils.token_tracker`` reads ``NEKO_DO_NOT_TRACK``
  once, at import; any ``TokenTracker`` built afterwards registers an atexit
  flush that POSTs usage to the production telemetry server when the process
  exits. So the variable has to be in the environment before anything imports
  the tracker. Subprocesses the tests spawn inherit it too. Tests about
  telemetry itself patch ``reporting._DO_NOT_TRACK`` and point the URL at a
  local collector (see ``tests/telemetry_capture.py``).
- **Inherited storage-root variables.** ``NEKO_STORAGE_SELECTED_ROOT`` and its
  siblings win over every directory candidate ConfigManager has. A shell
  started from the app can hand them to pytest; if they are still set when a
  module-level ``get_config_manager()`` runs during import or collection, the
  migration chain runs against the real root. The per-test guard in
  ``tests/storage_root_env_guard.py`` starts at session start, which is after
  conftest's own imports, so they are dropped here as well.
- **Log directory.** Loggers are built at import time and default to the real
  ``Documents/N.E.K.O/logs`` (and prune old files there).
- **ConfigManager root resolution** (``tests/real_root_isolation.py``):
  module-level singletons default to ``migrate=True``.

Measured before this existed, with the real directories swapped for a fake
tree: collecting ``plugin/tests`` alone built the ConfigManager singleton and
wrote ``config/characters.json``, ``card_faces/`` and ``memory/`` into the
"real" root and memory-server logs into ``Documents/N.E.K.O/logs``.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TELEMETRY_OPT_OUT_ENV = "NEKO_DO_NOT_TRACK"

_REPO_ROOT = Path(__file__).resolve().parents[1]

# None until isolate_test_process() runs; then the product modules that were
# already imported at that moment (must be empty for the isolation to count).
PRODUCT_MODULES_BEFORE_ISOLATION: tuple[str, ...] | None = None
# Storage-root variables inherited by the process and dropped before import.
STORAGE_ROOT_ENV_CLEARED_BEFORE_IMPORT: dict[str, str] = {}
TEST_LOG_ROOT: Path | None = None


def _is_product_file(path: Path) -> bool:
    if not path.is_relative_to(_REPO_ROOT):
        return False
    parts = path.relative_to(_REPO_ROOT).parts
    # Test trees (tests/, plugin/tests/, plugin/plugins/<name>/tests/) and the
    # conftest modules being executed are not product code; neither is a venv
    # that lives inside the checkout.
    if "tests" in parts[:-1] or parts[-1] == "conftest.py":
        return False
    return not any(part.startswith(".") for part in parts)


def product_modules_loaded() -> tuple[str, ...]:
    """Names of imported modules whose source lives in this checkout outside ``tests/``."""
    loaded = []
    for name, module in list(sys.modules.items()):
        file = getattr(module, "__file__", None)
        if not file:
            continue
        try:
            path = Path(file).resolve()
        except (OSError, ValueError):
            continue
        if _is_product_file(path):
            loaded.append(name)
    return tuple(sorted(loaded))


def isolate_test_process() -> None:
    """Install every process-wide isolation step. Idempotent."""
    global PRODUCT_MODULES_BEFORE_ISOLATION, TEST_LOG_ROOT
    if PRODUCT_MODULES_BEFORE_ISOLATION is not None:
        return
    PRODUCT_MODULES_BEFORE_ISOLATION = product_modules_loaded()

    os.environ[TELEMETRY_OPT_OUT_ENV] = "1"

    from tests import storage_root_env_guard

    STORAGE_ROOT_ENV_CLEARED_BEFORE_IMPORT.update(
        storage_root_env_guard.clear_inherited_storage_root_env()
    )

    # Override only the Documents fallback of RobustLoggerConfig._get_log_directory.
    # Its env-var branch (NEKO_STORAGE_SELECTED_ROOT) stays live so tests that
    # monkeypatch that variable still see the directory they expect.
    TEST_LOG_ROOT = Path(tempfile.gettempdir()) / f"neko_test_logs_{os.getpid()}"
    TEST_LOG_ROOT.mkdir(parents=True, exist_ok=True)
    from utils import logger_config

    logger_config.RobustLoggerConfig._get_documents_directory = (
        lambda self, _root=TEST_LOG_ROOT: _root
    )

    from utils.config_manager import ConfigManager

    from tests import real_root_isolation

    real_root_isolation.install(ConfigManager)
