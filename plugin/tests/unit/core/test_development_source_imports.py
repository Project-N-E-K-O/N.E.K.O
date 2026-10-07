from __future__ import annotations

import json
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("legacy_first", [False, True])
@pytest.mark.parametrize("native_package", [False, True], ids=["module", "package"])
@pytest.mark.parametrize("scanner", [False, True], ids=["runtime", "metadata"])
def test_development_imports_load_native_extensions(tmp_path: Path, legacy_first: bool, native_package: bool, scanner: bool):
    # Reuse the interpreter's real extension, preserving its PyInit_* name.
    native_spec = importlib.util.find_spec("_decimal")
    if native_spec is None or not isinstance(native_spec.loader, importlib.machinery.ExtensionFileLoader):
        pytest.skip("this interpreter does not ship _decimal as a native extension")
    directory = tmp_path / "native_probe"
    directory.mkdir()
    manifest = directory / "plugin.toml"
    manifest.write_text("", encoding="utf-8")
    binary = Path(native_spec.origin)
    if native_package:
        destination = directory / "_decimal" / ("__init__" + binary.name.removeprefix("_decimal"))
        destination.parent.mkdir()
    else:
        destination = directory / binary.name
    shutil.copy2(binary, destination)
    first, second = ("plugin.plugins", "plugins") if legacy_first else ("plugins", "plugin.plugins")
    (directory / "__init__.py").write_text(
        f"from {first}.native_probe import _decimal as first\n"
        f"from {second}.native_probe import _decimal as second\n"
        "assert first is second\n"
        "assert first.__spec__.name == 'plugins.native_probe._decimal'\n"
        "VALUE = str(first.Decimal('1.25') + first.Decimal('2.50'))\n"
        "from plugin.sdk.plugin.decorators import plugin_entry\n"
        "class Probe:\n"
        "    @plugin_entry(id='probe', name=VALUE)\n"
        "    def probe(self): pass\n", encoding="utf-8",
    )
    if scanner:
        from plugin.server.application.plugins.metadata_scanner import scan_plugin_metadata_isolated
        result = scan_plugin_metadata_isolated(
            plugin_id="native_probe", module_path="plugins.native_probe", class_name="Probe",
            config_path=manifest, conf={}, pdata={}, source_only=True,
        )
        assert any(entry.get("name") == "3.75" for entry in result.entries_preview)
    else:
        code = """
import sys
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.logging_config import get_logger
for mode in (False, True):
    root = _import_plugin_module('plugins.native_probe', Path(sys.argv[1]), get_logger('probe'), source_only=mode)
    assert root.VALUE == '3.75'
    assert Path(root.first.__file__).resolve() == Path(sys.argv[2]).resolve()
"""
        process = subprocess.run([sys.executable, "-c", code, str(manifest), str(destination)], capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr


def _stale_source(path: Path, old: str, new: str) -> None:
    assert len(old) == len(new)
    path.write_text(old, encoding="utf-8")
    os.utime(path, (1700000000.1, 1700000000.1))
    py_compile.compile(str(path), doraise=True)
    path.write_text(new, encoding="utf-8")
    os.utime(path, (1700000000.8, 1700000000.8))


@pytest.mark.parametrize("native_package", [False, True])
def test_native_fallback_keeps_python_source_priority(tmp_path: Path, native_package: bool):
    from plugin.core.source_imports import PluginSourceFinder, SourceOnlyLoader
    directory = tmp_path / "probe"
    directory.mkdir()
    candidate = directory / "native"
    if native_package:
        candidate.mkdir()
        source = candidate / "__init__.py"
    else:
        source = candidate.with_suffix(".py")
    _stale_source(source, 'VALUE="old"\n', 'VALUE="new"\n')
    binary = source.with_suffix(importlib.machinery.EXTENSION_SUFFIXES[0])
    binary.write_bytes(b"invalid native module must not override Python source")
    finder = PluginSourceFinder("plugins.probe", directory)
    spec = finder.find_spec("plugins.probe.native")
    assert isinstance(spec.loader, SourceOnlyLoader)
    namespace = {}
    exec(spec.loader.get_code(spec.name), namespace)
    assert namespace["VALUE"] == "new"


def test_native_fallback_does_not_load_deleted_source_bytecode(tmp_path: Path):
    from plugin.core.source_imports import PluginSourceFinder
    source = tmp_path / "deleted.py"
    source.write_text("VALUE = 'stale'\n", encoding="utf-8")
    py_compile.compile(str(source), cfile=str(source.with_suffix(".pyc")), doraise=True)
    source.unlink()
    # Ordinary PathFinder could load this legacy bytecode file.
    spec = importlib.machinery.PathFinder.find_spec("plugins.probe.deleted", [str(tmp_path)])
    assert isinstance(spec.loader, importlib.machinery.SourcelessFileLoader)
    with pytest.raises(ModuleNotFoundError) as exc:
        PluginSourceFinder("plugins.probe", tmp_path).find_spec("plugins.probe.deleted")
    assert exc.value.name == "plugins.probe.deleted"


@pytest.mark.parametrize("suffix", importlib.machinery.EXTENSION_SUFFIXES)
def test_native_fallback_uses_registered_directory(tmp_path: Path, suffix: str):
    from plugin.core.source_imports import PluginSourceFinder
    directory = tmp_path / "probe"
    directory.mkdir()
    binary = directory / ("native" + suffix)
    binary.write_bytes(b"native fixture: only resolving, not loading")
    other = tmp_path / "other"
    other.mkdir()
    (other / binary.name).write_bytes(b"must not select this directory")
    spec = PluginSourceFinder("plugins.probe", directory).find_spec("plugins.probe.native", [str(other)])
    assert isinstance(spec.loader, importlib.machinery.ExtensionFileLoader)
    assert Path(spec.origin) == binary


def test_native_fallback_rejects_extension_symlink_outside_source(tmp_path: Path):
    from plugin.core.source_imports import PluginSourceFinder
    directory = tmp_path / "probe"
    directory.mkdir()
    name = "native" + importlib.machinery.EXTENSION_SUFFIXES[0]
    outside = tmp_path / name
    outside.write_bytes(b"outside source")
    try:
        (directory / name).symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable in this test environment")
    with pytest.raises(ImportError, match="escapes its registered directory"):
        PluginSourceFinder("plugins.probe", directory).find_spec("plugins.probe.native")


def test_development_failed_native_import_can_retry_from_source(tmp_path: Path):
    directory = tmp_path / "native_retry"
    directory.mkdir()
    manifest = directory / "plugin.toml"
    manifest.write_text("", encoding="utf-8")
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / ("native" + importlib.machinery.EXTENSION_SUFFIXES[0])).write_bytes(b"invalid binary")
    code = """
import importlib, sys
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.logging_config import get_logger
manifest = Path(sys.argv[1])
_import_plugin_module('plugins.native_retry', manifest, get_logger('probe'), source_only=True)
names = ['plugin.plugins.native_retry.native', 'plugins.native_retry.native']
try:
    importlib.import_module(names[0])
except ImportError as exc:
    assert not isinstance(exc, ModuleNotFoundError), 'native loader must attempt the binary'
else:
    raise AssertionError('invalid native binary must fail')
assert all(name not in sys.modules for name in names)
(manifest.parent / 'native.py').write_text('VALUE = 42\\n', encoding='utf-8')
left = importlib.import_module(names[0])
right = importlib.import_module(names[1])
assert left is right and left.VALUE == 42
"""
    process = subprocess.run([sys.executable, "-c", code, str(manifest)], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("child", [False, True], ids=["entry", "submodule"])
@pytest.mark.parametrize("scanner", [False, True], ids=["runtime", "metadata"])
def test_development_imports_ignore_same_second_same_size_pyc(tmp_path: Path, child: bool, scanner: bool):
    directory = tmp_path / "中文 source" / "dev_probe"
    directory.mkdir(parents=True)
    manifest = directory / "plugin.toml"
    manifest.write_text("", encoding="utf-8")
    prefix = "from plugin.sdk.plugin.decorators import plugin_entry\n"
    suffix = "class Probe:\n    @plugin_entry(id='probe', name=VALUE)\n    def probe(self): pass\n"
    if child:
        (directory / "__init__.py").write_text(prefix + "from .child import VALUE\n" + suffix, encoding="utf-8")
        _stale_source(directory / "child.py", 'VALUE="old"\n', 'VALUE="new"\n')
    else:
        _stale_source(directory / "__init__.py", prefix + 'VALUE="old"\n' + suffix, prefix + 'VALUE="new"\n' + suffix)
    cache_before = {p: p.read_bytes() for p in directory.rglob("*.pyc")}
    if scanner:
        from plugin.server.application.plugins.metadata_scanner import scan_plugin_metadata_isolated
        result = scan_plugin_metadata_isolated(
            plugin_id="dev_probe", module_path="plugins.dev_probe", class_name="Probe",
            config_path=manifest, conf={}, pdata={}, source_only=True,
        )
        assert any(entry.get("name") == "new" for entry in result.entries_preview)
    else:
        code = """
import json, sys
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.logging_config import get_logger
module = _import_plugin_module('plugins.dev_probe', Path(sys.argv[1]), get_logger('probe'), source_only=sys.argv[2]=='true')
print('RESULT:' + json.dumps(module.VALUE))
"""
        # Counterexample: normal Python still sees the valid, stale timestamp cache.
        for mode, expected in [("false", "old"), ("true", "new")]:
            process = subprocess.run([sys.executable, "-c", code, str(manifest), mode], capture_output=True, text=True, timeout=30)
            assert process.returncode == 0, process.stderr
            result_line = next(line for line in process.stdout.splitlines() if line.startswith("RESULT:"))
            assert json.loads(result_line[7:]) == expected
    assert all(path.read_bytes() == content for path, content in cache_before.items())


def test_source_finder_does_not_change_dependencies_or_other_plugins(tmp_path: Path):
    from plugin.core.source_imports import PluginSourceFinder, SourceOnlyLoader
    directory = tmp_path / "probe"
    directory.mkdir()
    (directory / "lazy.py").write_text("VALUE = 1\n", encoding="utf-8")
    finder = PluginSourceFinder("plugins.probe", directory)
    assert finder.find_spec("json") is None
    assert finder.find_spec("plugins.other.module") is None
    assert finder.find_spec("plugins.probe.vendor.lib") is None
    assert finder.find_spec("plugin.plugins.probe.vendor.lib") is None
    spec = finder.find_spec("plugins.probe.lazy")
    assert isinstance(spec.loader, SourceOnlyLoader)
    namespace = {}
    exec(spec.loader.get_code("plugins.probe.lazy"), namespace)
    assert namespace["VALUE"] == 1
    namespace_dir = directory / "nested"
    namespace_dir.mkdir()
    assert finder.find_spec("plugins.probe.nested").submodule_search_locations == [str(namespace_dir)]
    with pytest.raises(ModuleNotFoundError):
        finder.find_spec("plugins.probe.deleted")


def test_development_imports_keep_alias_and_lazy_imports_source_only(tmp_path: Path):
    directory = tmp_path / "alias_probe"
    directory.mkdir()
    manifest = directory / "plugin.toml"
    manifest.write_text("", encoding="utf-8")
    (directory / "__init__.py").write_text(
        "from plugin.plugins.alias_probe.child import VALUE\n"
        "def lazy():\n    from .late import VALUE\n    return VALUE\n", encoding="utf-8",
    )
    _stale_source(directory / "child.py", 'VALUE="old"\n', 'VALUE="new"\n')
    _stale_source(directory / "late.py", 'VALUE="old"\n', 'VALUE="new"\n')
    code = """
import sys
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.core.source_imports import PluginSourceFinder
from plugin.logging_config import get_logger
for attempt in range(3):
    module = _import_plugin_module('plugins.alias_probe', Path(sys.argv[1]), get_logger('probe'), source_only=True)
    assert module is sys.modules['plugin.plugins.alias_probe']
    assert module.VALUE == 'new'
    assert module.lazy() == 'new'
assert sum(isinstance(f, PluginSourceFinder) for f in sys.meta_path) == 1
"""
    process = subprocess.run([sys.executable, "-c", code, str(manifest)], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr


def test_host_passes_source_policy_to_child_without_changing_default(monkeypatch, tmp_path: Path):
    from plugin.core import host
    captured = []
    monkeypatch.setattr(host, "state", SimpleNamespace(plugin_response_map={}, plugin_response_notify_event=object()))
    monkeypatch.setattr(host, "HostTransport", lambda: SimpleNamespace(downlink_endpoint="down", uplink_endpoint="up"))
    monkeypatch.setattr(host, "PluginCommunicationResourceManager", lambda **kwargs: None)
    monkeypatch.setattr(host.multiprocessing, "Event", lambda: None)
    monkeypatch.setattr(host.multiprocessing, "Process", lambda **kwargs: captured.append(kwargs))
    for enabled in (False, True):
        host.PluginHost("probe", "plugins.probe:Probe", tmp_path / "plugin.toml", source_only=enabled)
    assert "source_only" not in captured[0]["args"][7]
    assert captured[1]["args"][7]["source_only"] is True


def test_development_lazy_alias_imports_share_concurrent_initialization(tmp_path: Path):
    directory = tmp_path / "parallel_probe"
    directory.mkdir()
    manifest = directory / "plugin.toml"
    manifest.write_text("", encoding="utf-8")
    (directory / "__init__.py").write_text("loads = []\n", encoding="utf-8")
    (directory / "child.py").write_text(
        "import time\nfrom plugins.parallel_probe import loads\n"
        "loads.append(__name__)\ntime.sleep(0.1)\nVALUE = object()\n", encoding="utf-8",
    )
    code = """
import importlib, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.logging_config import get_logger
root = _import_plugin_module('plugins.parallel_probe', Path(sys.argv[1]), get_logger('probe'), source_only=True)
barrier = threading.Barrier(2)
def load(name):
    barrier.wait(timeout=5)
    return importlib.import_module(name)
with ThreadPoolExecutor(max_workers=2) as executor:
    modules = list(executor.map(load, ['plugins.parallel_probe.child', 'plugin.plugins.parallel_probe.child']))
assert modules[0] is modules[1]
assert len(root.loads) == 1
assert modules[0].__spec__.name == 'plugins.parallel_probe.child'
"""
    process = subprocess.run([sys.executable, "-c", code, str(manifest)], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("legacy_first", [False, True])
def test_development_failed_lazy_alias_import_can_retry(tmp_path: Path, legacy_first: bool):
    directory = tmp_path / "retry_probe"
    directory.mkdir()
    manifest = directory / "plugin.toml"
    manifest.write_text("", encoding="utf-8")
    (directory / "__init__.py").write_text("", encoding="utf-8")
    (directory / "child.py").write_text("raise RuntimeError('broken source')\n", encoding="utf-8")
    code = """
import importlib, sys
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.logging_config import get_logger
manifest = Path(sys.argv[1])
_import_plugin_module('plugins.retry_probe', manifest, get_logger('probe'), source_only=True)
names = ['plugins.retry_probe.child', 'plugin.plugins.retry_probe.child']
if sys.argv[2] == 'True':
    names.reverse()
try:
    importlib.import_module(names[0])
except RuntimeError as exc:
    assert str(exc) == 'broken source'
else:
    raise AssertionError('broken source must fail')
assert all(name not in sys.modules for name in names)
(manifest.parent / 'child.py').write_text('VALUE = object()\\n', encoding='utf-8')
left = importlib.import_module(names[1])
right = importlib.import_module(names[0])
assert left is right
assert left.__spec__.name == 'plugins.retry_probe.child'
assert importlib.reload(right) is left
assert left.__spec__.name == 'plugins.retry_probe.child'
"""
    process = subprocess.run([sys.executable, "-c", code, str(manifest), str(legacy_first)], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("legacy_first", [False, True])
@pytest.mark.parametrize("namespace", [False, True])
@pytest.mark.parametrize("scanner", [False, True], ids=["runtime", "metadata"])
def test_development_submodule_aliases_share_identity(tmp_path: Path, legacy_first: bool, namespace: bool, scanner: bool):
    directory = tmp_path / "mixed_probe"
    group = directory / "group"
    group.mkdir(parents=True)
    manifest = directory / "plugin.toml"
    manifest.write_text("", encoding="utf-8")
    if not namespace:
        (group / "__init__.py").write_text("", encoding="utf-8")
    first, second = ("plugin.plugins", "plugins") if legacy_first else ("plugins", "plugin.plugins")
    (directory / "__init__.py").write_text(
        "loads = []\n"
        f"from {first}.mixed_probe.group import child as first\n"
        f"from {second}.mixed_probe.group import child as second\n"
        "assert first is second, 'submodule loaded twice'\n"
        "assert len(loads) == 1, loads\n"
        "from plugin.sdk.plugin.decorators import plugin_entry\n"
        "class Probe:\n"
        "    @plugin_entry(id='probe', name=first.VALUE)\n"
        "    def probe(self): pass\n", encoding="utf-8",
    )
    child_prefix = "from plugins.mixed_probe import loads\nloads.append(__name__)\nSTATE = []\n"
    _stale_source(group / "child.py", child_prefix + 'VALUE="old"\n', child_prefix + 'VALUE="new"\n')
    if scanner:
        from plugin.server.application.plugins.metadata_scanner import scan_plugin_metadata_isolated
        result = scan_plugin_metadata_isolated(
            plugin_id="mixed_probe", module_path="plugins.mixed_probe", class_name="Probe",
            config_path=manifest, conf={}, pdata={}, source_only=True,
        )
        assert any(entry.get("name") == "new" for entry in result.entries_preview)
    else:
        code = """
import importlib, sys
from pathlib import Path
from plugin.core.host import _import_plugin_module
from plugin.logging_config import get_logger
previous = None
for attempt in range(2):
    root = _import_plugin_module('plugins.mixed_probe', Path(sys.argv[1]), get_logger('probe'), source_only=True)
    left = importlib.import_module('plugins.mixed_probe.group.child')
    right = importlib.import_module('plugin.plugins.mixed_probe.group.child')
    assert left is right and left is not previous
    assert left.__spec__.name == 'plugins.mixed_probe.group.child'
    assert left.__package__ == 'plugins.mixed_probe.group'
    left.STATE.append(attempt)
    assert right.STATE == [attempt]
    assert left.VALUE == 'new'
    canonical_group = importlib.import_module('plugins.mixed_probe.group')
    legacy_group = importlib.import_module('plugin.plugins.mixed_probe.group')
    assert canonical_group is legacy_group
    assert canonical_group.child is legacy_group.child is left
    assert len(root.loads) == 1
    previous = left
"""
        process = subprocess.run([sys.executable, "-c", code, str(manifest)], capture_output=True, text=True, timeout=30)
        assert process.returncode == 0, process.stderr
