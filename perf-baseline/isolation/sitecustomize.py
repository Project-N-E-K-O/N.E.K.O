"""Data-isolation guard for perf-baseline runs (loaded via PYTHONPATH in every child process).

Active only when NEKO_PERF_ISOLATED_ROOT is set. It makes sure no N.E.K.O process
started by the benchmark can read or write the real user data:

* ConfigManager's standard / legacy / Documents candidates are replaced with the
  isolated root (the same technique tests/real_root_isolation.py uses). On Windows the
  legacy lookup otherwise reads the real Documents folder through SHGetFolderPathW,
  which ignores environment overrides.
* utils.logging_layout.documents_directory() points into the isolated root.
* With NEKO_PERF_NO_BUILTIN=1 the built-in plugin root is redirected to an empty
  directory (the "0 plugins" tier) without touching the repository.

Nothing here changes behaviour that is being measured, apart from the plugin root in
the 0-plugin tier.
"""
from __future__ import annotations

import importlib.abc
import os
import sys
from pathlib import Path

_ROOT = os.environ.get("NEKO_PERF_ISOLATED_ROOT", "").strip()


def _patch_config_manager(module) -> None:
    cls = getattr(module, "ConfigManager", None)
    if cls is None:
        return
    root = Path(_ROOT) / "appdata"
    root.mkdir(parents=True, exist_ok=True)
    cls._get_standard_data_directory_candidates = lambda self: [root]
    cls._get_legacy_storage_candidates = lambda self: []
    cls._get_legacy_document_candidates = lambda self: []


def _patch_logging_layout(module) -> None:
    docs = Path(_ROOT) / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    module.documents_directory = lambda: docs


def _patch_settings(module) -> None:
    if os.environ.get("NEKO_PERF_NO_BUILTIN") != "1":
        return
    empty = Path(_ROOT) / "empty-builtin-plugins"
    empty.mkdir(parents=True, exist_ok=True)
    module.get_builtin_plugin_config_root = lambda: empty.resolve()
    module.BUILTIN_PLUGIN_CONFIG_ROOT = empty.resolve()
    module.PLUGIN_CONFIG_ROOTS = module.get_plugin_config_roots(state_root=module.PLUGIN_STATE_ROOT)


_PATCHES = {
    "utils.config_manager": _patch_config_manager,
    "utils.logging_layout": _patch_logging_layout,
    "plugin.settings": _patch_settings,
}


class _Loader(importlib.abc.Loader):
    def __init__(self, inner, name):
        self.inner, self.name = inner, name

    def create_module(self, spec):
        return self.inner.create_module(spec)

    def exec_module(self, module):
        self.inner.exec_module(module)
        _PATCHES[self.name](module)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name not in _PATCHES:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            find = getattr(finder, "find_spec", None)
            spec = find(name, path, target) if find else None
            if spec is not None:
                if spec.loader is not None:
                    spec.loader = _Loader(spec.loader, name)
                return spec
        return None


if _ROOT:
    sys.meta_path.insert(0, _Finder())
    os.environ.setdefault("NEKO_PERF_ISOLATION_ACTIVE", "1")
