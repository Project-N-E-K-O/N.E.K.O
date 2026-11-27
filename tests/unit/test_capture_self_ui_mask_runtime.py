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
"""Screen frames sent to the model hide N.E.K.O's own chat UI (#3474).

The behaviour is exercised by ``tests/frontend/capture_self_ui_mask.test.cjs``
(node:test, synthetic frames only). CI only runs an explicit list of node test
files, so this wrapper runs that file under pytest.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.node_harness import run_node_script

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NODE_TEST = PROJECT_ROOT / "tests" / "frontend" / "capture_self_ui_mask.test.cjs"


def _node() -> str:
    found = shutil.which("node")
    if not found:
        pytest.skip("node not available")
    return found


def test_capture_self_ui_mask_node_suite():
    script = f"require({json.dumps(str(NODE_TEST))});\n"
    proc = run_node_script(_node(), script, capture_output=True, timeout=120)
    report = proc.stdout + proc.stderr
    assert proc.returncode == 0, report[-4000:]
    assert "# fail 0" in report, report[-4000:]
    assert "# pass 0" not in report, report[-4000:]
