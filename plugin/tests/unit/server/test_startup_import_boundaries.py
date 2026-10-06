from __future__ import annotations

import subprocess
import sys
import threading

import pytest

from plugin.core import packaged_metadata as metadata_contract

pytestmark = pytest.mark.plugin_unit


def test_metadata_scan_budget_is_read_once_across_import_orders():
    probe = r'''
import os
original = os.getenv
reads = []
def changing_override(name, default=None):
    if name == 'NEKO_PLUGIN_METADATA_SCAN_TIMEOUT':
        reads.append(name)
        return '7.5' if len(reads) == 1 else '99'
    return original(name, default)
os.getenv = changing_override
from plugin.server.application.plugins import metadata_scanner, lifecycle_service, hot_reload_service
assert metadata_scanner._DEFAULT_SCAN_TIMEOUT_SECONDS == 7.5
assert lifecycle_service._DEFAULT_METADATA_SCAN_TIMEOUT == 7.5
assert hot_reload_service._DEFAULT_SCAN_TIMEOUT_SECONDS == 7.5
assert reads == ['NEKO_PLUGIN_METADATA_SCAN_TIMEOUT'], reads
print('ok')
'''
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True,
        text=True, timeout=120, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "ok"


@pytest.mark.parametrize(
    ("process_arch", "native_arch", "expected"),
    [("AMD64", "", "AMD64"), ("x86", "AMD64", "AMD64"),
     ("x86", "ARM64", "ARM64"), ("ARM64", "", "ARM64"),
     ("", "", ""), ("unknown", "", ""), ("x86", "unknown", "")],
)
def test_windows_metadata_architecture_preserves_native_fingerprint_without_shell(
    monkeypatch, process_arch, native_arch, expected,
):
    monkeypatch.setattr(metadata_contract.sys, "platform", "win32")
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", process_arch)
    monkeypatch.setenv("PROCESSOR_ARCHITEW6432", native_arch)

    def unexpected_machine_lookup():
        pytest.fail("Windows fingerprint lookup must not query the OS version")

    monkeypatch.setattr(metadata_contract.platform, "machine", unexpected_machine_lookup)
    assert metadata_contract.build_environment()["arch"] == expected


def test_non_windows_metadata_keeps_platform_machine(monkeypatch):
    monkeypatch.setattr(metadata_contract.sys, "platform", "linux")
    monkeypatch.setattr(metadata_contract.platform, "machine", lambda: "aarch64")
    assert metadata_contract.build_environment()["arch"] == "aarch64"


def test_configuration_snapshot_loads_web_stack_only_for_http_errors(tmp_path):
    probe = r'''
import os, sys
from pathlib import Path
root = Path(sys.argv[1])
os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(root / 'data')
os.environ['NEKO_STORAGE_ANCHOR_ROOT'] = str(root / 'anchor')
Path.home = classmethod(lambda cls: root / 'home')
manifest = root / 'demo' / 'plugin.toml'
manifest.parent.mkdir()
manifest.write_text('[plugin]\nid="demo"\nname="Demo"\nentry="demo:Plugin"\n', encoding='utf-8')
from plugin.server.infrastructure.config_resolver import resolve_plugin_config_from_path
assert 'fastapi' not in sys.modules
snapshot = resolve_plugin_config_from_path('demo', config_path=manifest)
assert snapshot['effective_config']['plugin']['id'] == 'demo'
assert snapshot['config_fingerprint']
assert 'fastapi' not in sys.modules
try:
    resolve_plugin_config_from_path('missing', config_path=root / 'missing.toml')
except Exception as error:
    from fastapi import HTTPException
    assert isinstance(error, HTTPException)
    assert error.status_code == 500
else:
    raise AssertionError('missing configuration must retain its HTTP error')
print('ok')
'''
    result = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path)], capture_output=True,
        text=True, timeout=120, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "ok"


