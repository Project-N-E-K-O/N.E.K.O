"""Compatibility of the lazy (PEP 562) exports in ``plugin.server.application.plugins``."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.plugin_unit

_EXPECTED_EXPORTS = {
    "PluginQueryService": "query_service",
    "PluginLifecycleService": "lifecycle_service",
    "PluginRegistryService": "registry_service",
    "PluginUiQueryService": "ui_query_service",
    "PluginRouterQueryService": "router_query_service",
    "PluginDispatchService": "dispatch_service",
    "PluginHotReloadService": "hot_reload_service",
}

_STORAGE_PRELUDE = r'''
import os, sys
from pathlib import Path
root = Path(sys.argv[1])
os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(root / 'data')
os.environ['NEKO_STORAGE_ANCHOR_ROOT'] = str(root / 'anchor')
Path.home = classmethod(lambda cls: root / 'home')
'''


def _run_probe(probe: str, tmp_path) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _STORAGE_PRELUDE + probe, str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_all_lists_every_service_and_dir_matches():
    import plugin.server.application.plugins as package

    assert set(package.__all__) == set(_EXPECTED_EXPORTS)
    assert len(package.__all__) == len(set(package.__all__))
    assert set(package.__all__) <= set(dir(package))


@pytest.mark.parametrize(("name", "submodule"), sorted(_EXPECTED_EXPORTS.items()))
def test_each_export_resolves_by_attribute_and_from_import(name, submodule):
    import importlib

    import plugin.server.application.plugins as package

    source = importlib.import_module(f"plugin.server.application.plugins.{submodule}")
    assert getattr(package, name) is getattr(source, name)
    namespace: dict[str, object] = {}
    exec(f"from plugin.server.application.plugins import {name}", namespace)
    assert namespace[name] is getattr(source, name)


def test_unknown_name_raises_attribute_error():
    import plugin.server.application.plugins as package

    with pytest.raises(AttributeError):
        package.DefinitelyNotAnExport  # noqa: B018


def test_star_import_exposes_every_export(tmp_path):
    _run_probe(r'''
namespace = {}
exec('from plugin.server.application.plugins import *', namespace)
from plugin.server.application.plugins import lifecycle_service
assert namespace['PluginLifecycleService'] is lifecycle_service.PluginLifecycleService
expected = %r
missing = sorted(set(expected) - set(namespace))
assert not missing, missing
print('ok')
''' % sorted(_EXPECTED_EXPORTS), tmp_path)


def test_metadata_scanner_entry_does_not_import_web_stack_or_services(tmp_path):
    # Mirrors the metadata scan worker bootstrap (metadata_scanner._WORKER_BOOTSTRAP).
    _run_probe(r'''
from plugin.server.application.plugins.metadata_scanner import _worker_main
assert callable(_worker_main)
heavy = sorted(
    name for name in sys.modules
    if name == 'fastapi' or name.startswith('fastapi.')
    or name == 'starlette' or name.startswith('starlette.')
)
assert not heavy, heavy
services = %r
loaded = sorted(
    name for name in services
    if 'plugin.server.application.plugins.' + name in sys.modules
)
assert not loaded, loaded
print('ok')
''' % sorted(_EXPECTED_EXPORTS.values()), tmp_path)


def test_package_import_alone_loads_no_service_module(tmp_path):
    _run_probe(r'''
import plugin.server.application.plugins as package
loaded = sorted(
    name for name in sys.modules
    if name.startswith('plugin.server.application.plugins.')
)
assert not loaded, loaded
assert 'fastapi' not in sys.modules
print('ok')
''', tmp_path)
