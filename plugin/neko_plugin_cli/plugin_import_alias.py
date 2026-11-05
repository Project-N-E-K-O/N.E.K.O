"""Pytest plugin: import ``plugin.plugins.<id>`` from a plugin kept outside the checkout.

Market CI copies the plugin source to ``plugin/plugins/<id>`` of a N.E.K.O
checkout before it runs the plugin tests. ``check --release`` loads this
module with ``-p`` for a plugin kept in its own directory, so its tests
import it under the same name without anything being written to the checkout.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

PLUGIN_ID_ENV = "NEKO_PLUGIN_TEST_ID"
PLUGIN_SOURCE_ENV = "NEKO_PLUGIN_TEST_SOURCE"


class PluginSourceFinder(importlib.abc.MetaPathFinder):
    """Resolve one package name to a source directory; submodules follow its ``__path__``."""

    def __init__(self, module_name: str, source: Path) -> None:
        self._module_name = module_name
        self._source = source

    def find_spec(self, fullname, path=None, target=None) -> importlib.machinery.ModuleSpec | None:
        if fullname != self._module_name:
            return None
        return importlib.util.spec_from_file_location(
            fullname,
            self._source / "__init__.py",
            submodule_search_locations=[str(self._source)],
        )


def pytest_load_initial_conftests() -> None:
    # pytest loads conftest.py files in its own trylast implementation of
    # this hook, so the finder is in place before they import the plugin.
    sys.meta_path.insert(
        0,
        PluginSourceFinder(
            f"plugin.plugins.{os.environ[PLUGIN_ID_ENV]}",
            Path(os.environ[PLUGIN_SOURCE_ENV]),
        ),
    )
