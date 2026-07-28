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
"""Register the clock guard for every test tree under ``plugin/``.

conftest hooks only reach descendants of the directory they live in, so the one
in ``plugin/tests/conftest.py`` leaves the six sibling roots under
``plugin/plugins/<name>/tests/`` unguarded when run directly. This file sits
above all of them.

Deliberately does **not** touch ``sys.path``. Pinning the repo root here flips
module resolution for those trees (the venv's editable install points at the
main checkout, so in a worktree they currently import the main copy), which
changes what they test and was observed to alter results. Registering a hook
should not move code out from under the tests it guards, so the guard is loaded
straight off its file path instead.
"""

import importlib.util as _importlib_util
from pathlib import Path as _Path

_GUARD_PATH = _Path(__file__).resolve().parents[1] / "tests" / "clock_guard.py"
_GUARD_SPEC = _importlib_util.spec_from_file_location("_neko_clock_guard", _GUARD_PATH)
_GUARD = _importlib_util.module_from_spec(_GUARD_SPEC)
_GUARD_SPEC.loader.exec_module(_GUARD)

# pytest 按名字在 conftest 命名空间里发现 hook；这不是死代码，删掉守卫就失效。
pytest_runtest_call = _GUARD.pytest_runtest_call
