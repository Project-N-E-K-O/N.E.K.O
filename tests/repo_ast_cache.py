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
"""Process-wide cache for parsing the repository's own Python sources.

A dozen structural guards in tests/unit answer their question by walking every
non-test ``.py`` file in the repo and running ``ast.parse`` on it. Parsing the
tree once costs ~5s on a CI runner, and each guard was paying that in full:
``test_root_state_write_lock.py`` alone did it five times, for ~35s of the unit
job's wall clock spent re-parsing bytes that had not changed.

Nothing in a test run writes to the files being scanned, so one parse per path
is enough for the whole session.

The returned ``ast.Module`` is SHARED. Treat it as read-only: walk it, read
attributes off it, collect from it. A guard that needs to *annotate* nodes
(``test_workshop_content_gate.py`` propagates parameter facts onto the tree)
must keep parsing its own private copy -- mutating a cached tree would leak
into every other guard that later reads the same file.

``utf-8-sig`` rather than ``utf-8``: a handful of files in this repo carry a
BOM, and ``ast.parse`` rejects U+FEFF as a non-printable character.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def _parse(path_str: str) -> ast.Module:
    return ast.parse(Path(path_str).read_text(encoding="utf-8-sig"))


def parse_source_file(path: Path | str) -> ast.Module:
    """Return the parsed tree for ``path``, parsing it at most once per process.

    Read-only -- see the module docstring before you reach for a node's
    ``setattr``.
    """
    return _parse(str(Path(path).resolve()))


@lru_cache(maxsize=None)
def _read(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8-sig")


def read_source_file(path: Path | str) -> str:
    """Return ``path``'s text, reading it at most once per process."""
    return _read(str(Path(path).resolve()))
