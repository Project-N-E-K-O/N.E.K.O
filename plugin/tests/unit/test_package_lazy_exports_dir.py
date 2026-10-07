from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = pytest.mark.plugin_unit

_PROBE = """
import sys
import plugin._types as types_pkg
import plugin.sdk as sdk_pkg

models = {"RunStatus", "PluginMeta", "PluginPushMessage", "HealthCheckResponse"}
missing_types = sorted(models - set(dir(types_pkg)))
missing_sdk = sorted({"plugin", "adapter"} - set(dir(sdk_pkg)))
listing = dir(types_pkg)
assert listing == sorted(listing)
# Listing names must not import them.
loaded = sorted(
    name for name in ("plugin._types.models", "plugin.sdk.plugin", "plugin.sdk.adapter")
    if name in sys.modules
)
print(repr((missing_types, missing_sdk, loaded)))
"""


def test_dir_lists_lazy_exports_without_importing_them():
    # A fresh interpreter: other tests may already have imported the lazy modules.
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=120, check=True,
    ).stdout.strip().splitlines()[-1]
    assert out == repr(([], [], []))


def test_server_import_leaves_execution_capabilities_unloaded(tmp_path):
    probe = """
import os, sys
from pathlib import Path
root = Path(sys.argv[1])
os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(root)
Path.home = classmethod(lambda cls: root / 'home')
import plugin.server.http_app
deferred = (
    'plugin.core.host', 'plugin.server.application.plugins.metadata_scanner',
    'plugin.neko_plugin_cli.core.build', 'plugin.sdk.shared.core.base',
    'plugin.sdk.shared.storage', 'plugin.sdk.plugin',
    'httpx', 'rich.console',
)
assert not any(name in sys.modules for name in deferred)
from plugin.server.application.plugins.lifecycle_service import create_plugin_host
assert callable(create_plugin_host)
assert "plugin.core.host" not in sys.modules
print('ok')
"""
    out = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path)], capture_output=True,
        text=True, timeout=120, check=True,
    ).stdout.strip().splitlines()[-1]
    assert out == "ok"


def test_lazy_sdk_and_cli_exports_keep_identity_under_concurrent_imports():
    probe = """
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
import plugin.sdk.shared.core as core
import plugin.neko_plugin_cli.public as public
from plugin.sdk.shared.core import router, config

targets = [(core, name) for name in core.__all__] + [(public, name) for name in public.__all__]
assert all(name in dir(package) for package, name in targets)
with ThreadPoolExecutor(max_workers=4) as pool:
    first = list(pool.map(lambda item: getattr(*item), targets))
assert first == [getattr(package, name) for package, name in targets]
assert core.PluginRouter is router.PluginRouter
assert core.PluginConfig is config.PluginConfig
assert public.build_plugin is import_module('plugin.neko_plugin_cli.core.build').build_plugin
print('ok')
"""
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True,
        text=True, timeout=120, check=True,
    ).stdout.strip().splitlines()[-1]
    assert out == "ok"


