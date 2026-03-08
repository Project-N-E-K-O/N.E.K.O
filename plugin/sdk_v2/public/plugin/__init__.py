"""SDK v2 plugin flavor namespace.

This layer is the primary entry for standard plugin development.
It re-exports stable primitives from core/runtime/storage modules.
"""

from . import base as _base
from . import decorators as _decorators
from . import runtime as _runtime

_exports: list[str] = []
for _module in (_base, _decorators, _runtime):
    for _export_name in _module.__all__:
        globals()[_export_name] = getattr(_module, _export_name)
        if _export_name not in _exports:
            _exports.append(_export_name)

__all__ = _exports
