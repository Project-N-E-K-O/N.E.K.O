# -*- coding: utf-8 -*-
"""Guardrails for the autouse ``_reset_sys_path`` fixture in conftest.

``sys.path`` is process-global and product code prepends to it on purpose
(``_start_embedded_user_plugin_server`` puts ``<repo>/plugin`` at index 1). A unit
test that drives such a path leaves the entry behind for the whole session, and
``<repo>/plugin`` carries its own ``config`` package that then outranks the repo
root — see the fixture's docstring for the full chain.

The first two tests are a pair: each asserts it cannot see the *other* one's
marker before planting its own. Whichever pytest-randomly happens to schedule
second turns red the moment the fixture stops restoring, under any seed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


_MARKER_A = "<neko-sys-path-isolation-marker-a>"
_MARKER_B = "<neko-sys-path-isolation-marker-b>"
_MARKERS = (_MARKER_A, _MARKER_B)


def _leaked_markers() -> list[str]:
    return [entry for entry in sys.path if entry in _MARKERS]


@pytest.mark.unit
def test_sys_path_mutation_does_not_leak_forward_first():
    assert _leaked_markers() == [], (
        "另一条用例往 sys.path 里插的标记漏到这里了 —— conftest 的 "
        "_reset_sys_path 没在还原"
    )
    sys.path.insert(0, _MARKER_A)


@pytest.mark.unit
def test_sys_path_mutation_does_not_leak_forward_second():
    assert _leaked_markers() == [], (
        "另一条用例往 sys.path 里插的标记漏到这里了 —— conftest 的 "
        "_reset_sys_path 没在还原"
    )
    sys.path.insert(0, _MARKER_B)


@pytest.mark.unit
def test_repo_root_outranks_the_bundled_plugin_dir():
    """``<repo>/plugin`` must never sit ahead of the repo root.

    It ships ``plugin/config/``, so once it outranks the root the next
    ``importlib.reload(config)`` rebinds the root ``config`` package to it. This
    only catches a leak that happened *earlier* in the session, so it is a
    second line of defence behind the marker pair above, not a replacement.
    """
    repo_root = Path(__file__).resolve().parents[2]
    plugin_dir = repo_root / "plugin"

    plugin_at = root_at = -1
    for index, entry in enumerate(sys.path):
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except (OSError, ValueError):
            continue
        if plugin_at < 0 and resolved == plugin_dir:
            plugin_at = index
        if root_at < 0 and resolved == repo_root:
            root_at = index

    if plugin_at < 0:
        return
    assert root_at >= 0 and root_at < plugin_at, (
        f"{plugin_dir} 排在仓库根前面（plugin={plugin_at} root={root_at}）："
        "任何一次 importlib.reload(config) 都会把根 config 包重指到 plugin/config"
    )
