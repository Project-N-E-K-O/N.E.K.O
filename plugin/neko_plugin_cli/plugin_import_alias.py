"""Pytest plugin: import ``plugin.plugins.<id>`` from a plugin kept outside the checkout.

Market CI copies the plugin source to ``plugin/plugins/<id>`` of a N.E.K.O
checkout before it runs the plugin tests. ``check --release`` loads this
module with ``-p`` for a plugin kept in its own directory, so its tests
import it under the same name without anything being written to the checkout.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from plugin.core.source_imports import ensure_plugins_namespace, install_source_imports
from plugin.logging_config import get_logger

PLUGIN_ID_ENV = "NEKO_PLUGIN_TEST_ID"
PLUGIN_SOURCE_ENV = "NEKO_PLUGIN_TEST_SOURCE"


def pytest_load_initial_conftests() -> None:
    # pytest loads conftest.py files in its own trylast implementation of
    # this hook, so the finder is in place before they import the plugin.
    source = Path(os.environ[PLUGIN_SOURCE_ENV])
    vendor = source / "vendor"
    if vendor.is_dir():
        sys.path.insert(0, str(vendor))
    ensure_plugins_namespace(source.parent, get_logger("plugin-tests"))
    install_source_imports(f"plugins.{os.environ[PLUGIN_ID_ENV]}", source)