def test_first_cli_calls_import_on_worker_and_publish_one_service(tmp_path):
    probe = r'''
import asyncio, os, sys, threading
from pathlib import Path
root = Path(sys.argv[1])
os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(root / 'data')
os.environ['NEKO_STORAGE_ANCHOR_ROOT'] = str(root / 'anchor')
Path.home = classmethod(lambda cls: root / 'home')
import plugin.server.http_app
import plugin.server.routes.plugin_cli as cli
import plugin.server.routes.market_bridge as market
assert 'plugin.server.application.plugin_cli.service' not in sys.modules
assert 'plugin.neko_plugin_cli.core.install' not in sys.modules
assert 'plugin.neko_plugin_cli.core.build_rules' not in sys.modules
assert cli.get_plugin_cli_service is market.get_plugin_cli_service
loop_thread = threading.get_ident()
imports = []
class ImportTracker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'plugin.server.application.plugin_cli.service':
            imports.append(threading.get_ident())
        return None
sys.meta_path.insert(0, ImportTracker())
async def main():
    services = await asyncio.gather(*(cli.get_plugin_cli_service() for _ in range(12)))
    results = await asyncio.gather(*(service.list_local_plugins() for service in services))
    assert all(result == results[0] for result in results)
    first = services[0]
    assert all(value is first for value in await asyncio.gather(*(cli.get_plugin_cli_service() for _ in range(12))))
    assert imports and all(thread != loop_thread for thread in imports)
    from plugin.server.application.plugin_cli.service import PluginCliService
    assert isinstance(first, PluginCliService)
    assert all(service is first for service in services)
    assert isinstance(await market.get_plugin_cli_service(), PluginCliService)
    assert first is await market.get_plugin_cli_service()
asyncio.run(main())
print('ok')
'''
    result = subprocess.run([sys.executable, "-c", probe, str(tmp_path)],
                            capture_output=True, text=True, timeout=120, check=True)
    assert result.stdout.strip().splitlines()[-1] == "ok"


async def test_layout_root_resolution_leaves_event_loop(
    monkeypatch, tmp_path,
):
    from plugin.server.application.plugins import layout_migration as migration

    owner = threading.get_ident()
    lookups = []

    def root(*, state_root=None):
        lookups.append(threading.get_ident())
        return tmp_path

    def migrate(**roots):
        assert threading.get_ident() != owner
        assert roots["state_root"] == tmp_path
        return migration.LayoutMigrationResult()

    monkeypatch.setattr(migration, "get_plugin_state_root", root)
    monkeypatch.setattr(migration, "get_user_plugin_exec_root", root)
    monkeypatch.setattr(migration, "get_user_package_profiles_root", root)
    monkeypatch.setattr(migration, "get_builtin_plugin_config_root", root)
    monkeypatch.setattr(migration, "_migrate_legacy_plugin_layout_sync", migrate)
    await migration.migrate_legacy_plugin_layout()
    assert lookups and all(thread != owner for thread in lookups)


def test_install_manager_root_snapshot_is_fresh_per_factory_call(monkeypatch, tmp_path):
    from plugin import settings
    from plugin.server.application.install_source import build_install_source_manager

    roots = []

    def root():
        path = tmp_path / str(len(roots)) / "plugins"
        roots.append(path)
        return path

    monkeypatch.delenv("PLUGIN_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("NEKO_PLUGIN_INSTALL_LOCK_PATH", raising=False)
    monkeypatch.setattr(settings, "get_plugins_directory", root)
    first = build_install_source_manager()
    second = build_install_source_manager()
    assert len(roots) == 2
    assert first.lock_path != second.lock_path


def test_general_env_parser_does_not_evaluate_scan_settings():
    probe = r"""
import os,sys,importlib.util
from pathlib import Path
os.environ['NEKO_PLUGIN_METADATA_SCAN_TIMEOUT']='invalid'
logging_was_loaded = 'plugin.logging_config' in sys.modules
original = os.getenv
reads = []
def record(name, default=None):
    if name == 'NEKO_PLUGIN_METADATA_SCAN_TIMEOUT':
        reads.append(name)
    return original(name, default)
os.getenv = record
spec = importlib.util.spec_from_file_location('isolated_env_parser', Path('plugin/server/application/plugins/_env_budgets.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
env_seconds = module.env_seconds
assert env_seconds('NEKO_BENCH_UNSET_BUDGET',5)==5
assert reads == [], reads
assert 'plugin.server.application.plugins._metadata_scan_settings' not in sys.modules
assert ('plugin.logging_config' in sys.modules) == logging_was_loaded
print('ok')
"""
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                            text=True, timeout=120, check=True)
    assert result.stdout.strip().splitlines()[-1] == "ok"
