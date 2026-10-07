"""Opt-in source imports confined to one plugin package in its child process."""
from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
from pathlib import Path
import sys


class SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Compile the selected source without reading or writing bytecode caches."""

    def get_code(self, fullname: str):
        source_path = self.get_filename(fullname)
        return self.source_to_code(self.get_data(source_path), source_path)


class _PluginAliasLoader(importlib.abc.Loader):
    """Resolve a legacy import through the canonical module's import lock/cache."""

    def __init__(self, canonical: str):
        self.canonical = canonical
        self.canonical_spec = None

    def create_module(self, spec):
        module = importlib.import_module(self.canonical)
        self.canonical_spec = module.__spec__
        return module

    def exec_module(self, module):
        # module_from_spec assigns the alias spec even when create_module
        # returns an existing object. Keep reload and relative imports canonical.
        module.__spec__ = self.canonical_spec


class PluginSourceFinder(importlib.abc.MetaPathFinder):
    def __init__(self, package: str, directory: Path):
        self.package = package
        self.directory = directory.resolve()
        self.prefixes = (package, f"plugin.{package}")

    def find_spec(self, fullname, path=None, target=None):
        prefix = next((p for p in self.prefixes if fullname == p or fullname.startswith(p + ".")), None)
        if prefix is None:
            return None
        relative = fullname[len(prefix):].lstrip(".").split(".") if fullname != prefix else []
        # Dependencies retain their ordinary import policy, including vendor/.
        if relative and relative[0] == "vendor":
            return None
        candidate = self.directory.joinpath(*relative)
        if prefix != self.package:
            canonical = self.package + fullname[len(prefix):]
            return importlib.util.spec_from_loader(
                fullname, _PluginAliasLoader(canonical), is_package=candidate.is_dir(),
            )
        source = candidate / "__init__.py" if candidate.is_dir() else candidate.with_suffix(".py")
        if not source.is_file():
            # Preserve native modules/packages without falling through to stale
            # bytecode or a different plugin directory on the import path.
            native_spec = importlib.machinery.FileFinder(
                str(candidate.parent),
                (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES),
            ).find_spec(fullname)
            if native_spec is not None and native_spec.loader is not None:
                try:
                    Path(native_spec.origin).resolve().relative_to(self.directory)
                except ValueError:
                    raise ImportError(f"Plugin source escapes its registered directory: {native_spec.origin}") from None
                return native_spec
            if candidate.is_dir():
                spec = importlib.machinery.ModuleSpec(fullname, None, is_package=True)
                spec.submodule_search_locations = [str(candidate)]
                return spec
            raise ModuleNotFoundError(f"No source module named '{fullname}'", name=fullname)
        try:
            source.resolve().relative_to(self.directory)
        except ValueError:
            raise ImportError(f"Plugin source escapes its registered directory: {source}") from None
        return importlib.util.spec_from_file_location(
            fullname, source, loader=SourceOnlyLoader(fullname, str(source)),
            submodule_search_locations=[str(candidate)] if candidate.is_dir() else None,
        )


def install_source_imports(package: str, directory: Path) -> None:
    """Keep the finder for lazy imports; replace it when this package is reloaded."""
    sys.meta_path[:] = [
        finder for finder in sys.meta_path
        if not isinstance(finder, PluginSourceFinder) or finder.package != package
    ]
    sys.meta_path.insert(0, PluginSourceFinder(package, directory))
