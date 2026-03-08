"""SDK v2 root namespace.

Primary developer-facing surfaces:
- direct exports from `plugin`
- adapter/extension exports as supplementary capabilities
- module namespaces: `plugin`, `extension`, `adapter`, `shared`
"""

from __future__ import annotations

from importlib import import_module

from . import shared

_adapter_mod = import_module(".adapter", __name__)
_extension_mod = import_module(".extension", __name__)
_plugin_mod = import_module(".plugin", __name__)

# Keep module namespaces as first-class exports.
adapter = _adapter_mod
extension = _extension_mod
plugin = _plugin_mod

for _name in _plugin_mod.__all__:
    if _name == "plugin":
        continue
    globals()[_name] = getattr(_plugin_mod, _name)

for _name in _adapter_mod.__all__:
    if _name not in globals():
        globals()[_name] = getattr(_adapter_mod, _name)

for _name in _extension_mod.__all__:
    if _name not in globals():
        globals()[_name] = getattr(_extension_mod, _name)

_plugin_exports = [name for name in _plugin_mod.__all__ if name != "plugin"]
_adapter_only = [name for name in _adapter_mod.__all__ if name not in _plugin_exports]
_extension_only = [
    name
    for name in _extension_mod.__all__
    if name not in _plugin_exports and name not in _adapter_mod.__all__
]

__all__ = [
    "plugin",
    "extension",
    "adapter",
    "shared",
    *_plugin_exports,
    *_adapter_only,
    *_extension_only,
]


del _name
