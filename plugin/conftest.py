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

import pytest as _pytest

_REPO_ROOT = _Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = _importlib_util.spec_from_file_location(name, _REPO_ROOT / relative)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_GUARD = _load("_neko_clock_guard", "tests/clock_guard.py")
_FAKE_CLOCK = _load("_neko_fake_clock", "tests/fake_clock.py")

# pytest 按名字在 conftest 命名空间里发现 hook；这不是死代码，删掉守卫就失效。
pytest_runtest_call = _GUARD.pytest_runtest_call


@_pytest.fixture
def patch_module_clock():
    """``tests.fake_clock.patch_module_clock``，以 fixture 形式提供。

    这几棵 sibling 树跑起来时仓库根不一定在 sys.path 上（本文件刻意不去钉它，
    见上面的说明），直接 ``from tests.fake_clock import ...`` 在别的 checkout 里
    会解析到另一份副本。用 fixture 交出去就没有这层依赖。
    """
    return _FAKE_CLOCK.patch_module_clock
