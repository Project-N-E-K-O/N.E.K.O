from __future__ import annotations

import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
from types import SimpleNamespace

import pytest


def _stale_source(path: Path, old: str, new: str) -> None:
    assert len(old) == len(new)
    path.write_text(old, encoding="utf-8")
    os.utime(path, (1700000000.1, 1700000000.1))
    py_compile.compile(str(path), doraise=True)
    path.write_text(new, encoding="utf-8")
    os.utime(path, (1700000000.8, 1700000000.8))


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
    for name in ("plugins.probe.lazy", "plugin.plugins.probe.lazy"):
        spec = finder.find_spec(name)
        assert isinstance(spec.loader, SourceOnlyLoader)
        namespace = {}
        exec(spec.loader.get_code(name), namespace)
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
