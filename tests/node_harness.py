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
script built at test time.  Two ways of handing that script to node have both
bitten this repo, and both failures look like anything but what they are:

Command-line length
    ``node -e <script>`` puts the whole script on the command line.  Past 32767
    characters Windows' ``CreateProcess`` refuses it and ``subprocess`` raises
    ``WinError 206`` before node starts, so not one assertion runs.  A suite
    crossed that line at 34067 characters and stayed red unnoticed.

Locale encoding
    ``subprocess.run(..., text=True)`` without an explicit ``encoding`` encodes
    stdin and decodes stdout with ``locale.getpreferredencoding()``.  On a
    machine with the Windows UTF-8 option enabled that is cp65001 and CJK in a
    harness script sails through; on a stock English Windows (every GitHub
    runner) it is cp1252 and the same script dies with ``UnicodeEncodeError``.
    Five tests passed locally and failed in CI on exactly this.

Both runners here take the script off the command line and pin UTF-8 in both
directions.  Node lookup and the node-missing policy (skip vs. hard failure)
stay with each caller, since the suites deliberately differ there.
"""

import os
import subprocess
import tempfile


def _utf8(kwargs: dict) -> dict:
    """Force UTF-8 for stdin/stdout so the host locale cannot decide."""
    merged = dict(kwargs)
    merged.setdefault("text", True)
    merged["encoding"] = "utf-8"
    return merged


def run_node_script(node_path: str, script: str, **kwargs) -> subprocess.CompletedProcess[str]:
    """Run ``script`` from a temp file under ``node_path``.

    Use this when the script is large or grows with the behaviour it simulates.
    Extra keyword arguments go straight to ``subprocess.run``.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        return subprocess.run([node_path, script_path], **_utf8(kwargs))
    finally:
        os.unlink(script_path)


def run_node_stdin(node_path: str, script: str, **kwargs) -> subprocess.CompletedProcess[str]:
    """Pipe ``script`` into ``node -`` over stdin.

    Equivalent to ``run_node_script`` for callers already written against the
    stdin form; stdin has no length ceiling, so only the encoding pin matters
    here. Extra keyword arguments go straight to ``subprocess.run``.
    """
    return subprocess.run([node_path, "-"], input=script, **_utf8(kwargs))