@pytest.mark.parametrize(
    ("package_name", "submodules"),
    [
        ("plugin.sdk.shared.core", (
            "_facade", "base", "base_runtime", "bus_context", "cards", "config",
            "context", "decorators", "events", "finish", "hook_executor", "hooks",
            "plugins", "result_contract", "router", "types",
        )),
        ("plugin.neko_plugin_cli.core", (
            "archive_utils", "build", "build_rules", "bundle_analysis", "dependencies",
            "inspect", "install", "metadata_probe", "models", "normalize",
            "plugin_source", "profile", "toml_utils",
        )),
        ("plugin.neko_plugin_cli.public", (
            "archive_utils", "models", "pack", "pack_rules", "plugin_source", "profile",
            "toml_utils", "unpack",
        )),
        ("plugin.server.infrastructure", ("auth", "error_handler", "exceptions")),
        ("plugin.config", ("service", "schema")),
    ],
)
def test_cold_facade_preserves_existing_submodule_attributes(
    tmp_path, package_name, submodules,
):
    # Each package starts in a fresh interpreter: eager imports used to expose
    # these modules, while imports made by other tests can conceal their loss.
    probe = """
import os, sys
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from pathlib import Path
root = Path(sys.argv[1])
os.environ['APPDATA'] = str(root / 'appdata')
os.environ['LOCALAPPDATA'] = str(root / 'localappdata')
os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(root / 'data')
os.environ['NEKO_STORAGE_ANCHOR_ROOT'] = str(root / 'anchor')
Path.home = classmethod(lambda cls: root / 'home')
package_name = sys.argv[2]
names = sys.argv[3:]
package = import_module(package_name)
assert not any(package_name + '.' + name in sys.modules for name in names)
assert set(names) <= set(dir(package))
assert not any(package_name + '.' + name in sys.modules for name in names)
with ThreadPoolExecutor(max_workers=8) as pool:
    loaded = list(pool.map(lambda name: getattr(package, name), names * 4))
assert all(value is import_module(package_name + '.' + name)
           for value, name in zip(loaded, names * 4))
scope = {}
exec('from ' + package_name + ' import *', scope)
assert set(scope) == set(package.__all__) | {'__builtins__'}
assert all(scope[name] is getattr(package, name) for name in package.__all__)
if package_name == 'plugin.sdk.shared.core':
    assert not any(name.startswith('plugin.neko_plugin_cli') for name in sys.modules)
try:
    getattr(package, 'not_an_existing_export')
except AttributeError:
    pass
else:
    raise AssertionError('unknown attributes must still raise AttributeError')
print('ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path), package_name, *submodules],
        capture_output=True, text=True, timeout=120, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "ok"


@pytest.mark.parametrize("access", ["attribute", "direct"])
@pytest.mark.parametrize(
    ("package_name", "first_helper", "second_helper", "parent_module", "child_module"),
    [
        (
            "plugin.neko_plugin_cli.core", "archive_utils", "metadata_probe",
            "plugin._types", "plugin._types.version",
        ),
        (
            "plugin.sdk.shared.core", "router", "config",
            "plugin.sdk.shared.models", "plugin.sdk.shared.models.exceptions",
        ),
    ],
)
def test_concurrent_facade_helpers_do_not_invert_parent_import_locks(
    tmp_path, access, package_name, first_helper, second_helper, parent_module, child_module,
):
    probe = """
import importlib
import importlib._bootstrap as bootstrap
import os, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
root = Path(sys.argv[1])
os.environ['APPDATA'] = str(root / 'appdata')
os.environ['LOCALAPPDATA'] = str(root / 'localappdata')
os.environ['NEKO_STORAGE_SELECTED_ROOT'] = str(root / 'data')
os.environ['NEKO_STORAGE_ANCHOR_ROOT'] = str(root / 'anchor')
Path.home = classmethod(lambda cls: root / 'home')
package_name, first_helper, second_helper, parent_module, child_module, access = sys.argv[2:]
package = importlib.import_module(package_name)
parent_held = threading.Event()
child_held = threading.Event()
worker = threading.local()
original_acquire = bootstrap._ModuleLock.acquire

def acquire(lock):
    value = original_acquire(lock)
    if lock.name == child_module and getattr(worker, 'kind', '') == 'second':
        child_held.set()
    if lock.name == parent_module and getattr(worker, 'kind', '') == 'first':
        parent_held.set()
        assert child_held.wait(5), 'second helper never acquired the child module'
    return value

def load(kind):
    worker.kind = kind
    if kind == 'second' and parent_module not in sys.modules:
        assert parent_held.wait(5), 'first helper never acquired the shared parent'
    name = first_helper if kind == 'first' else second_helper
    if access == 'direct':
        return importlib.import_module(package.__name__ + '.' + name)
    return getattr(package, name)

# Reproduce the valid schedule that previously raised importlib._DeadlockError:
# one helper owns the parent; another owns its child and waits for the parent;
# then the parent's eager re-export waits for that child. The fixed lightweight
# initialization boundary completes the parent before either helper.
bootstrap._ModuleLock.acquire = acquire
try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(load, 'first')
        second = pool.submit(load, 'second')
        assert first.result(10) is importlib.import_module(package.__name__ + '.' + first_helper)
        assert second.result(10) is importlib.import_module(package.__name__ + '.' + second_helper)
finally:
    child_held.set()
    bootstrap._ModuleLock.acquire = original_acquire
print('ok')
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path), package_name,
         first_helper, second_helper, parent_module, child_module, access],
        capture_output=True, text=True, timeout=60, check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "ok"
