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
"""Shared launcher for the generated node simulation harnesses.

Several static-contract suites drive real frontend modules through a node
script built at test time.  Those scripts grow with the behaviour they
simulate, and passing one via ``node -e`` puts the whole thing on the command
line: past 32767 characters Windows' ``CreateProcess`` refuses it and
``subprocess`` raises ``WinError 206`` before node ever starts, so not one
assertion runs and the failure reads as unrelated to the code under test.
One suite crossed that line at 34067 characters and stayed red unnoticed
because ``tests/unit`` does not run in CI.

Writing the script to a temp file removes the ceiling.  Node lookup and the
node-missing policy (skip vs. hard failure) stay with each caller, since the
suites deliberately differ there.
"""

import os
import subprocess
import tempfile


def run_node_script(node_path: str, script: str, **kwargs) -> subprocess.CompletedProcess[str]:
    """Run ``script`` from a temp file under ``node_path``.

    Extra keyword arguments go straight to ``subprocess.run``.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        return subprocess.run([node_path, script_path], **kwargs)
    finally:
        os.unlink(script_path)
